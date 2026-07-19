"""Regression: the TUI blackbox badge reads the real recorder.

`resolve_blackbox` / `_count_recent_errors` imported a never-existent
`BlackboxTimeline` class, so the badge always showed "no timeline" and the
gateway error count was always 0. They now read via
`navig.blackbox.recorder.get_recorder().read_events(...)`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import navig.blackbox.recorder as rec_mod
from navig.blackbox.types import BlackboxEvent, EventType
from navig.tui.resolvers import _count_recent_errors, resolve_blackbox


def _ev(event_type=EventType.COMMAND, *, age_s=1, tags=None, payload=None):
    return BlackboxEvent(
        id="x",
        event_type=event_type,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        payload=payload or {},
        tags=tags or [],
    )


class _Rec:
    def __init__(self, events):
        self._events = events

    def read_events(self, limit=500, **kw):
        return self._events[:limit]


def test_resolve_blackbox_shows_recent_ops(monkeypatch):
    events = [_ev(EventType.COMMAND, age_s=2), _ev(EventType.OUTPUT, age_s=30)]
    monkeypatch.setattr(rec_mod, "get_recorder", lambda *a, **k: _Rec(events))

    badge = resolve_blackbox()

    assert badge.status == "ok"
    assert "2 recent ops" in badge.detail
    assert f"last: {EventType.COMMAND.value}" in badge.detail  # newest event first


def test_resolve_blackbox_empty(monkeypatch):
    monkeypatch.setattr(rec_mod, "get_recorder", lambda *a, **k: _Rec([]))

    badge = resolve_blackbox()

    assert badge.status == "ok"
    assert "no ops recorded" in badge.detail


def test_count_recent_errors_counts_error_and_crash_in_window(monkeypatch):
    events = [
        _ev(EventType.ERROR, age_s=10, tags=["gateway"]),
        _ev(EventType.CRASH, age_s=20, payload={"component": "gateway"}),
        _ev(EventType.ERROR, age_s=10, tags=["other"]),  # wrong category
        _ev(EventType.COMMAND, age_s=10, tags=["gateway"]),  # not an error
        _ev(EventType.ERROR, age_s=99999, tags=["gateway"]),  # outside the window
    ]
    monkeypatch.setattr(rec_mod, "get_recorder", lambda *a, **k: _Rec(events))

    assert _count_recent_errors("gateway", window_seconds=3600) == 2


def test_count_recent_errors_zero_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(rec_mod, "get_recorder", lambda *a, **k: _Rec([_ev(EventType.COMMAND)]))

    assert _count_recent_errors("gateway") == 0


def test_phantom_timeline_class_is_gone():
    import navig.blackbox.timeline as tl_mod
    from navig.blackbox.recorder import get_recorder  # noqa: F401

    assert not hasattr(tl_mod, "BlackboxTimeline")
