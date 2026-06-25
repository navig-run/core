"""Sample notifications for the deck "Test" buttons — fire one representative
event per monitor/producer so you can prove the whole pipe (type → matrix →
deck/Telegram) without waiting for a real crash, outage, or camera access.

Test notifications are clearly labelled ``[Test]`` and carry ``data._test=True``.
"""

from __future__ import annotations

from typing import Any

# monitor key → (notify_type, title, body, priority)
_SAMPLES: dict[str, tuple[str, str, str, str]] = {
    "self_errors": ("self_error", "[Test] NAVIG error", "A sample self-error notification.", "high"),
    "connectivity": ("connectivity", "[Test] Brain reachability", "A sample connectivity notification.", "normal"),
    "resources": ("system_alert", "[Test] Resource alert", "A sample disk/CPU/memory alert.", "normal"),
    "webcam": ("webcam_on", "[Test] Webcam in use", "A sample webcam-in-use notification.", "high"),
}


def monitor_sample(name: str) -> tuple[str, str, str, str] | None:
    return _SAMPLES.get(name)


async def dispatch_monitor_test(name: str) -> dict[str, Any] | None:
    """Fire the sample notification for *name*; returns the dispatch result."""
    sample = _SAMPLES.get(name)
    if sample is None:
        return None
    notify_type, title, body, priority = sample
    from navig.notify import dispatch

    return await dispatch(
        notify_type, title, body, priority=priority, data={"_test": True, "monitor": name}
    )
