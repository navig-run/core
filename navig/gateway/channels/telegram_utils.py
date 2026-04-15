"""Shared utilities for NAVIG Telegram gateway channel modules.

Centralises helpers that were previously duplicated across
telegram_mesh.py, telegram_keyboards.py, telegram_refiner.py,
and telegram_voice.py, and adds patterns ported from the
Hermes agent:

- ``escape_mdv2``         — canonical MarkdownV2 character escaper
- ``sanitize_user_error`` — redact secrets before surfacing error text to users
- ``classify_tg_error``   — lightweight Telegram/HTTP error taxonomy
- ``jittered_backoff``    — decorrelated jittered-exponential backoff (from
                            .lab/hermes-agent/agent/retry_utils.py)

All public names are importable at package level; internal helpers carry a
``_`` prefix and must not be called from outside this module.
"""

from __future__ import annotations

import enum
import random
import re
import threading
import time
from typing import Optional

# ── MarkdownV2 escape ──────────────────────────────────────────────────────────

# Characters that Telegram MarkdownV2 requires to be backslash-escaped.
_MDV2_SPECIAL_RE = re.compile(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])")


def escape_mdv2(text: str) -> str:
    """Escape ``text`` for safe use in Telegram MarkdownV2 messages.

    Replaces all special characters required by the Telegram Bot API
    MarkdownV2 spec with backslash-escaped equivalents.  Safe to call on
    already-escaped strings (double-escaping will double-backslash, which is
    still correct for safe rendering).

    Args:
        text: Arbitrary user or system string.

    Returns:
        Telegram-safe MarkdownV2 string.

    Example::

        >>> escape_mdv2("Hello, world!")
        'Hello, world\\!'
    """
    return _MDV2_SPECIAL_RE.sub(r"\\\1", str(text))


# Backwards-compatible alias used by some older internal callers.
_mdv2_escape = escape_mdv2


# ── User-safe error text sanitization ─────────────────────────────────────────

# Patterns that identify secret-looking values in URLs.
_URL_SECRET_QUERY_RE = re.compile(
    r"([?&](?:access_token|api[_-]?key|auth[_-]?token|token|signature|sig)=)([^&#\s]+)",
    re.IGNORECASE,
)

# Generic ``key=value`` assignment patterns in plain text.
_GENERIC_SECRET_ASSIGN_RE = re.compile(
    r"\b(access_token|api[_-]?key|auth[_-]?token|signature|sig)\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)

# Match anything that looks like a full 32–128 char hex secret or JWT segment.
_HEX_SECRET_RE = re.compile(r"\b[0-9a-f]{32,128}\b", re.IGNORECASE)

# Match Bearer / Basic auth header values.
_AUTH_HEADER_RE = re.compile(
    r"((?:Bearer|Basic)\s+)([A-Za-z0-9+/=._-]{20,})",
    re.IGNORECASE,
)


def sanitize_user_error(text: object) -> str:
    """Scrub secrets from an error string before surfacing it to users.

    Ported from Hermes ``send_message_tool._sanitize_error_text``.  Applies
    multiple redaction passes so that API keys, tokens, and auth headers that
    appear in aiohttp / httpx exception messages are replaced with ``***``.

    This function **never raises** — if redaction itself errors the original
    ``str(text)`` is returned so the pipeline keeps running.

    Args:
        text: Exception, string, or any object.

    Returns:
        Redacted string safe to display in a Telegram message.
    """
    try:
        s = str(text)
        # Query-param secrets
        s = _URL_SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}***", s)
        # key=value secrets in plain text
        s = _GENERIC_SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}=***", s)
        # Bearer / Basic auth tokens
        s = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}***", s)
        # Long hex strings that look like raw secrets
        s = _HEX_SECRET_RE.sub("***", s)
        return s
    except Exception:  # noqa: BLE001
        return str(text)  # best-effort; never raise from sanitizer


# ── Telegram error classification ─────────────────────────────────────────────


class TgErrorKind(enum.Enum):
    """Lightweight taxonomy of Telegram-channel errors.

    Used by the retry / error-reporting logic to decide recovery action
    without string-matching at every call site.
    """

    TRANSIENT = "transient"          # Network flap, connection reset — safe to retry
    RATE_LIMIT = "rate_limit"        # HTTP 429 or Telegram 'Too Many Requests'
    AUTH = "auth"                    # Invalid token or permission denied (non-recoverable)
    NOT_FOUND = "not_found"          # Chat/message not found (stale IDs, removed users)
    PAYLOAD = "payload"              # Message too long, bad parse_mode, etc.
    TIMEOUT = "timeout"              # Request timed out — rebuild session + retry
    UNKNOWN = "unknown"              # Unclassifiable — retry with backoff


def classify_tg_error(exc: BaseException, status_code: Optional[int] = None) -> TgErrorKind:
    """Classify a Telegram channel exception into a ``TgErrorKind``.

    Ported from Hermes ``error_classifier`` but scoped to the simpler
    Telegram/aiohttp error surface only.

    Args:
        exc: The caught exception.
        status_code: HTTP status code when available (e.g. from aiohttp
            ``resp.status``).  Set to ``None`` when not applicable.

    Returns:
        ``TgErrorKind`` value indicating the recovery strategy.
    """
    msg = str(exc).lower()

    if status_code is not None:
        if status_code == 429:
            return TgErrorKind.RATE_LIMIT
        if status_code in (401, 403):
            return TgErrorKind.AUTH
        if status_code == 404:
            return TgErrorKind.NOT_FOUND
        if status_code in (400, 422):
            return TgErrorKind.PAYLOAD
        if status_code in (408, 504):
            return TgErrorKind.TIMEOUT
        if status_code >= 500:
            return TgErrorKind.TRANSIENT

    # Telegram API description strings (JSON body "description" field)
    if "too many requests" in msg or "rate_limit" in msg or "flood" in msg:
        return TgErrorKind.RATE_LIMIT
    if "unauthorized" in msg or "invalid token" in msg or "forbidden" in msg:
        return TgErrorKind.AUTH
    if "not found" in msg or "chat not found" in msg or "message to delete not found" in msg:
        return TgErrorKind.NOT_FOUND
    if "message is too long" in msg or "bad request" in msg or "parse_mode" in msg:
        return TgErrorKind.PAYLOAD

    # aiohttp / asyncio transport errors
    # Import only for name-check to avoid hard dependency at import time.
    exc_type = type(exc).__name__
    if "TimeoutError" in exc_type or "timeout" in msg:
        return TgErrorKind.TIMEOUT
    if any(
        kw in exc_type
        for kw in ("ServerDisconnected", "ClientConnector", "ClientOSError", "ConnectionReset")
    ):
        return TgErrorKind.TRANSIENT

    return TgErrorKind.UNKNOWN


# ── Jittered exponential backoff ──────────────────────────────────────────────

# Monotonic counter for jitter-seed uniqueness within the same process.
# Protected by a lock to avoid data races in concurrent retry paths
# (e.g. multiple Telegram sessions retrying simultaneously).
_jitter_counter: int = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Compute a jittered exponential backoff delay.

    Ported from Hermes ``retry_utils.jittered_backoff``.  Uses decorrelated
    jitter so concurrent sessions hitting the same rate-limited Telegram slot
    don't all retry at exactly the same instant (thundering-herd prevention).

    Args:
        attempt: 1-based retry attempt number.
        base_delay: Base delay in seconds for attempt 1.  Defaults to 1 s
            (Telegram's typical ``retry_after`` minimum).
        max_delay: Upper cap in seconds.
        jitter_ratio: Fraction of computed delay used as random jitter range.
            0.5 means jitter is uniform in ``[0, 0.5 * delay]``.

    Returns:
        Delay in seconds: ``min(base * 2^(attempt-1), max_delay) + jitter``.

    Example::

        delays = [jittered_backoff(i) for i in range(1, 5)]
        # e.g. [1.3, 2.7, 5.1, 10.6]
    """
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    # Seed from monotonic nanoseconds + counter for decorrelation even
    # when the system clock has coarse resolution.
    seed = (time.monotonic_ns() ^ (tick * 0x9E3779B9)) & 0xFFFF_FFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)

    return delay + jitter
