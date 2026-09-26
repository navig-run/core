"""
NAVIG Boot Message Variants

Rotating startup announcements used wherever NAVIG signals it has come online.
Every variant communicates exactly one thing — awake, operational, ready —
then adds one layer of personality.

Usage::

    from navig.boot_messages import get_boot_message

    msg = get_boot_message()
    # → "Online. Horizon locked. Waiting on your first move."

    # With optional runtime context:
    msg = get_boot_message(location="48.8566° N", uptime=3600)
    # → "... · Last session: 3600s."
"""

from __future__ import annotations

import logging

__all__ = [
    "NAVIG_BOOT_MESSAGES",
    "boot_greeting_enabled",
    "claim_boot_greeting",
    "get_boot_message",
]

logger = logging.getLogger(__name__)

NAVIG_BOOT_MESSAGES: list[str] = [
    "Awake. All systems breathing. Ready when you are.",
    "Back online. Sensors up, memory clear, clocks synced. Standing by.",
    "Good morning, navigator. Systems warm, heading null. Let's go somewhere.",
    "Initialization complete. No anomalies. The road is yours.",
    "Online. Horizon locked. Waiting on your first move.",
    "Diagnostics passed. Signal clean. I've been expecting you.",
    "Systems live. Position acquired. Time to move.",
    "Rebooted. Everything where it should be. Ready to navigate.",
    "Uptime: 0s. Confidence: full. Your turn.",
    "Came back clean. No errors, no ghosts. Online and sharp.",
]


def get_boot_message(
    *,
    location: str | None = None,
    uptime: int | None = None,
    signal_strength: int | None = None,
) -> str:
    """Return a randomised NAVIG boot message.

    Always signals: awake + operational + ready.
    Accepts optional runtime context to append useful live data.

    Args:
        location: Human-readable position string, e.g. ``"48.8566° N"``.
        uptime: Duration (seconds) of the previous session.
        signal_strength: Network/signal quality as a percentage (0–100).

    Returns:
        Formatted boot string, e.g.
        ``"Diagnostics passed. Signal clean. I've been expecting you."``
    """
    # Localized pool, with the English list above as the fallback — so a locale
    # file that has not been written yet degrades to English rather than to
    # silence. This used to read NAVIG_BOOT_MESSAGES directly, which is why the
    # operator saw "Systems live. Position acquired." with user.language=Russian.
    from navig.core import i18n  # noqa: PLC0415

    base = i18n.pick("boot.messages", fallback=NAVIG_BOOT_MESSAGES)

    extras: list[str] = [
        x
        for x in [
            i18n.t("boot.position", value=location) if location else None,
            i18n.t("boot.last_session", value=uptime) if uptime is not None else None,
            i18n.t("boot.signal", value=signal_strength)
            if signal_strength is not None
            else None,
        ]
        if x is not None
    ]

    suffix = " · " + " ".join(extras) if extras else ""
    return f"{base}{suffix}"


# ── "greet once per boot", across processes ──────────────────────────────────
#
# The supervisor runs a gateway AND a telegram_worker; each builds its own
# NavigGateway and therefore its own Telegram channel. A per-process flag
# ("announce once") is honoured faithfully by BOTH, which is how one restart
# produced two greetings — measured in the operator's own chat (19:10 ×2, and
# ×3 with the engagement greeting). The claim has to live where both can see it.

CFG_GREETING_ENABLED = "telegram.boot_greeting.enabled"
CFG_GREETING_DEDUPE = "telegram.boot_greeting.dedupe_sec"
#: Long enough to cover two siblings booting together (measured: same second) and
#: a supervisor's staggered restart, short enough that a genuine boot hours later
#: still says hello.
DEFAULT_DEDUPE_SEC = 300


def boot_greeting_enabled() -> bool:
    """Whether to greet on boot at all. Default on; a restart-heavy day is
    exactly when an operator wants to turn this off."""
    try:
        from navig.core import Config
        from navig.core.coerce import coerce_bool

        return coerce_bool(Config().get(CFG_GREETING_ENABLED, True), default=True)
    except Exception:  # noqa: BLE001
        return True


def _dedupe_sec() -> int:
    try:
        from navig.core import Config
        from navig.core.coerce import coerce_int

        return max(0, coerce_int(Config().get(CFG_GREETING_DEDUPE, DEFAULT_DEDUPE_SEC),
                                 default=DEFAULT_DEDUPE_SEC))
    except Exception:  # noqa: BLE001
        return DEFAULT_DEDUPE_SEC


def _marker_path():
    from navig.platform import paths

    return paths.cache_dir() / "boot_greeting.claim"


def claim_boot_greeting(*, now: float | None = None) -> bool:
    """Claim the right to greet, for this boot, across every navig process.

    True exactly once per dedupe window. Implemented as an atomic ``open(..., "x")``
    rather than read-then-write: two siblings start in the same second, so a
    check-then-act would let both through — the very race this exists to close. A
    marker older than the window is replaced, so the next real boot greets.

    Fails OPEN: if the marker cannot be written (read-only dir, odd permissions),
    greeting once too often is better than an install that never says hello."""
    import os
    import time

    ts = time.time() if now is None else now
    path = _marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(str(ts))
            return True
        except FileExistsError:
            try:
                age = ts - float(path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                age = float("inf")     # unreadable marker: treat as stale
            if age < _dedupe_sec():
                return False
            # Stale: replace it and greet. os.replace is atomic, so a sibling
            # arriving mid-swap sees either the old or the new marker, never a
            # half-written one.
            tmp = path.with_suffix(".claim.tmp")
            tmp.write_text(str(ts), encoding="utf-8")
            os.replace(tmp, path)
            return True
    except OSError:
        logger.debug("boot greeting claim could not be recorded; greeting anyway",
                     exc_info=True)
        return True
