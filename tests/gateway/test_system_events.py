"""
Tests for navig.gateway.system_events — SystemEvent dataclass and EventPriority.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from navig.gateway.system_events import EventPriority, SystemEvent, SystemEventQueue


# ─── EventPriority ────────────────────────────────────────────────────────────


def test_event_priority_values():
    assert EventPriority.LOW.value < EventPriority.NORMAL.value
    assert EventPriority.NORMAL.value < EventPriority.HIGH.value
    assert EventPriority.HIGH.value < EventPriority.CRITICAL.value


def test_event_priority_names():
    for name in ("LOW", "NORMAL", "HIGH", "CRITICAL"):
        assert EventPriority[name].name == name


# ─── SystemEvent ──────────────────────────────────────────────────────────────


def _make_event(**overrides) -> SystemEvent:
    defaults = dict(
        id="evt_1",
        event_type="test.event",
        payload={"key": "value"},
        priority=EventPriority.NORMAL,
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        processed=False,
        error=None,
    )
    defaults.update(overrides)
    return SystemEvent(**defaults)


def test_system_event_to_dict_basic():
    evt = _make_event()
    d = evt.to_dict()
    assert d["id"] == "evt_1"
    assert d["event_type"] == "test.event"
    assert d["payload"] == {"key": "value"}
    assert d["priority"] == "NORMAL"
    assert d["processed"] is False
    assert d["error"] is None
    assert "timestamp" in d


def test_system_event_to_dict_timestamp_iso():
    ts = datetime(2024, 6, 1, 8, 30, 0)
    evt = _make_event(timestamp=ts)
    d = evt.to_dict()
    assert d["timestamp"] == ts.isoformat()


@pytest.mark.parametrize("priority", list(EventPriority))
def test_system_event_to_dict_all_priorities(priority):
    evt = _make_event(priority=priority)
    d = evt.to_dict()
    assert d["priority"] == priority.name


def test_system_event_from_dict_roundtrip():
    original = _make_event(
        id="evt_99",
        event_type="host.reboot",
        payload={"host": "prod-01"},
        priority=EventPriority.HIGH,
        processed=True,
        error="timeout",
    )
    d = original.to_dict()
    restored = SystemEvent.from_dict(d)
    assert restored.id == original.id
    assert restored.event_type == original.event_type
    assert restored.payload == original.payload
    assert restored.priority == original.priority
    assert restored.processed == original.processed
    assert restored.error == original.error
    assert restored.timestamp == original.timestamp


def test_system_event_from_dict_missing_optional_fields():
    data = {
        "id": "evt_2",
        "event_type": "minimal",
        "payload": {},
        "priority": "LOW",
        "timestamp": datetime(2024, 1, 1).isoformat(),
    }
    evt = SystemEvent.from_dict(data)
    assert evt.processed is False
    assert evt.error is None


def test_system_event_from_dict_critical_priority():
    data = {
        "id": "evt_crit",
        "event_type": "alert",
        "payload": {"severity": "high"},
        "priority": "CRITICAL",
        "timestamp": datetime(2024, 3, 10).isoformat(),
        "processed": False,
    }
    evt = SystemEvent.from_dict(data)
    assert evt.priority == EventPriority.CRITICAL


# ─── SystemEventQueue ─────────────────────────────────────────────────────────


def test_system_event_queue_init_empty(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path)
    assert q.storage_path == tmp_path
    assert q.max_history == 1000
    assert len(q._pending) == 0
    assert not q._running


def test_system_event_queue_custom_max_history(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path, max_history=50)
    assert q.max_history == 50


def test_system_event_queue_generate_id_increments(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path)
    id1 = q._generate_id()
    id2 = q._generate_id()
    assert id1 != id2
    assert id1.startswith("evt_")
    assert id2.startswith("evt_")


def test_system_event_queue_save_and_load(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path)
    evt = _make_event(id="evt_persist", event_type="persist.test")
    q._pending[evt.id] = evt
    q._save_events()

    # New queue loads from same path
    q2 = SystemEventQueue(storage_path=tmp_path)
    assert "evt_persist" in q2._pending


def test_system_event_queue_subscribe_stores_handler(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path)

    calls = []

    async def handler(event):
        calls.append(event)

    q._subscribers["my.event"] = [handler]
    assert handler in q._subscribers["my.event"]


def test_system_event_queue_get_events_path(tmp_path):
    q = SystemEventQueue(storage_path=tmp_path)
    p = q._get_events_path()
    assert p.parent == tmp_path
    assert p.name == "events.json"
