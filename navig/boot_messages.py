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

import random
from typing import Optional

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
    location: Optional[str] = None,
    uptime: Optional[int] = None,
    signal_strength: Optional[int] = None,
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
    base = NAVIG_BOOT_MESSAGES[random.randrange(len(NAVIG_BOOT_MESSAGES))]

    extras: list[str] = [
        x
        for x in [
            f"Position: {location}." if location else None,
            f"Last session: {uptime}s." if uptime is not None else None,
            f"Signal: {signal_strength}%." if signal_strength is not None else None,
        ]
        if x is not None
    ]

    suffix = " · " + " ".join(extras) if extras else ""
    return f"{base}{suffix}"
