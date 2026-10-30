"""Shared Recall queries — one implementation behind every surface that reads the bank.

``navig recall`` (CLI), the MCP bundle (``navig mcp serve --only recall``, read by Claude
Code) and the deck routes (NAVIG OS → Knowledge → Recall) all call these functions, so a
search, an "asked before?" answer or a view means the same thing everywhere.

Two rules every caller inherits:

* **Citations travel with every result.** A turn carries ``cite`` (source id, message id,
  timestamp, speaker, and whether that speaker is the owner); a record carries its
  ``status`` together with ``status_basis`` — ``user`` is the owner's own decision,
  ``audit`` an imported audit, ``assistant`` model output, ``verify`` a verification step.
* **``for_model=True`` drops what may not leave the machine.** A shared chat whose level is
  below ``agent`` (``sources.chats.may_leave_machine``) is readable locally but must never
  reach a model, so its turns, its excerpts and every record it supports are withheld from
  the MCP surface. Local surfaces (CLI, OS) pass ``for_model=False``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from navig.recall.model import USER_SPEAKERS, Record
from navig.recall.projects import SHARED

if TYPE_CHECKING:
    from navig.recall.store import RecallStore


@dataclass(frozen=True)
class ViewSpec:
    rtype: str
    status: str | None = None
    forgotten: bool = False


#: The named views, shared by the CLI, the MCP tools and the OS rail.
VIEWS: dict[str, ViewSpec] = {
    "forgotten_ideas": ViewSpec("idea", forgotten=True),
    "open_questions": ViewSpec("question", status="unresolved"),
    "decisions": ViewSpec("decision"),
    "plans": ViewSpec("plan"),
    "tables": ViewSpec("table"),
    "findings": ViewSpec("finding"),
    "features": ViewSpec("feature"),
    "ideas": ViewSpec("idea"),
    "questions": ViewSpec("question"),
}

#: impl states that mean "nobody has shown this was built".
UNBUILT_IMPL = ("not_found", "unknown")


@dataclass(frozen=True)
class Scope:
    """Which projects a read covers. ``projects=None`` means every project."""

    projects: tuple[str, ...] | None = None

    @classmethod
    def everything(cls) -> Scope:
        return cls(None)

    @classmethod
    def only(cls, project: str | None) -> Scope:
        """The CLI's ``--project``: exactly that project, or everything when unset."""
        return cls((project,)) if project else cls(None)

    def to_dict(self) -> dict[str, Any]:
        return {"projects": list(self.projects) if self.projects is not None else "all"}


def scope_for(project: str | None = None, *, all_projects: bool = False, cwd: str | None = None) -> Scope:
    """The model-facing default: one project plus ``shared`` (consent tier 4).

    ``project`` names it explicitly; otherwise it is the project *cwd* belongs to.
    ``all_projects`` widens the read to every project and must be asked for.
    """
    if all_projects:
        return Scope.everything()
    pid = (project or "").strip()
    if not pid:
        from navig.recall.projects import project_for_path  # noqa: PLC0415

        pid = project_for_path(cwd).id if cwd else SHARED
    return Scope(tuple(dict.fromkeys((pid, SHARED))))


def _store(store: RecallStore | None) -> RecallStore:
    if store is not None:
        return store
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    return get_recall_store()


def is_enabled() -> bool:
    """``recall.read_local`` — may Recall read local transcripts at all."""
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(get_config_manager().get("recall.read_local"), default=False)


# ── Citations and the model filter ────────────────────────────


def cite(source_id: str, msg_id: str, ts: str = "", speaker: str = "") -> dict[str, Any]:
    return {"source_id": source_id, "msg_id": msg_id, "ts": ts or "", "speaker": speaker,
            "owner": speaker in USER_SPEAKERS, "ref": f"{source_id}#{msg_id}"}


def private_sources(store: RecallStore) -> frozenset[str]:
    """Sources whose text must not be sent to a model (chats shared below ``agent``)."""
    from navig.recall.sources.chats import ADAPTER, may_leave_machine  # noqa: PLC0415

    return frozenset(s["source_id"] for s in store.list_sources(adapter=ADAPTER)
                     if not may_leave_machine(s.get("meta")))


def _drop_private_records(store: RecallStore, rows: list[Any], private: frozenset[str]) -> list[Any]:
    """Withhold every record with ANY evidence from a private source (most private wins)."""
    if not private or not rows:
        return rows
    ids = [r.id if isinstance(r, Record) else r["id"] for r in rows]
    srcs = store.evidence_sources(ids)
    return [r for r, rid in zip(rows, ids, strict=True) if not (srcs.get(rid, set()) & private)]


def _with_cite(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "cite": cite(row["source_id"], row["msg_id"], row.get("ts", ""), row.get("speaker", ""))}


def record_row(rec: Record, *, with_body: bool = False) -> dict[str, Any]:
    """A record as a list row (no evidence; the body only when asked)."""
    d = asdict(rec)
    d.pop("evidence", None)
    if not with_body:
        d.pop("body", None)
    return d


# ── Queries ───────────────────────────────────────────────────


def search(query: str, *, scope: Scope | None = None, rtype: str | None = None, status: str | None = None,
           raw: bool = False, limit: int = 15, for_model: bool = False,
           store: RecallStore | None = None) -> dict[str, Any]:
    """Records matching *query*; the conversation turns too when ``raw`` or nothing matched."""
    st = _store(store)
    projects = (scope or Scope()).projects
    private = private_sources(st) if for_model else frozenset()
    recs = [] if raw else st.search_records(query, rtype=rtype, status=status, limit=limit, projects=projects)
    recs = _drop_private_records(st, recs, private)
    turns: list[dict[str, Any]] = []
    if raw or not recs:
        turns = [_with_cite(t) for t in st.search_turns(query, limit=limit, projects=projects)
                 if t["source_id"] not in private]
    return {"records": recs, "turns": turns}


def ask(question: str, *, scope: Scope | None = None, limit: int = 5, answer_chars: int | None = None,
        for_model: bool = False, store: RecallStore | None = None) -> dict[str, Any]:
    """Have I asked this before? Earlier owner questions, the reply that followed, and decisions."""
    st = _store(store)
    projects = (scope or Scope()).projects
    private = private_sources(st) if for_model else frozenset()
    asked = []
    for h in st.search_turns(question, speakers=["user_typed"], limit=limit, projects=projects):
        if h["source_id"] in private:
            continue
        nxt = st.next_turn(h["source_id"], int(h["seq"]), speaker="assistant")
        if nxt is not None:
            nxt = {**nxt, "cite": cite(h["source_id"], nxt["msg_id"], nxt.get("ts", ""), nxt["speaker"])}
            if answer_chars and len(nxt["text"]) > answer_chars:
                nxt["text"] = nxt["text"][: answer_chars - 1] + "…"
                nxt["truncated"] = True
        asked.append({**_with_cite(h), "answer": nxt})
    decisions = st.search_records(question, rtype="decision", limit=limit, projects=projects)
    return {"asked": asked, "decisions": _drop_private_records(st, decisions, private)}


def view(name: str, *, scope: Scope | None = None, status: str | None = None, limit: int = 30,
         for_model: bool = False, store: RecallStore | None = None) -> list[Record]:
    """One named view (``VIEWS``). ``status`` overrides the view's default status filter."""
    spec = VIEWS.get(name)
    if spec is None:
        raise ValueError(f"unknown view '{name}' (use one of: {', '.join(VIEWS)})")
    st = _store(store)
    projects = (scope or Scope()).projects
    if spec.forgotten:
        recs = st.list_records(rtype=spec.rtype, status=status, limit=limit, projects=projects,
                               impls=UNBUILT_IMPL, exclude_statuses=("rejected",), oldest_first=True)
    else:
        recs = st.list_records(rtype=spec.rtype, status=status or spec.status, limit=limit, projects=projects)
    if for_model:
        recs = _drop_private_records(st, recs, private_sources(st))
    return recs


def resurrect(limit: int = 10, *, scope: Scope | None = None, store: RecallStore | None = None,
              candidates: int = 300) -> list[dict[str, Any]]:
    """Ideas worth reviving, ranked by a deterministic score — the reasons ARE the score.

    Eligible: type ``idea``, not rejected or superseded, ``impl`` in :data:`UNBUILT_IMPL`
    (nobody has shown it was built). Weights:

    * **+40** evidence in the owner's own words (``user_typed`` / ``user_answer`` /
      ``user_plan_verdict``) — the owner engaged with it;
    * **+min(4·evidence, 24)** — how much of the conversation quoted it;
    * **+(11−rank)·4** for an audit recovered-idea rank 1–10 (**+10** when tagged
      ``recovered-idea`` without a rank, **+3** more for audit provenance);
    * **+min(days/15, 15)** staleness — untouched longer reads as more overdue;
    * **+5** the status is still open (proposed / exploring / unresolved).

    Ties break oldest-updated first. Days use the local clock (never a UTC calendar day).
    Nothing here is a model opinion: the same bank always ranks the same way.
    """
    st = _store(store)
    recs = st.list_records(rtype="idea", projects=(scope or Scope()).projects,
                           impls=UNBUILT_IMPL, exclude_statuses=("rejected", "superseded"),
                           oldest_first=True, limit=candidates)
    if not recs:
        return []
    stats = st.evidence_stats([r.id for r in recs])
    now = datetime.now().astimezone()
    rows: list[dict[str, Any]] = []
    for rec in recs:
        count, speakers = stats.get(rec.id, (0, set()))
        score, reasons = 0, []
        owner = bool(speakers & USER_SPEAKERS)
        if owner:
            score += 40
            reasons.append("your own words are quoted in it")
        if count:
            score += min(count * 4, 24)
            reasons.append(f"{count} quoted passage{'s' if count != 1 else ''}")
        tags = set(rec.tags)
        rank = rec.attrs.get("rank")
        try:
            rank_i = int(rank)
        except (TypeError, ValueError):
            rank_i = 0
        if 1 <= rank_i <= 10:
            score += (11 - rank_i) * 4
            reasons.append(f"recovered from the audit (rank {rank_i})")
        elif "recovered-idea" in tags:
            score += 10
            reasons.append("recovered from the audit")
        if "audit" in tags:
            score += 3
        days = 0
        if rec.updated:
            try:
                when = datetime.fromisoformat(rec.updated.replace("Z", "+00:00")).astimezone()
                days = max(0, int((now - when).total_seconds() // 86400))
            except ValueError:
                days = 0
        if days:
            score += min(days // 15, 15)
            reasons.append(f"untouched {days}d")
        if rec.status in ("proposed", "exploring", "unresolved"):
            score += 5
        rows.append({"id": rec.id, "title": rec.title, "project": rec.project, "status": rec.status,
                     "impl": rec.impl, "score": score, "reasons": reasons, "days_stale": days,
                     "evidence": count, "owner": owner, "audit_rank": rank_i or None,
                     "updated": rec.updated})
    rows.sort(key=lambda r: (-r["score"], r["updated"] or "9999"))
    return rows[: max(1, limit)]


def record_detail(record_id: str, *, evidence_limit: int | None = None, for_model: bool = False,
                  store: RecallStore | None = None) -> dict[str, Any] | None:
    """A record with its evidence (each cited), timeline, related records and vault path.

    ``None`` when the record does not exist — or, for a model, when it rests on a source
    that may not leave the machine (indistinguishable on purpose).
    """
    st = _store(store)
    rec = st.get_record(record_id)
    if rec is None:
        return None
    if for_model and {e.source_id for e in rec.evidence} & private_sources(st):
        return None
    evidence = [{**e.to_dict(), "cite": cite(e.source_id, e.msg_id, e.ts, e.speaker),
                 "link": source_link(st, e.source_id, e.msg_id)} for e in rec.evidence]
    total = len(evidence)
    if evidence_limit is not None:
        evidence = evidence[:evidence_limit]
    related = st.related(record_id)
    if for_model and related:
        visible = {r["id"] for r in _drop_private_records(
            st, [{"id": r["other"]} for r in related], private_sources(st))}
        related = [r for r in related if r["other"] in visible]
    return {"record": record_row(rec, with_body=True), "evidence": evidence, "evidence_total": total,
            "events": st.events_for(record_id), "related": related, "path": st.record_path(record_id)}


def excerpt(source_id: str, msg_id: str, *, before: int = 3, after: int = 3, for_model: bool = False,
            store: RecallStore | None = None) -> dict[str, Any] | None:
    """The turns around one cited message. ``None`` when it is missing (or private, for a model)."""
    st = _store(store)
    if for_model and source_id in private_sources(st):
        return None
    rows = st.turn_window(source_id, msg_id, before=max(0, before), after=max(0, after))
    if not rows:
        return None
    src = st.get_source(source_id) or {}
    turns = [{**r, "focus": r["msg_id"] == msg_id, "owner": r["speaker"] in USER_SPEAKERS} for r in rows]
    return {"source": {"source_id": source_id, "adapter": src.get("adapter", ""), "project": src.get("project", ""),
                       "title": src.get("title", ""), "link": source_link(st, source_id, msg_id)},
            "cite": cite(source_id, msg_id, next((r["ts"] for r in rows if r["msg_id"] == msg_id), ""),
                         next((r["speaker"] for r in rows if r["msg_id"] == msg_id), "")),
            "turns": turns}


def source_link(st: RecallStore, source_id: str, msg_id: str) -> str:
    """A link that opens the exact message, for sources that have one (shared web chats)."""
    if not source_id.startswith("chats:"):
        return ""
    src = st.get_source(source_id) or {}
    url = (src.get("meta") or {}).get("url") or ""
    return f"{url}#navig-msg={msg_id}" if url else ""


# ── Provenance views (history/reconcile delegates — one impl behind every surface) ──


def _visible(st: RecallStore, rows: list[dict[str, Any]], private: frozenset[str],
             for_model: bool) -> list[dict[str, Any]]:
    if not for_model or not private:
        return rows
    return _drop_private_records(st, rows, private)


def history(target: str, *, project: str | None = None, keyword: str | None = None,
            root: str | None = None, limit: int = 50, for_model: bool = False,
            resolver: Any = None, store: RecallStore | None = None) -> dict[str, Any]:
    """Sessions, commits and records for one target (see ``navig.recall.history``)."""
    from navig.recall.history import history as _history  # noqa: PLC0415

    st = _store(store)
    out = _history(target, project=project, limit=limit, keyword=keyword, root=root,
                   resolver=resolver, store=st)
    if for_model:
        priv = private_sources(st)
        before = {r["id"] for r in out["records"]}
        out["records"] = _visible(st, out["records"], priv, True)
        after = {r["id"] for r in out["records"]}
        out["timeline"] = [e for e in out["timeline"]
                           if not (e["kind"] == "record" and e["ref"] in before - after)]
    return out


def trace(ref: str, *, project: str | None = None, root: str | None = None, limit: int = 50,
          for_model: bool = False, store: RecallStore | None = None) -> dict[str, Any]:
    """One commit or session → its edges (see ``navig.recall.history.trace``)."""
    from navig.recall.history import trace as _trace  # noqa: PLC0415

    st = _store(store)
    out = _trace(ref, project=project, limit=limit, root=root, store=st)
    if for_model:
        out["records"] = _visible(st, out["records"], private_sources(st), True)
    return out


def changes(project: str | None, *, root: str | None = None, since_days: int = 90,
            limit: int = 50, refresh: bool = False, for_model: bool = False,
            store: RecallStore | None = None) -> dict[str, Any]:
    """Recent commits of a project joined to sessions/records (``navig.recall.history``)."""
    from navig.recall.history import changes as _changes  # noqa: PLC0415

    st = _store(store)
    out = _changes(project, root=root, since_days=since_days, limit=limit, refresh=refresh, store=st)
    if for_model:
        priv = private_sources(st)
        for c in out["commits"]:
            c["records"] = _visible(st, c["records"], priv, True)
    return out


def unfinished(project: str | None = None, *, limit: int = 30, for_model: bool = False,
               store: RecallStore | None = None) -> dict[str, Any]:
    """Plans/tasks nothing verified yet (``navig.recall.history``)."""
    from navig.recall.history import unfinished as _unfinished  # noqa: PLC0415

    st = _store(store)
    out = _unfinished(project, limit=limit, store=st)
    if for_model:
        out["records"] = _visible(st, out["records"], private_sources(st), True)
    return out


def conflicts(project: str | None = None, *, limit: int = 50, reconcile_run: bool = True,
              root: str | None = None, refresh: bool = False, for_model: bool = False,
              store: RecallStore | None = None) -> dict[str, Any]:
    """Recorded contradictions (``navig.recall.reconcile``); ``reconcile_run=False`` only reads."""
    from navig.recall import reconcile  # noqa: PLC0415

    st = _store(store)
    life = None
    if refresh:
        from navig.recall.lifecycle import refresh as refresh_lifecycle  # noqa: PLC0415

        life = refresh_lifecycle(st, project=project, root=root)
    res = reconcile.reconcile(st, project=project) if reconcile_run else {"found": 0, "links": 0, "reviews": 0}
    reviews = reconcile.open_reviews(st, project=project, limit=limit)
    if for_model:
        priv = private_sources(st)
        ids = [i for r in reviews for i in ([r["a"]["id"]] + ([r["b"]["id"]] if r.get("b") else []))]
        visible = {v["id"] for v in _drop_private_records(st, [{"id": i} for i in ids], priv)}
        reviews = [r for r in reviews
                   if r["a"]["id"] in visible and (not r.get("b") or r["b"]["id"] in visible)]
    return {"reconcile": res, "reviews": reviews, "lifecycle": life}


def session_brief(project: str | None, *, budget: int = 1500,
                  store: RecallStore | None = None) -> str:
    """A few lines of CURRENT context for SessionStart: the project's recent user
    decisions, the unfinished count and the latest supersession.

    Config-gated at the CALLER (``recall.session_brief``, default off); byte-bounded;
    fail-open (any error → ``""`` — a broken bank must not break a session start);
    zero LLM calls.
    """
    if not project:
        return ""
    st = _store(store)
    try:
        from navig.recall.history import UNFINISHED_LIFECYCLES, UNFINISHED_TYPES  # noqa: PLC0415

        # The brief feeds MODEL context: every line passes the for_model filter —
        # a below-agent chat's record never appears in a SessionStart section.
        priv = private_sources(st)
        lines: list[str] = []
        decs = _drop_private_records(
            st, st.list_records(rtype="decision", projects=[project], limit=50), priv)
        mine = [r for r in decs if r.status_basis == "user" or r.endorsement != "none"][:3]
        for r in mine:
            title = " ".join((r.title or "").split())[:90]
            lines.append(f"decision · {r.status}: {title}")
        unfin = _drop_private_records(
            st, st.records_by_lifecycle(UNFINISHED_LIFECYCLES, rtypes=UNFINISHED_TYPES,
                                        projects=[project], limit=999), priv)
        n = sum(1 for r in unfin if r.impl != "verified")
        if n:
            lines.append(f"unfinished: {n} plan/task(s) awaiting verification")
        sup = _drop_private_records(
            st, st.list_records(status="superseded", projects=[project], limit=1), priv)
        if sup:
            title = " ".join((sup[0].title or "").split())[:80]
            lines.append(f"latest supersession: {title}")
    except Exception:  # noqa: BLE001 — SessionStart must survive a broken bank
        return ""
    if not lines:
        return ""
    text = "\n".join(f"  {ln}" for ln in lines)
    data = text.encode("utf-8")
    if len(data) > budget:
        text = data[:budget].decode("utf-8", errors="ignore") + "…"
    return text


def status(store: RecallStore | None = None) -> dict[str, Any]:
    """What is in the bank, whether reading is enabled, where it lives, and the projects."""
    from navig.recall.paths import recall_root  # noqa: PLC0415

    st = _store(store)
    s = st.stats()
    s["enabled"] = is_enabled()
    s["root"] = str(recall_root())
    s["projects"] = st.project_counts()
    return s


__all__ = ["UNBUILT_IMPL", "VIEWS", "Scope", "ViewSpec", "ask", "changes", "cite", "conflicts",
           "excerpt", "history", "is_enabled", "private_sources", "record_detail", "record_row",
           "scope_for", "search", "session_brief", "source_link", "status", "trace", "unfinished",
           "view"]
