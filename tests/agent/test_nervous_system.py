"""
Tests for navig.agent.nervous_system
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from navig.agent.nervous_system import (
    Event,
    EventEmitter,
    EventPriority,
    EventType,
    NervousSystem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_ns() -> NervousSystem:
    return NervousSystem()


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------


class TestEvent:
    def test_fields(self):
        e = Event(type=EventType.SYSTEM_INFO, source="test")
        assert e.type == EventType.SYSTEM_INFO
        assert e.source == "test"
        assert e.data == {}
        assert e.priority == EventPriority.NORMAL
        assert len(e.id) > 0

    def test_to_dict(self):
        e = Event(type=EventType.HEARTBEAT, source="heart", data={"beat": 1})
        d = e.to_dict()
        assert d["type"] == "HEARTBEAT"
        assert d["source"] == "heart"
        assert d["data"] == {"beat": 1}
        assert d["priority"] == "NORMAL"
        assert "timestamp" in d
        assert "id" in d

    def test_repr_contains_type(self):
        e = Event(type=EventType.SYSTEM_ERROR, source="x")
        r = repr(e)
        assert "SYSTEM_ERROR" in r
        assert "x" in r

    def test_unique_ids(self):
        ids = {Event(type=EventType.CUSTOM, source="s").id for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# EventPriority ordering
# ---------------------------------------------------------------------------


def test_event_priority_values():
    assert EventPriority.LOW.value < EventPriority.NORMAL.value
    assert EventPriority.NORMAL.value < EventPriority.HIGH.value
    assert EventPriority.HIGH.value < EventPriority.CRITICAL.value


# ---------------------------------------------------------------------------
# NervousSystem.subscribe / unsubscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_subscribe_adds_handler(self):
        ns = _make_ns()
        handler = MagicMock()
        ns.subscribe(EventType.HEARTBEAT, handler)
        assert EventType.HEARTBEAT in ns._handlers
        assert handler in ns._handlers[EventType.HEARTBEAT]

    def test_subscribe_all_adds_global_handler(self):
        ns = _make_ns()
        handler = MagicMock()
        ns.subscribe_all(handler)
        assert handler in ns._global_handlers

    def test_unsubscribe_removes_handler(self):
        ns = _make_ns()
        handler = MagicMock()
        ns.subscribe(EventType.HEARTBEAT, handler)
        ns.unsubscribe(EventType.HEARTBEAT, handler)
        assert handler not in ns._handlers.get(EventType.HEARTBEAT, [])

    def test_unsubscribe_nonexistent_is_noop(self):
        ns = _make_ns()
        handler = MagicMock()
        ns.unsubscribe(EventType.HEARTBEAT, handler)  # no-op, should not raise

    def test_unsubscribe_all_removes_global(self):
        ns = _make_ns()
        handler = MagicMock()
        ns.subscribe_all(handler)
        ns.unsubscribe_all(handler)
        assert handler not in ns._global_handlers

    def test_unsubscribe_all_nonexistent_is_noop(self):
        ns = _make_ns()
        ns.unsubscribe_all(MagicMock())  # should not raise


# ---------------------------------------------------------------------------
# NervousSystem.emit / dispatch
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_returns_event(self):
        ns = _make_ns()
        result = _run(ns.emit(EventType.CUSTOM, "test"))
        assert isinstance(result, Event)
        assert result.type == EventType.CUSTOM
        assert result.source == "test"

    def test_emit_calls_type_handler(self):
        ns = _make_ns()
        received = []
        ns.subscribe(EventType.LOG_ENTRY, received.append)
        _run(ns.emit(EventType.LOG_ENTRY, "logger", {"msg": "hello"}))
        assert len(received) == 1
        assert received[0].data == {"msg": "hello"}

    def test_emit_calls_global_handler(self):
        ns = _make_ns()
        received = []
        ns.subscribe_all(received.append)
        _run(ns.emit(EventType.HEARTBEAT, "heart"))
        assert len(received) == 1

    def test_emit_calls_both_type_and_global_handlers(self):
        ns = _make_ns()
        global_received = []
        type_received = []
        ns.subscribe_all(global_received.append)
        ns.subscribe(EventType.HEARTBEAT, type_received.append)
        _run(ns.emit(EventType.HEARTBEAT, "heart"))
        assert len(global_received) == 1
        assert len(type_received) == 1

    def test_emit_stores_event_in_history(self):
        ns = _make_ns()
        _run(ns.emit(EventType.CUSTOM, "src"))
        assert len(ns._event_history) == 1

    def test_handler_exception_does_not_propagate(self):
        ns = _make_ns()

        def bad_handler(e):
            raise RuntimeError("boom")

        ns.subscribe(EventType.CUSTOM, bad_handler)
        # should not raise
        _run(ns.emit(EventType.CUSTOM, "resilience"))

    def test_async_handler_is_awaited(self):
        ns = _make_ns()
        received = []

        async def async_handler(e):
            received.append(e)

        ns.subscribe(EventType.CUSTOM, async_handler)
        _run(ns.emit(EventType.CUSTOM, "src"))
        assert len(received) == 1

    def test_history_capped_at_max(self):
        ns = _make_ns()
        ns._max_history = 5
        for _ in range(10):
            _run(ns.emit(EventType.CUSTOM, "src"))
        assert len(ns._event_history) <= 5


# ---------------------------------------------------------------------------
# NervousSystem.pause / resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_paused_events_queued_not_dispatched(self):
        ns = _make_ns()
        received = []
        ns.subscribe(EventType.CUSTOM, received.append)
        ns.pause()
        _run(ns.emit(EventType.CUSTOM, "src"))
        assert len(received) == 0  # not dispatched yet
        assert ns._pending_events.qsize() == 1

    def test_resume_dispatches_queued_events(self):
        ns = _make_ns()
        received = []
        ns.subscribe(EventType.CUSTOM, received.append)
        ns.pause()
        _run(ns.emit(EventType.CUSTOM, "src"))
        _run(ns.resume())
        assert len(received) == 1

    def test_resume_clears_queue(self):
        ns = _make_ns()
        ns.pause()
        _run(ns.emit(EventType.CUSTOM, "src"))
        _run(ns.resume())
        assert ns._pending_events.empty()


# ---------------------------------------------------------------------------
# NervousSystem.get_history
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_filter_by_type(self):
        ns = _make_ns()
        _run(ns.emit(EventType.HEARTBEAT, "s"))
        _run(ns.emit(EventType.LOG_ENTRY, "s"))
        events = ns.get_history(event_type=EventType.HEARTBEAT)
        assert all(e.type == EventType.HEARTBEAT for e in events)

    def test_filter_by_source(self):
        ns = _make_ns()
        _run(ns.emit(EventType.CUSTOM, "alice"))
        _run(ns.emit(EventType.CUSTOM, "bob"))
        events = ns.get_history(source="alice")
        assert all(e.source == "alice" for e in events)

    def test_limit(self):
        ns = _make_ns()
        for _ in range(10):
            _run(ns.emit(EventType.CUSTOM, "s"))
        assert len(ns.get_history(limit=3)) == 3

    def test_clear_history(self):
        ns = _make_ns()
        _run(ns.emit(EventType.CUSTOM, "s"))
        ns.clear_history()
        assert ns.get_history() == []


# ---------------------------------------------------------------------------
# NervousSystem.get_stats / list_subscriptions
# ---------------------------------------------------------------------------


class TestStats:
    def test_get_stats_structure(self):
        ns = _make_ns()
        _run(ns.emit(EventType.CUSTOM, "src"))
        stats = ns.get_stats()
        assert "total_events" in stats
        assert stats["total_events"] == 1
        assert "events_by_type" in stats
        assert "CUSTOM" in stats["events_by_type"]

    def test_list_subscriptions_empty(self):
        ns = _make_ns()
        assert ns.list_subscriptions() == {}

    def test_list_subscriptions_counts(self):
        ns = _make_ns()
        ns.subscribe(EventType.HEARTBEAT, MagicMock())
        ns.subscribe(EventType.HEARTBEAT, MagicMock())
        result = ns.list_subscriptions()
        assert result["HEARTBEAT"] == 2


# ---------------------------------------------------------------------------
# EventEmitter
# ---------------------------------------------------------------------------


class TestEventEmitter:
    def test_emit_without_nervous_system_returns_none(self):
        emitter = EventEmitter("widget")
        result = _run(emitter.emit(EventType.CUSTOM))
        assert result is None

    def test_emit_with_nervous_system_returns_event(self):
        ns = _make_ns()
        emitter = EventEmitter("widget", ns)
        result = _run(emitter.emit(EventType.CUSTOM, data={"x": 1}))
        assert isinstance(result, Event)
        assert result.source == "widget"

    def test_set_nervous_system(self):
        ns = _make_ns()
        emitter = EventEmitter("widget")
        emitter.set_nervous_system(ns)
        result = _run(emitter.emit(EventType.CUSTOM))
        assert isinstance(result, Event)

    def test_emit_info_uses_system_info_type(self):
        ns = _make_ns()
        emitter = EventEmitter("widget", ns)
        result = _run(emitter.emit_info("all good"))
        assert result.type == EventType.SYSTEM_INFO

    def test_emit_warning_uses_high_priority(self):
        ns = _make_ns()
        emitter = EventEmitter("widget", ns)
        result = _run(emitter.emit_warning("slow"))
        assert result.priority == EventPriority.HIGH

    def test_emit_error_uses_critical_priority(self):
        ns = _make_ns()
        emitter = EventEmitter("widget", ns)
        result = _run(emitter.emit_error("crash", error=ValueError("bad")))
        assert result.priority == EventPriority.CRITICAL
        assert result.data["error"] == "bad"
