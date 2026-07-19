"""
Runtime error-driven fallback policy for the LLM dispatch layer.

When a provider call fails **mid-flight** (a rate-limit, an overload, a retired
model), NAVIG should visibly hop to the next configured model instead of ending
the turn with an error. This module owns the two decisions that governs:

  1. **What kind of failure is this?**  :func:`categorize_error` maps an
     exception (or a ``run_llm`` ``finish_reason`` string like
     ``"error:ProviderError: … (status=429)"``) to one canonical category.

  2. **How long should we avoid this model?**  A per-model cooldown map keyed by
     ``"provider:model"`` records ``time.time()`` deadlines so the *next* call
     (in this turn *or* a later one) skips a model we just saw fail — the exact
     fix for "Claude Max is capped for the next hour and every agent call still
     wastes a round-trip on Claude first."

The policy pairs 7 canonical error categories with per-model cooldown maps and
a filtered fallback chain, wired into NAVIG's dispatcher.

The cooldown map is process-global on purpose: it is soft, best-effort state
that should be shared across every ``run_llm`` call in the daemon. Tests reset
it with :func:`reset_cooldowns`.
"""

from __future__ import annotations

import logging
import re
import threading
import time

logger = logging.getLogger("navig.llm.fallback_policy")

# ─────────────────────────────────────────────────────────────
# Error categories  (canonical — keep in sync with COOLDOWN_SECONDS)
# ─────────────────────────────────────────────────────────────
RATE_LIMITED = "rate_limited"   # 429 — provider/plan quota hit
OVERLOADED = "overloaded"       # 529 / 503 — provider temporarily overloaded
SERVER_ERROR = "server_error"   # 5xx — provider-side failure
TIMEOUT = "timeout"             # request timed out / connection dropped
AUTH = "auth"                   # 401 / 403 — bad or expired credential
PAYMENT = "payment"             # 402 — billing / insufficient credit
DEAD_MODEL = "dead_model"       # 404 / 410 — model retired or unknown
UNKNOWN = "unknown"             # anything else


# Human-readable phrase per category for a user-facing rotation notice. Phrased as
# a present-participle state so it slots into "Primary account was {phrase} —
# answered with …". The CLI (`navig ask`), the chat boundary (Telegram/deck chat),
# and the deck ask API all render a rotation, so the mapping lives HERE next to the
# categories — one source of truth instead of a drifting copy per surface.
_CATEGORY_PHRASE: dict[str, str] = {
    RATE_LIMITED: "rate-limited",
    OVERLOADED: "overloaded",
    SERVER_ERROR: "erroring",
    TIMEOUT: "timing out",
    AUTH: "failing to authenticate",
    PAYMENT: "having a billing issue",
    DEAD_MODEL: "using a retired model",
    UNKNOWN: "unavailable",
    # "cooldown" is a pre-skip pseudo-reason (the account was still cooling from a
    # prior failure, so no live error occurred) — not a categorize_error output.
    "cooldown": "cooling down from a recent failure",
}


def describe_category(category: str | None) -> str:
    """A human phrase for a fallback/cooldown category, for a rotation notice
    (e.g. "Primary account was **rate-limited** — answered with …").

    Blank → ``"unavailable"``; a novel/unmapped category degrades to its
    de-underscored form so it still reads sanely rather than showing a raw token.
    """
    if not category:
        return "unavailable"
    return _CATEGORY_PHRASE.get(category) or category.replace("_", " ")

# How long to avoid a model after each failure class (seconds).
# A rate-limit window is minutes-to-hours, but we re-probe after 60s rather than
# block for the whole window — the model may free up, and a cheap re-probe beats
# permanently abandoning the user's preferred (subscription) model.
COOLDOWN_SECONDS: dict[str, float] = {
    RATE_LIMITED: 60.0,
    OVERLOADED: 20.0,
    SERVER_ERROR: 10.0,
    TIMEOUT: 5.0,
    AUTH: 300.0,        # credential is bad — don't hammer it
    PAYMENT: 600.0,     # out of credit — long back-off
    DEAD_MODEL: 3600.0,  # retired — effectively "don't use this again this session"
    UNKNOWN: 0.0,        # don't cool on an unclassified error; just hop once
}

# Categories worth hopping to a *different* model for. Everything real is
# hoppable — even auth/payment, because the fallback chain crosses providers, so
# a different provider may still have a working credential.
_HOPPABLE = {
    RATE_LIMITED, OVERLOADED, SERVER_ERROR, TIMEOUT, AUTH, PAYMENT, DEAD_MODEL, UNKNOWN,
}

_STATUS_RE = re.compile(r"status[=:\s]+(\d{3})")
_BARE_STATUS_RE = re.compile(r"\b(4\d\d|5\d\d)\b")

_cooldowns: dict[str, float] = {}
_lock = threading.Lock()


def categorize_error(err: object) -> str:
    """Classify an exception or ``finish_reason`` string into a category.

    Accepts a live exception (reads ``status_code`` / ``error_type`` when
    present, e.g. a :class:`~navig.providers.clients.ProviderError`) or the
    ``"error:…"`` string that ``run_llm`` stamps onto a failed
    :class:`~navig.llm.types.LLMResult`.
    """
    status: int | None = None
    etype: str | None = None
    text = ""

    if isinstance(err, BaseException):
        status = _coerce_int(getattr(err, "status_code", None))
        etype = getattr(err, "error_type", None)
        text = f"{type(err).__name__}: {err}"
    else:
        text = str(err or "")

    low = text.lower()

    # Recover a status code from the message when the object didn't carry one
    # (run_llm flattens exceptions to strings before we see them).
    if status is None:
        m = _STATUS_RE.search(low) or _BARE_STATUS_RE.search(low)
        if m:
            status = _coerce_int(m.group(1))

    # 1) Structured error_type from ProviderError wins when present.
    if etype:
        mapped = {
            "rate_limit": RATE_LIMITED,
            "auth": AUTH,
            "billing": PAYMENT,
            "server_error": SERVER_ERROR,
        }.get(etype)
        if mapped:
            return mapped

    # 2) Status-code mapping.
    if status is not None:
        if status == 429:
            return RATE_LIMITED
        if status == 529:
            return OVERLOADED
        if status == 503:
            return OVERLOADED
        if status in (401, 403):
            return AUTH
        if status == 402:
            return PAYMENT
        if status in (404, 410):
            return DEAD_MODEL
        if status >= 500:
            return SERVER_ERROR

    # 3) Keyword heuristics for string-only errors.
    if "rate limit" in low or "ratelimit" in low or "quota" in low or "429" in low:
        return RATE_LIMITED
    if "overloaded" in low or "capacity" in low or "529" in low:
        return OVERLOADED
    if "timeout" in low or "timed out" in low or "connect" in low and "error" in low:
        return TIMEOUT
    if "unauthor" in low or "invalid api key" in low or "authentication" in low or "expired" in low:
        return AUTH
    if "billing" in low or "insufficient" in low or "payment" in low or "credit" in low:
        return PAYMENT
    if ("model" in low and ("not found" in low or "does not exist" in low or "decommission" in low
                            or "retired" in low or "deprecated" in low)) or "410" in low:
        return DEAD_MODEL
    if "server error" in low or "internal error" in low or "bad gateway" in low:
        return SERVER_ERROR

    return UNKNOWN


def should_fallback(category: str) -> bool:
    """True when it is worth hopping to a different model for this category."""
    return category in _HOPPABLE


# Categories where trying the SAME model on a DIFFERENT account might succeed:
# a transient cap/overload, or a per-account credential/billing issue. A retired
# model or an unclassified error would fail identically on every account, so
# rotating across accounts there is wasted round-trips.
_ROTATE_WORTHY = {RATE_LIMITED, OVERLOADED, SERVER_ERROR, TIMEOUT, AUTH, PAYMENT}


def should_rotate_account(category: str) -> bool:
    """True when the same model on another account (e.g. a second Claude Max
    subscription) is worth trying for this failure category."""
    return category in _ROTATE_WORTHY


# Categories where the WHOLE account is affected, not just the one model: a
# subscription/API quota (rate limit), a billing cap, or a bad credential all
# apply across every model on that account. For these, cooling the entire
# account (keyed only by connection) stops rotation from re-probing a capped
# account for a *different* model — a Claude Max sub's inference limit is
# account-wide, so an opus cap must also skip that account for sonnet.
_ACCOUNT_WIDE = frozenset({RATE_LIMITED, PAYMENT, AUTH})


def account_cool_key(provider: str, model: str, connection_id: str | None) -> str:
    """The canonical cooldown key for a specific account (connection) running a
    model: ``"provider:model@conn:<cid>"``, or bare ``"provider:model"`` when no
    connection backs it. The three dispatch paths (run_llm,
    complete_via_connection, the agent loop) all key per-account cooldowns
    through this, so the format can't drift between them as they converge."""
    base = f"{provider}:{model}"
    return f"{base}@conn:{connection_id}" if connection_id else base


def _account_key(spec: str) -> str | None:
    """The account-wide cooldown key for a ``…@conn:<cid>`` spec, else ``None``.

    e.g. ``"anthropic:claude-opus-4-8@conn:abc"`` → ``"@conn:abc"``. A spec with
    no ``@conn:`` (a bare ``provider:model``, or the run_llm default account)
    has no account key and is treated per-model only.
    """
    if "@conn:" in spec:
        cid = spec.rsplit("@conn:", 1)[-1].strip()
        if cid:
            return f"@conn:{cid}"
    return None


def mark_cooldown(spec: str, category: str) -> float:
    """Record a cooldown for ``spec`` and return the deadline (epoch).

    A zero-duration category (``unknown``) is a no-op so we don't sideline a
    model over a one-off blip. For an **account-wide** category on a
    ``…@conn:<cid>`` spec, the whole account is cooled too (all its models).
    """
    secs = COOLDOWN_SECONDS.get(category, 0.0)
    if secs <= 0:
        return 0.0
    deadline = time.time() + secs
    with _lock:
        # Never shorten an existing, longer cooldown.
        _cooldowns[spec] = max(_cooldowns.get(spec, 0.0), deadline)
        deadline = _cooldowns[spec]
        if category in _ACCOUNT_WIDE:
            ak = _account_key(spec)
            if ak:
                _cooldowns[ak] = max(_cooldowns.get(ak, 0.0), deadline)
    logger.debug("cooldown %s for %.0fs (%s)", spec, secs, category)
    return deadline


def is_cooling(spec: str) -> bool:
    """True when ``spec`` — or the **account** backing it — is within an active
    cooldown window. A ``…@conn:<cid>`` spec is cooling if either its per-model
    key or its account-wide key (``@conn:<cid>``) is active."""
    now = time.time()
    with _lock:
        for key in (spec, _account_key(spec)):
            if key is None:
                continue
            deadline = _cooldowns.get(key)
            if deadline is None:
                continue
            if deadline <= now:
                _cooldowns.pop(key, None)  # expired — keep the map from growing
                continue
            return True
        return False


def cooldown_remaining(spec: str) -> float:
    """Seconds left before ``spec`` (or its account) is usable again (0.0 if
    not cooling) — the longer of the per-model and account-wide windows."""
    now = time.time()
    with _lock:
        deadlines = [
            _cooldowns.get(key)
            for key in (spec, _account_key(spec))
            if key is not None
        ]
    remaining = [max(0.0, d - now) for d in deadlines if d is not None]
    return max(remaining) if remaining else 0.0


def reset_cooldowns() -> None:
    """Clear all cooldowns (test hook / manual reset)."""
    with _lock:
        _cooldowns.clear()


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
