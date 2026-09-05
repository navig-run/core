"""Did a ``notify.dispatch`` fan-out actually reach anyone?

Deliberately dependency-free (typing only). Every producer and monitor that latches
state on "I told the operator" needs this, so it cannot live in a package whose
import pulls in the router — ``navig.notify.__init__`` imports ``router``, which
imports prefs/feed/stores. It used to live in ``navig.notify.monitors``, which made
the dependency read backwards once a *producer* needed it too.
"""

from __future__ import annotations

from typing import Any


def all_channels_failed(outcome: dict[str, Any] | None) -> bool:
    """True when a ``notify.dispatch`` delivery was ATTEMPTED to ≥1 channel and
    every one FAILED — a real delivery failure worth retrying.

    An EMPTY channel list is NOT a failure: it means the fan-out intentionally
    delivered to nothing (master-off / muted / no enabled channels), which a
    producer must NOT churn on. Callers latch a flag (``alerted``, ``announced``, a
    throttle reservation) on a state change and undo it when this returns True, so
    the next occurrence re-fires instead of being silently swallowed (dispatch never
    raises — a down channel comes back as ``{ok: False}``, not an exception).
    """
    channels = (outcome or {}).get("channels") or []
    return bool(channels) and not any(c.get("ok") for c in channels)
