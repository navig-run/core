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

__all__ = ["NAVIG_BOOT_MESSAGES", "get_boot_message"]

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
