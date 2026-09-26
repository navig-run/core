"""A proactive cooldown must hold across PROCESSES, not just within one.

Measured on the operator's own daemon — two greetings three seconds apart, both
`state=just_arrived`, against a 12-hour cooldown:

    21:28:10 [navig.agent.proactive.engagement] Proactive engagement: greeting
    21:28:13 [navig.agent.proactive.engagement] Proactive engagement: greeting

`get_engagement_coordinator` is a "process-level singleton" over a module global.
It protects the two in-process callers (`proactive/engine.py` and
`gateway/notifications.py`) from each other, and protects nothing from a second
interpreter — and the supervisor spawns `navig.daemon.telegram_worker` alongside
`navig gateway start`, each building its own `NavigGateway`.

The write side was already correct (`record_proactive_event` persists at once);
the read side loaded once, in `__init__`, so each process answered from the
snapshot it took at its own startup.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.agent.proactive.user_state import UserStateTracker

#: Every event type with its own cooldown bucket. Greeting is the one that was
#: observed firing twice, but the staleness belongs to the read path, so it
#: applied to all six equally.
EVENT_TYPES = [
    "greeting",
    "checkin",
    "capability_promo",
    "feedback_ask",
    "idle_nudge",
    "wrapup",
]


def _state_file(d: Path) -> Path:
    return d / "user_state.json"


def test_a_second_process_sees_the_first_process_greeting(tmp_path: Path) -> None:
    """The bug, stated directly.

    Two trackers over one state dir are two processes: `_COORDINATORS` is a module
    global, so a second interpreter starts with an empty one.
    """
    gateway = UserStateTracker(state_dir=tmp_path)
    worker = UserStateTracker(state_dir=tmp_path)

    # Both booted before anything was sent, which is the real sequence.
    assert gateway.hours_since("greeting") is None
    assert worker.hours_since("greeting") is None

    gateway.record_proactive_event("greeting")

    since = worker.hours_since("greeting")
    assert since is not None, (
        "the second process still sees no greeting, so it would greet again — "
        "this is the 21:28:10 / 21:28:13 duplicate"
    )
    assert since < 1.0


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_every_cooldown_bucket_crosses_the_boundary(tmp_path: Path, event_type: str) -> None:
    """Not just greeting: the gap was in the shared read path."""
    a = UserStateTracker(state_dir=tmp_path)
    b = UserStateTracker(state_dir=tmp_path)
    assert b.hours_since(event_type) is None

    a.record_proactive_event(event_type)

    assert b.hours_since(event_type) is not None, f"{event_type} did not cross"


def test_a_timestamp_never_moves_backwards(tmp_path: Path) -> None:
    """The property that makes reading another process's file safe.

    A cooldown that moved BACKWARDS would cause *more* messages — the exact
    failure this fix exists to prevent. So the refresh takes the max of memory and
    disk rather than adopting whatever disk says.
    """
    tracker = UserStateTracker(state_dir=tmp_path)
    tracker.record_proactive_event("greeting")
    recent = tracker.stats.last_greeting
    assert recent is not None

    # Someone else's older state lands on disk (a slow writer, a restored backup).
    stale = recent - 6 * 3600
    _state_file(tmp_path).write_text(
        json.dumps({"stats": {"last_greeting": stale}}), encoding="utf-8"
    )

    tracker.hours_since("greeting")
    assert tracker.stats.last_greeting == recent, (
        "an older on-disk timestamp overwrote a newer in-memory one — the cooldown "
        "moved backwards and the operator gets a second message"
    )


def test_an_unreadable_state_file_changes_nothing(tmp_path: Path) -> None:
    """⚠ A failed READ must never become a destructive WRITE.

    This module's own `_load_state` docstring records that incident: a transient
    failure left a factory-fresh tracker whose next save wrote defaults over the
    operator's real `autonomy_level` and `quiet_hours_*`. The refresh must not
    reopen it — it touches the six timestamps and nothing else.
    """
    tracker = UserStateTracker(state_dir=tmp_path)
    tracker.record_proactive_event("greeting")
    before = tracker.stats.last_greeting
    autonomy_before = tracker.preferences.autonomy_level

    _state_file(tmp_path).write_text("{{ not json", encoding="utf-8")

    assert tracker.hours_since("greeting") is not None
    assert tracker.stats.last_greeting == before, "a bad read cleared a real timestamp"
    assert tracker.preferences.autonomy_level == autonomy_before, (
        "the refresh reached preferences; it must read only the timestamps"
    )


def test_a_missing_state_file_changes_nothing(tmp_path: Path) -> None:
    """The first run of a brand-new install: nothing on disk, nothing to adopt."""
    tracker = UserStateTracker(state_dir=tmp_path)
    tracker.stats.last_greeting = time.time()
    kept = tracker.stats.last_greeting

    _state_file(tmp_path).unlink(missing_ok=True)

    assert tracker.hours_since("greeting") is not None
    assert tracker.stats.last_greeting == kept


def test_a_later_send_is_adopted_after_an_earlier_one(tmp_path: Path) -> None:
    """The refresh is cached on (mtime, size); a genuine second write must land.

    A cache that went stale would restore the original bug a few seconds later,
    which is precisely the window that matters here.
    """
    a = UserStateTracker(state_dir=tmp_path)
    b = UserStateTracker(state_dir=tmp_path)

    a.record_proactive_event("greeting")
    first = b.hours_since("greeting")
    assert first is not None

    # A real later send, far enough forward to be unambiguous.
    a.stats.last_greeting = time.time() + 3600
    a._save_state()

    b.hours_since("greeting")
    assert b.stats.last_greeting == a.stats.last_greeting, (
        "the second write was not adopted — the refresh cache went stale"
    )


def test_non_numeric_timestamps_are_ignored(tmp_path: Path) -> None:
    """A hand-edited or half-migrated file must not crash a heartbeat."""
    tracker = UserStateTracker(state_dir=tmp_path)
    tracker.record_proactive_event("greeting")
    before = tracker.stats.last_greeting

    _state_file(tmp_path).write_text(
        json.dumps({"stats": {"last_greeting": "yesterday", "last_checkin": None}}),
        encoding="utf-8",
    )

    assert tracker.hours_since("greeting") is not None
    assert tracker.stats.last_greeting == before
    assert tracker.hours_since("checkin") is None


def test_two_writes_inside_one_clock_tick_are_both_adopted(tmp_path: Path) -> None:
    """The `(mtime, size)` stamp cannot tell two writes apart until the clock moves.

    The flake this pins: `test_a_later_send_is_adopted_after_an_earlier_one` went red
    under load with "the refresh cache went stale". A second same-length write inside
    one timestamp tick (Windows: up to 15.6ms) leaves the stamp identical, so the
    reader kept the first value. Reproduced here deterministically by pinning the
    second file's mtime to the first's, the way a coarse clock would.
    """
    import os

    a = UserStateTracker(state_dir=tmp_path)
    b = UserStateTracker(state_dir=tmp_path)

    a.stats.last_greeting = 1_800_000_000.123456
    a._save_state()
    st1 = _state_file(tmp_path).stat()
    assert b.hours_since("greeting") is not None
    assert b.stats.last_greeting == 1_800_000_000.123456

    a.stats.last_greeting = 1_800_000_000.654321  # same repr length → same size
    a._save_state()
    os.utime(_state_file(tmp_path), ns=(st1.st_atime_ns, st1.st_mtime_ns))  # same tick
    st2 = _state_file(tmp_path).stat()
    assert (st1.st_mtime, st1.st_size) == (st2.st_mtime, st2.st_size), "the stamp is identical"

    b.hours_since("greeting")
    assert b.stats.last_greeting == 1_800_000_000.654321


def test_a_settled_file_is_served_from_the_stamp(tmp_path: Path, monkeypatch) -> None:
    """The window above is not a licence to re-read forever: once the file is older
    than any timestamp granularity, an unchanged stamp means no read at all."""
    import os

    from navig.agent.proactive import user_state as mod

    a = UserStateTracker(state_dir=tmp_path)
    a.record_proactive_event("greeting")
    f = _state_file(tmp_path)
    old = time.time() - 60
    os.utime(f, (old, old))

    b = UserStateTracker(state_dir=tmp_path)
    b.hours_since("greeting")  # first read stamps the file

    reads = 0
    real_read_text = Path.read_text

    def counting(self, *args, **kwargs):
        nonlocal reads
        if self == f:
            reads += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    for _ in range(5):
        b.hours_since("greeting")
    assert reads == 0, "an unchanged, settled stamp must not re-read the file"
    assert (time.time() - f.stat().st_mtime) > mod._STAMP_SETTLE_SECONDS
