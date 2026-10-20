"""Pass 1 + 2 — model extraction over archived turns, with every claim pinned to a quote.

Safety and honesty rules, all enforced here (not in the prompt):

* **What leaves the machine:** only the prose turns of one session window, secret- AND
  PII-redacted, plus titles of existing records of the same project. Nothing is sent unless
  ``recall.extract.remote`` is on; ``--dry-run`` sends nothing and prints the cost.
* **Quotes are verified:** every quote must be a verbatim substring of the line it cites (as
  sent). An item with no surviving quote never becomes a record — it goes to the review queue.
* **Authority:** ``confirmed`` / ``approved`` / ``rejected`` / ``deferred`` stick only when a
  verified quote comes from the operator (typed text, an answer, a plan verdict); otherwise the
  status is capped at ``proposed``/``exploring``/``unresolved``. An "implemented" claim never
  sets an implementation state — that is :mod:`navig.recall.verify`'s job.
* **Reconciliation:** an item merges into an existing record only when the model cites that
  record's id (and the types are compatible) or the canonical key matches exactly in the same
  project; a near-duplicate is created AND queued for review — never merged silently.
* **Budget & cache:** every call is keyed by (source, window hash, extractor version, model);
  a window already extracted is never sent again. A run stops before a call that would exceed
  the per-run budget or the daily cap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from navig.recall.model import USER_SPEAKERS, Evidence, Record, canonical_key, record_id
from navig.recall.redact import redact_pii
from navig.store.base import _utcnow

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "x1"
SPEAKER_LABEL = {
    "user_typed": "USER", "user_answer": "USER_ANSWER", "user_plan_verdict": "USER_VERDICT",
    "user_pasted": "PASTED", "assistant": "ASSISTANT", "agent_report": "AGENT",
}
ITEM_TYPES = {"idea", "decision", "term", "plan", "question", "finding", "risk", "architecture", "lore"}
_USER_STATUSES = {"confirmed", "approved", "rejected", "deferred"}
_SOFT_STATUSES = {"proposed", "exploring", "unresolved", "superseded"}
_CONCEPT_FAMILY = {"idea", "plan", "architecture", "feature"}
MAX_TURN_CHARS = 6_000
WINDOW_CHARS = 160_000
OVERLAP_TURNS = 2
LIVE_SECONDS = 15 * 60
_OUTPUT_TOKENS_EST = 2_500
_MIN_QUOTE = 8
_MAX_CONSECUTIVE_FAILURES = 8
_CUES = re.compile(
    r"\b(we could|could we|what if|maybe later|later we|follow[- ]up|instead of|rather than|rename|call it|"
    r"hidden rock|rejected|let'?s not|out of scope|idea|todo|next step)\b|"
    r"(можно|лучше|вместо|давай|идея|назов)|"
    r"\b(on pourrait|plutôt|au lieu|idée|appelons)\b",
    re.I,
)


# ── Windows ──────────────────────────────────────────────────


@dataclass
class Window:
    text: str
    labels: dict[str, dict[str, Any]]  # "m12" → {msg_id, speaker, ts, sent}
    chunk_hash: str


def _line(turn: dict[str, Any]) -> tuple[str, str]:
    sent = redact_pii(turn["text"])
    if len(sent) > MAX_TURN_CHARS:
        sent = sent[:MAX_TURN_CHARS] + " …[cut]"
    label = f"m{turn['seq']}"
    return label, f"[{label}] {SPEAKER_LABEL[turn['speaker']]}: {sent}"


def build_windows(turns: list[dict[str, Any]], *, window_chars: int = WINDOW_CHARS) -> list[Window]:
    """Prose turns → windows of at most *window_chars*, overlapping by a couple of turns."""
    prose = [t for t in turns if t["speaker"] in SPEAKER_LABEL and (t.get("text") or "").strip()]
    lines = [(t, *_line(t)) for t in prose]
    windows: list[Window] = []
    start = 0
    while start < len(lines):
        size, end = 0, start
        while end < len(lines) and (size + len(lines[end][2]) <= window_chars or end == start):
            size += len(lines[end][2]) + 1
            end += 1
        chunk = lines[start:end]
        text = "\n".join(ln for _, _, ln in chunk)
        labels = {lab: {"msg_id": t["msg_id"], "speaker": t["speaker"], "ts": t.get("ts", ""),
                        "sent": ln.split(": ", 1)[1] if ": " in ln else ln} for t, lab, ln in chunk}
        windows.append(Window(text=text, labels=labels,
                              chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]))
        if end >= len(lines):
            break
        start = max(start + 1, end - OVERLAP_TURNS)
    return windows


# ── Validation & rules ───────────────────────────────────────

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "«": '"', "»": '"'})


def _norm(s: str) -> str:
    s = _WS.sub(" ", str(s).translate(_QUOTES)).strip()
    return s.strip("…").removesuffix("...").removeprefix("...").strip()


def verify_quotes(item: dict[str, Any], labels: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """(verified quotes with their source line, number rejected)."""
    good, bad = [], 0
    for q in item.get("quotes") or []:
        if not isinstance(q, dict):
            bad += 1
            continue
        lab = str(q.get("msg") or "").strip().strip("[]")
        text = _norm(q.get("text") or "")
        line = labels.get(lab)
        if line is None or len(text) < _MIN_QUOTE or text.casefold() not in _norm(line["sent"]).casefold():
            bad += 1
            continue
        good.append({**line, "quote": text})
    return good, bad


def apply_rules(item: dict[str, Any], quotes: list[dict[str, Any]]) -> tuple[str, str, str]:
    """(status, status_basis, endorsement) from the item and its VERIFIED quotes."""
    status = str(item.get("status") or "proposed").strip().lower()
    user_quote = any(q["speaker"] in USER_SPEAKERS for q in quotes)
    if status in _USER_STATUSES and str(item.get("decided_by")).lower() == "user" and user_quote:
        endorsement = "user_rejected" if status == "rejected" else "user_endorsed"
        return status, "user", endorsement
    if status in _SOFT_STATUSES:
        return status, "assistant", "none"
    if status in ("confirmed", "approved"):
        return "exploring", "assistant", "none"  # an assistant conclusion, not a decision
    return "proposed", "assistant", "none"


def item_to_record(item: dict[str, Any], quotes: list[dict[str, Any]], source_id: str, project: str,
                   model: str) -> Record:
    rtype = str(item.get("type") or "idea").lower()
    rtype = rtype if rtype in ITEM_TYPES else "idea"
    title = " ".join(str(item.get("title") or "").split())[:200] or quotes[0]["quote"][:120]
    status, basis, endorsement = apply_rules(item, quotes)
    key = canonical_key(title)
    rejected = [r for r in item.get("rejected_names") or [] if isinstance(r, dict) and r.get("name")]
    body = ""
    if rejected:
        body = "**Rejected names:**\n" + "\n".join(
            f"- {r['name']}" + (f" — {r.get('reason')}" if r.get("reason") else "") for r in rejected)
    ts = max((q["ts"] for q in quotes if q.get("ts")), default="")
    return Record(
        id=record_id("x", source_id, rtype, key), type=rtype, title=title,
        summary=" ".join(str(item.get("summary") or "").split())[:1200], body=body, project=project,
        status=status, status_basis=basis, endorsement=endorsement,
        impl="discussed_unverified" if rtype in _CONCEPT_FAMILY else "unknown",
        confidence=round(min(0.9, 0.5 + 0.15 * len(quotes)), 2), canonical_key=key,
        aliases=[str(a) for a in item.get("aliases") or [] if str(a).strip()][:10],
        tags=sorted({*(str(t).lower() for t in item.get("tags") or [] if str(t).strip()), "extracted"})[:8],
        created=ts, updated=ts,
        attrs={"decided_by": str(item.get("decided_by") or "none"), "rejected_names": rejected,
               "extractor": EXTRACTOR_VERSION, "model": model},
        evidence=[Evidence(source_id, q["msg_id"], q["speaker"], q["quote"], q.get("ts", "")) for q in quotes],
    )


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"\w{3,}", s.casefold())}


def reconcile(store: Any, rec: Record, refines: str | None) -> tuple[str | None, str | None]:
    """(id to merge into, id to queue as a possible duplicate)."""
    if refines and refines.startswith("rk-") and refines != rec.id:
        target = store.get_record(refines)
        if target and target.project == rec.project and (
                target.type == rec.type or {target.type, rec.type} <= _CONCEPT_FAMILY):
            return target.id, None
    same = store.find_by_key(rec.project, rec.type, rec.canonical_key)
    if same and same != rec.id:
        return same, None
    for hit in store.search_records(rec.title, project=rec.project, rtype=rec.type, limit=5, any_term=True):
        if hit["id"] == rec.id:
            continue
        a, b = _words(rec.title), _words(hit["title"])
        if a and b and len(a & b) / len(a | b) >= 0.5:
            return None, hit["id"]
    return None, None


# ── Runner ───────────────────────────────────────────────────


@dataclass
class ExtractOptions:
    provider: str = "mimo"
    model: str = "mimo-v2.6-flash"
    budget_usd: float = 5.0
    day_cap_usd: float = 20.0
    focus: tuple[str, ...] = ("all",)
    recovery: bool = True
    dry_run: bool = False
    project: str | None = None
    source_ids: tuple[str, ...] = ()
    max_sessions: int | None = None
    window_chars: int = WINDOW_CHARS
    workers: int = 6


@dataclass
class ExtractReport:
    sessions: int = 0
    windows: int = 0
    calls: int = 0
    cached: int = 0
    items: int = 0
    records_created: int = 0
    records_updated: int = 0
    valid_quotes: int = 0
    rejected_quotes: int = 0
    unquoted_items: int = 0
    review_added: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    est_prompt_tokens: int = 0
    est_cost_usd: float = 0.0
    parse_errors: int = 0
    consecutive_failures: int = 0
    stopped: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["cost_usd"] = round(self.cost_usd, 4)
        d["est_cost_usd"] = round(self.est_cost_usd, 4)
        return d


def model_price(provider: str, model: str) -> tuple[float, float]:
    """USD per 1M (input, output) tokens from the provider registry; (0, 0) when unknown."""
    try:
        from navig.providers import get_builtin_provider  # noqa: PLC0415

        cfg = get_builtin_provider(provider)
        for m in (cfg.models if cfg else []):
            if m.id == model and m.cost:
                return float(m.cost.input), float(m.cost.output)
    except Exception:  # noqa: BLE001
        logger.debug("recall: no price for %s:%s", provider, model)
    return 0.0, 0.0


def _today_start() -> str:
    """Start of the operator's LOCAL day, in the stores' UTC timestamp format — the daily cap
    resets at local midnight, not at UTC midnight."""
    from navig.store.base import _to_utc_iso  # noqa: PLC0415

    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return _to_utc_iso(local_midnight)


def _is_live(src: dict[str, Any]) -> bool:
    ts = src.get("last_ts") or ""
    try:
        last = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < LIVE_SECONDS


LLMCall = Callable[[str], Any]


def default_llm(opts: ExtractOptions) -> LLMCall:
    from navig.agent.recall_extractor import extract_items  # noqa: PLC0415

    return lambda prompt: extract_items(prompt, provider=opts.provider, model=opts.model)


def run_extraction(ingestor: Any, opts: ExtractOptions, *, llm: LLMCall | None = None,
                   progress: Callable[[int, int, str], None] | None = None) -> ExtractReport:
    """Extract records from archived sessions. Idempotent and resumable (window cache)."""
    from navig.agent.recall_extractor import PROMPT_VERSION  # noqa: PLC0415

    store = ingestor.store
    rep = ExtractReport()
    price_in, price_out = model_price(opts.provider, opts.model)
    if not opts.dry_run and llm is None:
        llm = default_llm(opts)
    sources = [s for s in store.list_sources()
               if s["adapter"] in ("claude_code", "codex")
               and (not opts.project or s["project"] == opts.project)
               and (not opts.source_ids or s["source_id"] in opts.source_ids)]
    if opts.max_sessions:
        sources = sources[:opts.max_sessions]
    day_spent = store.spend_since(_today_start())
    version = f"{PROMPT_VERSION}/{EXTRACTOR_VERSION}"

    jobs: list[_Job] = []
    turns_by_source: dict[str, list[dict[str, Any]]] = {}
    for n, src in enumerate(sources, 1):
        if progress:
            progress(n, len(sources), src["source_id"])
        if _is_live(src):
            continue  # a session still being written is extracted once it goes idle
        turns = store.source_turns(src["source_id"])
        windows = build_windows(turns, window_chars=opts.window_chars)
        if not windows:
            continue
        rep.sessions += 1
        turns_by_source[src["source_id"]] = turns
        jobs += [_Job(src, w, f, version) for w in windows for f in opts.focus]
    _run_jobs(ingestor, jobs, opts, llm, rep, (price_in, price_out), day_spent)
    if opts.recovery and not rep.stopped:
        by_id = {s["source_id"]: s for s in sources}
        rec_jobs = []
        for sid, turns in turns_by_source.items():
            win = _recovery_window(turns, store.covered_msg_ids(sid))
            if win is not None:
                rec_jobs.append(_Job(by_id[sid], win, "all", version + "/recovery"))
        _run_jobs(ingestor, rec_jobs, opts, llm, rep, (price_in, price_out), day_spent)
    return rep


@dataclass
class _Job:
    src: dict[str, Any]
    win: Window
    focus: str
    version: str
    key: str = ""
    prompt: str = ""
    est_cost: float = 0.0


def _recovery_window(turns: list[dict[str, Any]], cited: set[str]) -> Window | None:
    """Turns with idea/decision cues that no record cites yet — a second, targeted look."""
    picks = [t for t in turns if t["speaker"] in ("user_typed", "assistant") and t["msg_id"] not in cited
             and _CUES.search(t.get("text") or "")]
    if not picks:
        return None
    wins = build_windows(picks[:30], window_chars=60_000)
    return wins[0] if wins else None


def _prepare(store: Any, job: _Job, opts: ExtractOptions, price: tuple[float, float], rep: ExtractReport) -> bool:
    """Key, cache check, prompt and cost estimate. False when the window needs no call."""
    from navig.agent.recall_extractor import build_prompt  # noqa: PLC0415

    job.key = hashlib.sha256(
        f"{job.src['source_id']}|{job.win.chunk_hash}|{job.focus}|{job.version}|{opts.model}".encode()).hexdigest()[:24]
    rep.windows += 1
    if store.run_status(job.key) == "ok":
        rep.cached += 1
        return False
    brief = store.project_brief(job.src["project"])
    existing = "\n".join(f"- {b['id']} [{b['type']}] {b['title']}"
                         + (f" (aka {', '.join(b['aliases'][:3])})" if b["aliases"] else "") for b in brief)
    job.prompt = build_prompt(job.win.text, existing, job.focus)
    est_in = int(len(job.prompt) / 3.5)
    job.est_cost = (est_in * price[0] + _OUTPUT_TOKENS_EST * price[1]) / 1_000_000
    rep.est_prompt_tokens += est_in
    rep.est_cost_usd += job.est_cost
    return not opts.dry_run


def _run_jobs(ingestor: Any, jobs: list[_Job], opts: ExtractOptions, llm: LLMCall | None, rep: ExtractReport,
              price: tuple[float, float], day_spent: float) -> None:
    """Model calls on a bounded thread pool; everything that touches the bank stays on this thread."""
    from concurrent.futures import (  # noqa: PLC0415
        FIRST_COMPLETED,
        Future,
        ThreadPoolExecutor,
        wait,
    )

    from navig.agent.recall_extractor import ExtractorUnavailable  # noqa: PLC0415

    store = ingestor.store
    pending = iter(jobs)
    inflight: dict[Future, _Job] = {}
    reserved = 0.0
    pool = ThreadPoolExecutor(max_workers=max(1, opts.workers), thread_name_prefix="recall-extract")
    try:
        while True:
            while not rep.stopped and len(inflight) < max(1, opts.workers):
                job = next(pending, None)
                if job is None:
                    break
                if not _prepare(store, job, opts, price, rep):
                    continue
                if rep.cost_usd + reserved + job.est_cost > opts.budget_usd:
                    rep.stopped = f"run budget ${opts.budget_usd:.2f} reached"
                    break
                if day_spent + rep.cost_usd + reserved + job.est_cost > opts.day_cap_usd:
                    rep.stopped = f"daily cap ${opts.day_cap_usd:.2f} reached"
                    break
                reserved += job.est_cost
                inflight[pool.submit(llm, job.prompt)] = job  # type: ignore[arg-type]
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                job = inflight.pop(fut)
                reserved -= job.est_cost
                try:
                    res = fut.result()
                except ExtractorUnavailable as exc:
                    rep.stopped = rep.stopped or f"extractor unavailable: {exc}"[:300]
                    continue
                except Exception as exc:  # noqa: BLE001 — one bad call must not lose the others
                    rep.errors.append(f"{job.src['source_id']}: {type(exc).__name__}: {exc}"[:300])
                    continue
                _apply(ingestor, job, res, opts, rep, price)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _apply(ingestor: Any, job: _Job, res: Any, opts: ExtractOptions, rep: ExtractReport,
           price: tuple[float, float]) -> None:
    from navig.recall.ingest import IngestReport  # noqa: PLC0415
    from navig.recall.vault import make_event  # noqa: PLC0415

    store = ingestor.store
    src, win = job.src, job.win
    rep.calls += 1
    cost = (res.prompt_tokens * price[0] + res.completion_tokens * price[1]) / 1_000_000
    rep.cost_usd += cost
    rep.prompt_tokens += res.prompt_tokens
    rep.completion_tokens += res.completion_tokens
    run = {"key": job.key, "source_id": src["source_id"], "chunk_hash": win.chunk_hash,
           "extractor_version": job.version, "model": f"{opts.provider}:{opts.model}",
           "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
           "cost_usd": round(cost, 6), "created": _utcnow()}
    if res.parse_error:
        rep.parse_errors += 1
        rep.consecutive_failures += 1
        store.record_run({**run, "status": "failed", "error": res.parse_error[:300]})
        _persist_run(ingestor, {**run, "status": "failed"})
        if rep.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            rep.stopped = rep.stopped or (f"{rep.consecutive_failures} answers in a row failed "
                                          f"(last: {res.parse_error[:120]})")
        return
    rep.consecutive_failures = 0
    records: list[Record] = []
    extra_events: list[dict[str, Any]] = []
    for item in res.items:
        rep.items += 1
        quotes, bad = verify_quotes(item, win.labels)
        rep.rejected_quotes += bad
        rep.valid_quotes += len(quotes)
        if not quotes:
            rep.unquoted_items += 1
            if store.add_review("unquoted_candidate", record_id("u", src["source_id"], json.dumps(item)[:200]), "",
                                {"source_id": src["source_id"], "item": item}):
                rep.review_added += 1
            continue
        rec = item_to_record(item, quotes, src["source_id"], src["project"], f"{opts.provider}:{opts.model}")
        target, dup = reconcile(store, rec, str(item.get("refines") or "") or None)
        if target:
            extra_events.append(make_event(target, "refined", rec.updated or _utcnow(),
                                           {"from": src["source_id"], "title": rec.title}))
            rec.id = target
        elif dup and store.add_review("possible_duplicate", rec.id, dup, {"title": rec.title}):
            rep.review_added += 1
        records.append(rec)
    sub = IngestReport()
    ingestor.commit(records, sub, extra_events=extra_events)
    rep.records_created += sub.records_created
    rep.records_updated += sub.records_updated
    store.record_run({**run, "status": "ok", "items": len(res.items)})
    _persist_run(ingestor, {**run, "status": "ok", "items": len(res.items)})

def _persist_run(ingestor: Any, run: dict[str, Any]) -> None:
    """Append the run to the vault so a lost index never causes paying twice."""
    try:
        path = ingestor.vault.root / ".extract_runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("recall: could not persist extract run: %s", exc)
