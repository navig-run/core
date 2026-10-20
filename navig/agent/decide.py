"""
navig.agent.decide — bounded decisions from a decision model (Cloudflare Clef).

A decision model answers a fixed set of TYPED questions about some context, with a
calibrated probability per option — never free text. That is exactly the shape of the
many places navig asks a full LLM "reply with ONE word from this list" and then regexes
the answer out of its prose: a decision model cannot answer outside the list, answers
every question in one pass (~40 ms for Clef-flash, ~200 ms for Clef) and says how sure
it is.

Question types (Cloudflare's names):

* ``noul``   — yes/no; the answer is the probability of yes  → :func:`yesno`
* ``choice`` — one of named options (2–255)                  → :func:`choice`
* ``score``  — a level on an ordered rubric (2–10 levels)    → :func:`score`

The model runs on Workers AI in the USER's own Cloudflare account — the same account and
credential they connected for Lighthouse (:mod:`navig.cloud.cf_token`). Nothing is
sent anywhere unless the user turned it on (``decide.enabled``, default off).

The contract every consumer relies on
-------------------------------------
:func:`decide` / :func:`adecide` **never raise**. They return ``None`` whenever they
cannot give a confident answer — the feature is off, this use is switched off, shadow
mode is on, there is no credential, Cloudflare refused or failed or timed out, the daily
cap is spent — and the caller then runs the logic it has always run. So turning the
feature off, or Clef breaking, gives today's behaviour exactly. Answers below
``decide.min_confidence`` are dropped one by one; if none survive, the result is ``None``.

:func:`call` is the raw, raising form for ``navig decider test``, where the user needs
to see WHY a call failed rather than a silent fallback.

Configuration (``navig config set decide.<key> <value>``, or ``navig decider …``)::

    decide.enabled          false        master switch
    decide.model            clef-flash   clef-flash (fast) | clef (stronger)
    decide.use.<id>         per USES     on | shadow | off for one use (true/false work too)
    decide.min_confidence   0.5          below this the caller's own logic decides
    decide.timeout_ms       1500         per-call ceiling
    decide.daily_cap        2000         calls per UTC day, all uses together
    decide.shadow           false        call and LOG, but let the old logic decide
    decide.redact           true         strip emails / phone numbers / secrets first
    decide.account_id       (auto)       Cloudflare account; defaults to Lighthouse's
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────

MODELS: tuple[str, ...] = ("clef-flash", "clef")
DEFAULT_MODEL = "clef-flash"
#: Workers AI model id. The variant (clef / clef-flash) is selected by the body's ``model``.
#: Workers AI model id family. Each variant has its OWN endpoint — measured 2026-10-04:
#: posting ``model: "clef-flash"`` to ``@cf/cloudflare/clef`` answers HTTP 422
#: "Unsupported model 'clef-flash'. Use 'clef'." — so the path follows the model.
MODEL_ID = "@cf/cloudflare/clef"
_RUN_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{model}"


def model_id(model: str) -> str:
    """``clef`` → ``@cf/cloudflare/clef`` · ``clef-flash`` → ``@cf/cloudflare/clef-flash``."""
    return f"@cf/cloudflare/{model}"

MAX_QUESTIONS = 64
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
#: Longest ``state`` we send. Clef has a 64k context; triage-style decisions need far less,
#: and every input token is billed.
MAX_STATE_CHARS = 8_000

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "model": DEFAULT_MODEL,
    "min_confidence": 0.5,
    "timeout_ms": 1500,
    "daily_cap": 2000,
    "shadow": False,
    "redact": True,
}

#: After a transient failure (5xx, timeout, network), stop calling for this long so a
#: broken edge does not add the full timeout to every message.
_PAUSE_AFTER_FAILURE_S = 60.0

QuestionType = Literal["noul", "choice", "score"]


# ── Uses (the registry every surface is generated from) ──────


#: A use's mode. ``shadow`` = Clef answers and its agreement with the previous logic is
#: recorded, but the previous logic still decides — the safe way to introduce a use.
MODES: tuple[str, ...] = ("on", "shadow", "off")


@dataclass(frozen=True)
class Use:
    id: str
    label: str
    description: str
    default_mode: str = "on"
    #: The call site only COMPARES (its caller has no code path that acts on Clef's
    #: answer yet), so "on" is clamped to "shadow" — a switch must not promise what the
    #: code does not do.
    shadow_only: bool = False


USES: dict[str, Use] = {
    u.id: u
    for u in (
        Use(
            "telegram_triage",
            "Telegram triage",
            "Sorts each Telegram message into a complexity tier (simple … agentic) when the "
            "rule-based router is unsure, so the right model answers it.",
        ),
        Use(
            "inbox_sort",
            "Inbox sorting",
            "Picks the wiki / hub category for a file dropped into an inbox.",
        ),
        Use(
            "inbox_space",
            "Inbox space routing",
            "Picks which of your spaces a file belongs in, when it should leave this one.",
        ),
        Use(
            "guard_tool_call",
            "Guarded mode (NAVIG OS)",
            "In NAVIG OS's Guarded permission mode, asks whether a tool call is hard to undo, "
            "reaches outside the project or reaches other people; a likely yes becomes a "
            "permission prompt. It can only add a prompt, and when it cannot answer the OS "
            "asks you. Not compare-only: shadow would make every call ask.",
        ),
        Use(
            "telegram_brief",
            "Telegram message brief",
            "ONE call per Telegram message answering mode, depth, system intent, space "
            "intent, reminder and settings intent — compared with each existing classifier.",
            default_mode="shadow",
            shadow_only=True,
        ),
        # ── judges: shadow-only, compared with the logic that decides today ──
        Use(
            "judge_confirm",
            "Confirm / cancel replies",
            "Reads your reply to 'run this?' — confirm, cancel or something else — in any "
            "language. Today only exact English words count.",
            default_mode="shadow",
            shadow_only=True,
        ),
        Use(
            "judge_reply_quality",
            "Reply quality",
            "Judges whether a model's reply actually answers the request, next to the "
            "length/hedge rules that decide whether to retry on a stronger model.",
            default_mode="shadow",
            shadow_only=True,
        ),
        Use(
            "judge_continuation",
            "Auto-continue",
            "Reads the agent's own reply: should it continue, offer a choice, wait, or is "
            "it blocked — next to the keyword scorer that drives auto-continue.",
            default_mode="shadow",
            shadow_only=True,
        ),
        Use(
            "judge_action_safety",
            "Action safety",
            "Is a proposed destructive action safe? A calibrated probability next to the "
            "verifier's self-reported confidence (runs only while the verifier is on).",
            default_mode="shadow",
            shadow_only=True,
        ),
        Use(
            "judge_goal",
            "Goal achieved",
            "After a multi-step task: was the goal actually achieved, next to the model's "
            "own self-assessment.",
            default_mode="shadow",
            shadow_only=True,
        ),
    )
}

#: Not a switchable use — ``navig decider test`` calls :func:`call` under this label.
TEST_USE = "test"


# ── Questions and answers ────────────────────────────────────


@dataclass(frozen=True)
class Question:
    id: str
    type: QuestionType
    instructions: str
    criteria: Any = None

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


def _check_id(qid: str) -> str:
    if not _ID_RE.match(qid or ""):
        raise ValueError(f"question id {qid!r}: use 1-100 letters, digits, '_', '.', '-'")
    return qid


def yesno(qid: str, instructions: str, *, yes: str = "", no: str = "") -> Question:
    """A yes/no question. ``Decision.answer`` is ``"yes"`` or ``"no"``."""
    criteria = None
    if yes or no:
        criteria = {k: v for k, v in (("true", yes), ("false", no)) if v}
    return Question(_check_id(qid), "noul", instructions, criteria)


def choice(qid: str, instructions: str, options: Mapping[str, str | None] | Sequence[str]) -> Question:
    """Pick one of *options* — a ``{option: description}`` map, or a plain list of names."""
    opts = dict(options) if isinstance(options, Mapping) else {str(o): None for o in options}
    if not 2 <= len(opts) <= 255:
        raise ValueError(f"choice {qid!r}: needs 2-255 options, got {len(opts)}")
    return Question(_check_id(qid), "choice", instructions, opts)


def score(qid: str, instructions: str, levels: Sequence[str]) -> Question:
    """Rate on an ordered rubric, lowest level first (2-10 levels)."""
    lv = [str(x) for x in levels]
    if not 2 <= len(lv) <= 10:
        raise ValueError(f"score {qid!r}: needs 2-10 levels, got {len(lv)}")
    return Question(_check_id(qid), "score", instructions, lv)


@dataclass(frozen=True)
class Decision:
    """One answered question.

    ``answer`` is ``"yes"``/``"no"`` for a yes/no question, the chosen option for a
    choice, and the most likely level's label for a score. ``probs`` maps every option
    (or ``yes``/``no``, or each level label) to its probability. ``value`` is the
    probability-weighted level for a score (0-based, may fall between levels).

    ``confidence`` is the probability of ``answer`` — what a person reads as "69% sure",
    and what ``decide.min_confidence`` is compared with. ``clef_confidence`` is Clef's own
    spread-based certainty, kept for reference: it is much stricter (a 0.69 / 0.29 / 0.02
    split scored 0.34, live), so gating on it would discard answers that are plainly right.
    """

    answer: str
    confidence: float
    probs: dict[str, float] = field(default_factory=dict)
    value: float | None = None
    clef_confidence: float | None = None

    @property
    def yes(self) -> bool:
        return self.answer == "yes"

    def ranked(self) -> list[tuple[str, float]]:
        """Options, most likely first."""
        return sorted(self.probs.items(), key=lambda kv: kv[1], reverse=True)


@dataclass(frozen=True)
class CallResult:
    answers: dict[str, Decision]
    model: str
    latency_ms: int
    input_tokens: int


@dataclass(frozen=True)
class Outcome:
    """What one guarded call produced, and whether it may be APPLIED.

    ``live`` is False in shadow mode: the caller must let its previous logic decide and
    may only :func:`compare` against these answers. :meth:`get` returns an answer only
    when it clears ``min_confidence``; :attr:`answers` holds every answer, for comparing.
    """

    use: str
    answers: dict[str, Decision]
    live: bool
    min_confidence: float
    latency_ms: int = 0

    def get(self, qid: str) -> Decision | None:
        d = self.answers.get(qid)
        return d if d is not None and d.confidence >= self.min_confidence else None

    def applied(self, qid: str) -> Decision | None:
        """The answer the caller should ACT on — None in shadow mode or when unsure."""
        return self.get(qid) if self.live else None


class DecideError(Exception):
    """A call that could not produce answers. ``kind`` says why, for status views:

    ``no_credential`` · ``no_account`` · ``auth`` · ``http`` · ``timeout`` · ``network`` ·
    ``bad_response`` · ``invalid``
    """

    def __init__(self, kind: str, message: str, *, status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


# ── Settings (fresh across config edits, without re-reading on every call) ──


@dataclass(frozen=True)
class Settings:
    enabled: bool
    model: str
    uses: dict[str, str]  # use id -> "on" | "shadow" | "off"
    min_confidence: float
    timeout_s: float
    daily_cap: int
    shadow: bool
    redact: bool
    account_id: str


_cfg_lock = threading.Lock()
_cfg_mtime: int | None = -1  # sentinel: never read


def _config_manager():
    from navig.config import get_config_manager

    return get_config_manager()


def _section() -> dict[str, Any]:
    """The ``decide`` config section — re-read whenever config.yaml changes on disk.

    The daemon lives for days; a ``navig decider off`` typed in another terminal must take
    effect without a restart. Comparing the file's mtime is a stat per call, and the
    config is only re-parsed when the user actually changed it.
    """
    global _cfg_mtime
    cm = _config_manager()
    try:
        mtime: int | None = (cm.global_config_dir / "config.yaml").stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime != _cfg_mtime:
        with _cfg_lock:
            if mtime != _cfg_mtime:
                try:
                    cm.refresh_global_config()
                except Exception:  # noqa: BLE001 — a stale read beats no read
                    logger.debug("decide: config refresh failed", exc_info=True)
                _cfg_mtime = mtime
    try:
        sec = cm.get("decide", {})
    except Exception:  # noqa: BLE001
        sec = {}
    return sec if isinstance(sec, dict) else {}


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(f, lo), hi)


def parse_mode(value: Any, default: str) -> str:
    """``on`` / ``shadow`` / ``off`` from a config value. Booleans and their string forms
    (``true``, ``false``, ``yes``, ``off`` …) mean on/off; anything unreadable → *default*."""
    from navig.core.coerce import coerce_bool

    if value is None or value == "":
        return default
    if isinstance(value, str) and value.strip().lower() in MODES:
        return value.strip().lower()
    marker = object()
    parsed = coerce_bool(value, default=marker)  # type: ignore[arg-type]
    if parsed is marker:
        return default
    return "on" if parsed else "off"


def _clamp(use: Use, mode: str) -> str:
    return "shadow" if use.shadow_only and mode == "on" else mode


def settings() -> Settings:
    from navig.core.coerce import coerce_bool, coerce_int

    sec = _section()
    use_sec = sec.get("use") if isinstance(sec.get("use"), dict) else {}
    model = str(sec.get("model") or DEFAULT_MODEL).strip().lower()
    if model not in MODELS:
        model = DEFAULT_MODEL
    return Settings(
        enabled=coerce_bool(sec.get("enabled"), default=False),
        model=model,
        uses={uid: _clamp(u, parse_mode(use_sec.get(uid), u.default_mode)) for uid, u in USES.items()},
        min_confidence=_as_float(sec.get("min_confidence"), DEFAULTS["min_confidence"], 0.0, 1.0),
        timeout_s=_as_float(sec.get("timeout_ms"), DEFAULTS["timeout_ms"], 200, 30_000) / 1000.0,
        daily_cap=coerce_int(sec.get("daily_cap"), DEFAULTS["daily_cap"], minimum=0),
        shadow=coerce_bool(sec.get("shadow"), default=False),
        redact=coerce_bool(sec.get("redact"), default=True),
        account_id=str(sec.get("account_id") or "").strip(),
    )


# ── Credentials ──────────────────────────────────────────────

_cred_lock = threading.Lock()
_cred_cache: tuple[float, str] | None = None  # (monotonic expiry, token)
_CRED_TTL_S = 300.0


def _token() -> str:
    """The user's Cloudflare credential (Lighthouse's), cached for five minutes.

    Single-flight: the lock is HELD while the vault is read. The brief and the judges
    start at the same moment, and concurrent FIRST reads of the vault in a fresh process
    fail with "secret is unreadable" for all but one reader (reproduced 4/4, 2026-10-04 —
    a cold-start decrypt race inside the vault engine). One reader at a time avoids it, and
    the others get the cached value. An empty result is never cached: one bad read must
    not silence every decision for five minutes.
    """
    global _cred_cache
    with _cred_lock:
        now = time.monotonic()
        if _cred_cache and _cred_cache[0] > now:
            return _cred_cache[1]
        from navig.cloud.cf_token import resolve_cf_token

        tok = resolve_cf_token(caller="decide")
        if tok:
            _cred_cache = (now + _CRED_TTL_S, tok)
        return tok


def _forget_token() -> None:
    global _cred_cache
    with _cred_lock:
        _cred_cache = None


def _account_id(token: str, s: Settings) -> str:
    """``decide.account_id`` → Lighthouse's ``cloud.lighthouse_account_id`` → ask Cloudflare.

    A looked-up id is saved to ``decide.account_id`` so the lookup happens once.
    """
    if s.account_id:
        return s.account_id
    try:
        acct = str(_config_manager().get("cloud.lighthouse_account_id", "") or "").strip()
    except Exception:  # noqa: BLE001
        acct = ""
    if acct:
        return acct
    from navig.cloud.lighthouse_deploy import DeployError, resolve_account_id

    try:
        acct = resolve_account_id(token, None)
    except DeployError as exc:
        raise DecideError(
            "no_account",
            f"{exc} — set it with: navig config set decide.account_id <id>",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — network etc.
        raise DecideError("network", f"could not list Cloudflare accounts: {exc}") from exc
    try:
        _config_manager().set_global("decide.account_id", acct)
    except Exception:  # noqa: BLE001 — best-effort cache
        logger.debug("decide: could not save decide.account_id", exc_info=True)
    return acct


# ── Redaction ────────────────────────────────────────────────

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])"), "[phone]"),
    # Long opaque runs: API keys, tokens, hashes. Ordinary words never reach 32 chars.
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[secret]"),
)


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


# ── Wire format ──────────────────────────────────────────────


def build_body(
    state: Any,
    questions: Sequence[Question],
    *,
    model: str,
    images: Sequence[Mapping[str, str]] | None = None,
    redact_state: bool = False,
) -> dict[str, Any]:
    if not questions:
        raise DecideError("invalid", "at least one question is required")
    if len(questions) > MAX_QUESTIONS:
        raise DecideError("invalid", f"at most {MAX_QUESTIONS} questions per call, got {len(questions)}")
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise DecideError("invalid", "question ids must be unique")
    if isinstance(state, str):
        state = state[:MAX_STATE_CHARS]
        if redact_state:
            state = redact(state)
    body: dict[str, Any] = {
        "model": model,
        "state": state,
        "questions": {q.id: q.to_wire() for q in questions},
    }
    if images:
        body["images"] = [dict(img) for img in images][:4]
    return body


def parse_answers(payload: Any, questions: Sequence[Question]) -> tuple[dict[str, Decision], str, int]:
    """Workers AI response → ``({id: Decision}, model, input_tokens)``.

    Accepts the REST envelope (``{"result": …, "success": …}``) or the bare model output
    (``{"model", "answers", "usage"}`` — what a Worker's ``env.AI.run`` returns).
    """
    root = payload
    if isinstance(root, dict) and "result" in root and ("success" in root or "errors" in root):
        if root.get("success") is False:
            raise DecideError("bad_response", f"Cloudflare reported failure: {root.get('errors')}")
        root = root.get("result")
    if not isinstance(root, dict) or not isinstance(root.get("answers"), dict):
        raise DecideError("bad_response", "response has no 'answers' object")
    answers: dict[str, Any] = root["answers"]
    out: dict[str, Decision] = {}
    for q in questions:
        raw = answers.get(q.id)
        if not isinstance(raw, dict):
            raise DecideError("bad_response", f"no answer for question {q.id!r}")
        try:
            out[q.id] = _parse_one(q, raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise DecideError("bad_response", f"malformed answer for {q.id!r}: {exc}") from exc
    usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
    try:
        tokens = int(usage.get("input_tokens") or 0)
    except (TypeError, ValueError):
        tokens = 0
    return out, str(root.get("model") or ""), tokens


def _parse_one(q: Question, raw: dict[str, Any]) -> Decision:
    if q.type == "noul":
        p = min(max(float(raw["noul"]), 0.0), 1.0)
        return Decision(
            answer="yes" if p >= 0.5 else "no",
            confidence=max(p, 1.0 - p),
            probs={"yes": p, "no": 1.0 - p},
        )
    probs = {str(k): float(v) for k, v in dict(raw["probabilities"]).items()}
    if q.type == "choice":
        answer = str(raw.get("choice") or max(probs, key=probs.__getitem__))
        if q.criteria and answer not in q.criteria:
            raise ValueError(f"answer {answer!r} is not one of the options")
        return Decision(
            answer=answer,
            confidence=probs.get(answer, 0.0),
            probs=probs,
            clef_confidence=_opt_float(raw.get("confidence")),
        )
    # score: probabilities are keyed by level index ("0", "1", …)
    levels = list(q.criteria or [])
    by_label: dict[str, float] = {}
    for k, v in probs.items():
        idx = int(k)
        by_label[levels[idx] if 0 <= idx < len(levels) else k] = v
    best = max(probs, key=probs.__getitem__)
    best_idx = int(best)
    label = levels[best_idx] if 0 <= best_idx < len(levels) else best
    return Decision(
        answer=label,
        confidence=probs[best],
        probs=by_label,
        value=float(raw["score"]),
        clef_confidence=_opt_float(raw.get("confidence")),
    )


def _opt_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ── The raw call ─────────────────────────────────────────────


def _redact_detail(text: str) -> str:
    """Error text is stored and shown — never let a credential ride along."""
    return redact(str(text))[:300]


def call(
    state: Any,
    questions: Sequence[Question],
    *,
    model: str | None = None,
    images: Sequence[Mapping[str, str]] | None = None,
    use: str = TEST_USE,
    timeout_s: float | None = None,
) -> CallResult:
    """One call to Clef, raising :class:`DecideError` on any failure.

    Ignores the on/off switches (that is :func:`decide`'s job) but still counts against
    the daily cap's ledger, so ``navig decider test`` is visible in usage.
    """
    s = settings()
    mdl = (model or s.model).lower()
    if mdl not in MODELS:
        raise DecideError("invalid", f"unknown model {mdl!r}; choose one of {', '.join(MODELS)}")
    body = build_body(state, questions, model=mdl, images=images, redact_state=s.redact)
    token = _token()
    if not token:
        raise DecideError(
            "no_credential",
            "no Cloudflare credential — connect your account with `navig lighthouse login` "
            "(or set CLOUDFLARE_API_TOKEN)",
        )
    account = _account_id(token, s)

    import httpx

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            _RUN_URL.format(account=account, model=mdl),
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=timeout_s or s.timeout_s,
        )
    except httpx.TimeoutException as exc:
        _record(use, ok=False, kind="timeout", t0=t0, model=mdl, detail=str(exc))
        raise DecideError("timeout", f"Clef did not answer within {timeout_s or s.timeout_s:.1f}s") from exc
    except httpx.HTTPError as exc:
        _record(use, ok=False, kind="network", t0=t0, model=mdl, detail=str(exc))
        raise DecideError("network", f"could not reach Cloudflare: {exc}") from exc

    if resp.status_code in (401, 403):
        _record(use, ok=False, kind="auth", t0=t0, model=mdl, detail=f"HTTP {resp.status_code}")
        raise DecideError(
            "auth",
            f"Cloudflare refused the call (HTTP {resp.status_code}): your credential has "
            f"no Workers AI permission. {grant_hint()}",
            status=resp.status_code,
        )
    if resp.status_code >= 400:
        _record(use, ok=False, kind="http", t0=t0, model=mdl, detail=f"HTTP {resp.status_code}: {resp.text[:200]}")
        raise DecideError(
            "http",
            f"Cloudflare answered HTTP {resp.status_code}: {_redact_detail(resp.text[:200])}",
            status=resp.status_code,
        )
    try:
        answers, model_out, tokens = parse_answers(resp.json(), questions)
    except DecideError as exc:
        _record(use, ok=False, kind=exc.kind, t0=t0, model=mdl, detail=str(exc))
        raise
    except ValueError as exc:  # not JSON
        _record(use, ok=False, kind="bad_response", t0=t0, model=mdl, detail=str(exc))
        raise DecideError("bad_response", f"response was not JSON: {exc}") from exc
    latency = _record(use, ok=True, t0=t0, model=model_out or mdl, input_tokens=tokens)
    return CallResult(answers=answers, model=model_out or mdl, latency_ms=latency, input_tokens=tokens)


def grant_hint() -> str:
    """How to give the stored credential Workers AI access — depends on WHICH credential."""
    try:
        from navig.cloud.cf_token import credential_kind

        kind = credential_kind()
    except Exception:  # noqa: BLE001
        kind = ""
    if kind == "oauth":
        return "Re-run `navig lighthouse login` to grant it."
    if kind == "env":
        return "Add 'Workers AI: Read' to the token in CLOUDFLARE_API_TOKEN."
    return (
        "Edit your API token at https://dash.cloudflare.com/profile/api-tokens and add "
        "'Workers AI: Read' (or switch to `navig lighthouse login`)."
    )


def _record(
    use: str,
    *,
    ok: bool,
    t0: float,
    model: str,
    kind: str = "",
    input_tokens: int = 0,
    detail: str = "",
) -> int:
    """Ledger + cost tracker. Best-effort: bookkeeping never breaks a decision."""
    latency = int((time.perf_counter() - t0) * 1000)
    try:
        from navig.store.decide_usage import get_decide_usage_store

        get_decide_usage_store().record_call(
            use=use,
            ok=ok,
            kind=kind,
            latency_ms=latency,
            model=model,
            input_tokens=input_tokens,
            detail=_redact_detail(detail),
        )
    except Exception:  # noqa: BLE001
        logger.debug("decide: usage ledger write failed", exc_info=True)
    if ok:
        try:
            from navig.cost_tracker import get_session_tracker

            get_session_tracker().record(
                model=model_id(model),
                input_tokens=input_tokens,
                api_duration_s=latency / 1000.0,
            )
        except Exception:  # noqa: BLE001
            logger.debug("decide: cost tracker record failed", exc_info=True)
    return latency


# ── The guarded entry points ─────────────────────────────────

_guard_lock = threading.Lock()
_pause_until = 0.0
_auth_failed_for: str | None = None  # fingerprint of the token Cloudflare refused
_budget_noted_day: str | None = None
_logged_once: set[str] = set()


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _log_once(key: str, msg: str, *args: Any) -> None:
    with _guard_lock:
        if key in _logged_once:
            return
        _logged_once.add(key)
    logger.warning(msg, *args)


def is_active(use: str) -> bool:
    """Would :func:`decide` call Clef for *use* right now (switches only, no network)?
    Never raises — an unreadable config reads as "off"."""
    try:
        s = settings()
    except Exception:  # noqa: BLE001
        logger.debug("decide: settings unreadable", exc_info=True)
        return False
    return s.enabled and s.uses.get(use, "off") != "off"


def outcome(
    state: Any,
    questions: Sequence[Question],
    *,
    use: str,
    images: Sequence[Mapping[str, str]] | None = None,
) -> Outcome | None:
    """Ask Clef under every guard; ``None`` = no answer at all. Never raises.

    Unlike :func:`decide`, this also answers in SHADOW mode (``Outcome.live`` False), so
    a caller can :func:`compare` Clef with its previous logic without acting on Clef.
    """
    global _pause_until, _auth_failed_for, _budget_noted_day
    try:
        s = settings()
        mode = s.uses.get(use, "off")
        if not s.enabled or mode == "off":
            return None
        if time.monotonic() < _pause_until:
            return None
        token = _token()
        if not token:
            _log_once("no_credential", "decide: enabled, but no Cloudflare credential — run `navig lighthouse login`")
            return None
        if _auth_failed_for == _fingerprint(token):
            return None  # Cloudflare already refused THIS credential; a new one retries
        if s.daily_cap and _calls_today() >= s.daily_cap:
            from navig.store.decide_usage import usage_day

            today = usage_day()
            if _budget_noted_day != today:
                _budget_noted_day = today
                _incident("DECIDER_BUDGET", cap=s.daily_cap)
            return None

        result = call(state, questions, images=images, use=use)
    except DecideError as exc:
        if exc.kind == "auth":
            tok = _token()
            _auth_failed_for = _fingerprint(tok) if tok else None
            _forget_token()
            _incident("DECIDER_AUTH_FAILED", status=exc.status)
            _log_once("auth", "decide: %s", exc)
        elif exc.kind in ("timeout", "network", "http"):
            _pause_until = time.monotonic() + _PAUSE_AFTER_FAILURE_S
            logger.warning("decide: %s — using the previous method for %ds", exc, int(_PAUSE_AFTER_FAILURE_S))
        else:
            logger.warning("decide: %s", exc)
        return None
    except Exception:  # noqa: BLE001 — the contract: never raise into a caller
        logger.warning("decide: unexpected failure", exc_info=True)
        return None
    return Outcome(
        use=use,
        answers=result.answers,
        live=mode == "on" and not s.shadow,
        min_confidence=s.min_confidence,
        latency_ms=result.latency_ms,
    )


def decide(
    state: Any,
    questions: Sequence[Question],
    *,
    use: str,
    images: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Decision] | None:
    """Ask Clef; ``None`` means "use your own logic" (see the module docstring). Never raises.

    The confident answers, only when this use is live. In shadow mode the answers are
    logged and ``None`` is returned — a caller that wants to RECORD agreement uses
    :func:`outcome` + :func:`compare` instead.
    """
    out = outcome(state, questions, use=use, images=images)
    if out is None:
        return None
    if not out.live:
        logger.info(
            "decide[shadow] use=%s %s (%d ms) — not applied",
            use,
            {k: (d.answer, round(d.confidence, 3)) for k, d in out.answers.items()},
            out.latency_ms,
        )
        return None
    confident = {k: d for k in out.answers if (d := out.get(k)) is not None}
    if len(confident) < len(out.answers):
        logger.debug(
            "decide: use=%s dropped low-confidence answers %s",
            use,
            sorted(set(out.answers) - set(confident)),
        )
    return confident or None


def compare(out: Outcome | None, qid: str, legacy: str, *, sample: str = "") -> None:
    """Record whether Clef agreed with the previous logic's answer *legacy*. Never raises.

    Call it with the answer the previous logic actually produced. A Clef answer below
    ``min_confidence`` is recorded as an ABSTAIN. *sample* (redacted, truncated) is kept
    only for disagreements, so a human can see what Clef got "wrong" before switching on.
    """
    if out is None:
        return
    try:
        d = out.get(qid)
        from navig.store.decide_usage import get_decide_usage_store

        get_decide_usage_store().record_comparison(
            use=out.use,
            question=qid,
            clef=d.answer if d is not None else None,
            legacy=str(legacy),
            confidence=d.confidence if d is not None else 0.0,
            sample=redact(sample)[:120] if sample else "",
        )
    except Exception:  # noqa: BLE001 — bookkeeping never breaks a decision
        logger.debug("decide: comparison record failed", exc_info=True)


def judge(
    use: str,
    state: Any,
    question: Question,
    legacy: str,
    *,
    sample: str = "",
) -> None:
    """Shadow-compare Clef with an answer the caller has ALREADY made. Never raises,
    never blocks, never changes what the caller does.

    From a coroutine (a running event loop) the call runs as a GC-safe background task;
    from plain sync code, on a daemon thread. Either way the caller's latency is a config
    stat, and the comparison lands in ``navig decider status`` / ``navig decider review``.
    """
    try:
        if not is_active(use):
            return
        text_sample = sample or (state if isinstance(state, str) else "")

        def _work() -> None:
            compare(outcome(state, [question], use=use), question.id, legacy, sample=text_sample)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=_work, name=f"decide-judge-{use}", daemon=True).start()
            return
        from navig.core.background import spawn

        spawn(asyncio.to_thread(_work), name=f"decide-judge:{use}")
    except Exception:  # noqa: BLE001 — a shadow comparison must never touch the real path
        logger.debug("decide: judge %s failed to start", use, exc_info=True)


async def aoutcome(
    state: Any,
    questions: Sequence[Question],
    *,
    use: str,
    images: Sequence[Mapping[str, str]] | None = None,
) -> Outcome | None:
    """:func:`outcome` for async callers (worker thread; never blocks the loop)."""
    if not is_active(use):
        return None
    return await asyncio.to_thread(outcome, state, questions, use=use, images=images)


async def adecide(
    state: Any,
    questions: Sequence[Question],
    *,
    use: str,
    images: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Decision] | None:
    """:func:`decide` for async callers. Runs in a worker thread, so the credential read,
    the HTTP call and the ledger write never block the event loop."""
    if not is_active(use):  # cheap fast path: no thread hop when the feature is off
        return None
    return await asyncio.to_thread(decide, state, questions, use=use, images=images)


def _calls_today() -> int:
    try:
        from navig.store.decide_usage import get_decide_usage_store

        return get_decide_usage_store().calls_today()
    except Exception:  # noqa: BLE001 — an unreadable ledger must not block decisions
        return 0


def _incident(name: str, **data: Any) -> None:
    try:
        from navig.core import incidents

        incidents.record(getattr(incidents, name), **data)
    except Exception:  # noqa: BLE001
        logger.debug("decide: incident record failed", exc_info=True)


# ── Status (CLI, doctor, deck) ───────────────────────────────


def status() -> dict[str, Any]:
    """Everything a status view needs. Reads only; never calls Cloudflare."""
    s = settings()
    try:
        from navig.cloud.cf_token import credential_kind

        cred = credential_kind()
    except Exception:  # noqa: BLE001
        cred = ""
    try:
        acct_known = bool(s.account_id or _config_manager().get("cloud.lighthouse_account_id", ""))
    except Exception:  # noqa: BLE001
        acct_known = bool(s.account_id)
    usage: dict[str, dict[str, int]] = {}
    last: dict[str, Any] | None = None
    agreement: dict[str, dict[str, int]] = {}
    try:
        from navig.store.decide_usage import get_decide_usage_store

        store = get_decide_usage_store()
        usage = store.usage_today()
        last = store.last_call()
        agreement = store.agreement(days=7)
    except Exception:  # noqa: BLE001
        logger.debug("decide: usage ledger read failed", exc_info=True)
    return {
        "enabled": s.enabled,
        "model": s.model,
        "shadow": s.shadow,
        "redact": s.redact,
        "min_confidence": s.min_confidence,
        "timeout_ms": int(s.timeout_s * 1000),
        "daily_cap": s.daily_cap,
        "calls_today": sum(u["calls"] for u in usage.values()),
        "usage_today": usage,
        "uses": [
            {
                "id": u.id,
                "label": u.label,
                "description": u.description,
                "mode": s.uses.get(u.id, "off"),
                "shadow_only": u.shadow_only,
                "on": s.uses.get(u.id, "off") != "off",
                "agreement_7d": agreement.get(u.id, {"total": 0, "agree": 0, "abstain": 0}),
            }
            for u in USES.values()
        ],
        "credential": cred,  # "", "env", "oauth", "token" — never the secret
        "account_known": acct_known,
        "last_call": last,
    }


def reset_state_for_tests() -> None:
    """Tests only: forget cached settings, credentials and failure guards."""
    global _cfg_mtime, _pause_until, _auth_failed_for, _budget_noted_day
    _cfg_mtime = -1
    _pause_until = 0.0
    _auth_failed_for = None
    _budget_noted_day = None
    _logged_once.clear()
    _forget_token()
