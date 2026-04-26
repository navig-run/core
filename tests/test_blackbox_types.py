"""Hermetic unit tests for navig.blackbox.types."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from navig.blackbox.types import (
    _SEVERITY,
    BlackboxEvent,
    Bundle,
    EventType,
)

# ---------------------------------------------------------------------------
# EventType
# ---------------------------------------------------------------------------


class TestEventType:
    def test_all_members_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)

    def test_severity_covers_all_event_types(self):
        for et in EventType:
            assert et in _SEVERITY

    def test_crash_has_highest_severity(self):
        max_sev = max(_SEVERITY.values())
        assert _SEVERITY[EventType.CRASH] == max_sev

    def test_system_has_lowest_severity(self):
        min_sev = min(_SEVERITY.values())
        assert _SEVERITY[EventType.SYSTEM] == min_sev


# ---------------------------------------------------------------------------
# BlackboxEvent.create
# ---------------------------------------------------------------------------


class TestBlackboxEventCreate:
    def test_create_returns_event(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {"cmd": "navig run ls"})
        assert isinstance(ev, BlackboxEvent)

    def test_id_is_8_chars(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {})
        assert len(ev.id) == 8

    def test_timestamp_is_utc(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {})
        assert ev.timestamp.tzinfo is not None

    def test_default_tags_empty(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {})
        assert ev.tags == []

    def test_tags_passthrough(self):
        ev = BlackboxEvent.create(EventType.WARNING, {}, tags=["cli", "deploy"])
        assert ev.tags == ["cli", "deploy"]

    def test_custom_source(self):
        ev = BlackboxEvent.create(EventType.SYSTEM, {}, source="daemon")
        assert ev.source == "daemon"

    def test_default_source(self):
        ev = BlackboxEvent.create(EventType.SYSTEM, {})
        assert ev.source == "navig"


# ---------------------------------------------------------------------------
# BlackboxEvent.to_json
# ---------------------------------------------------------------------------


class TestBlackboxEventToJson:
    def test_to_json_is_valid_json(self):
        ev = BlackboxEvent.create(EventType.CRASH, {"message": "boom"})
        raw = ev.to_json()
        parsed = json.loads(raw)
        assert parsed["event_type"] == "crash"

    def test_to_json_contains_id(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {})
        data = json.loads(ev.to_json())
        assert "id" in data
        assert data["id"] == ev.id

    def test_to_json_payload_preserved(self):
        ev = BlackboxEvent.create(EventType.OUTPUT, {"lines": ["a", "b"]})
        data = json.loads(ev.to_json())
        assert data["payload"] == {"lines": ["a", "b"]}

    def test_to_json_no_spaces(self):
        ev = BlackboxEvent.create(EventType.COMMAND, {})
        raw = ev.to_json()
        # Check compact separators — no ": " or ", " 
        assert ": " not in raw
        assert ", " not in raw


# ---------------------------------------------------------------------------
# BlackboxEvent.from_dict
# ---------------------------------------------------------------------------


class TestBlackboxEventFromDict:
    def _sample_dict(self) -> dict:
        return {
            "id": "abcd1234",
            "event_type": "command",
            "timestamp": "2024-06-01T12:00:00+00:00",
            "payload": {"cmd": "navig status"},
            "tags": ["tag1"],
            "source": "test",
        }

    def test_from_dict_basic(self):
        ev = BlackboxEvent.from_dict(self._sample_dict())
        assert ev.id == "abcd1234"
        assert ev.event_type == EventType.COMMAND
        assert ev.source == "test"

    def test_from_dict_default_source(self):
        d = self._sample_dict()
        del d["source"]
        ev = BlackboxEvent.from_dict(d)
        assert ev.source == "navig"

    def test_from_dict_default_tags(self):
        d = self._sample_dict()
        del d["tags"]
        ev = BlackboxEvent.from_dict(d)
        assert ev.tags == []

    def test_round_trip(self):
        ev = BlackboxEvent.create(EventType.ERROR, {"msg": "err"}, tags=["x"])
        restored = BlackboxEvent.from_dict(json.loads(ev.to_json()))
        assert restored.event_type == ev.event_type
        assert restored.payload == ev.payload
        assert restored.tags == ev.tags


# ---------------------------------------------------------------------------
# BlackboxEvent.severity
# ---------------------------------------------------------------------------


class TestBlackboxEventSeverity:
    def test_crash_severity_greater_than_error(self):
        crash = BlackboxEvent.create(EventType.CRASH, {})
        error = BlackboxEvent.create(EventType.ERROR, {})
        assert crash.severity() > error.severity()

    def test_error_severity_greater_than_warning(self):
        error = BlackboxEvent.create(EventType.ERROR, {})
        warn = BlackboxEvent.create(EventType.WARNING, {})
        assert error.severity() > warn.severity()

    def test_severity_returns_int(self):
        ev = BlackboxEvent.create(EventType.SESSION, {})
        assert isinstance(ev.severity(), int)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


class TestBundle:
    def _make_bundle(self) -> Bundle:
        return Bundle(
            id="b123",
            created_at=datetime.now(timezone.utc),
            navig_version="1.0.0",
            events=[
                BlackboxEvent.create(EventType.COMMAND, {}),
                BlackboxEvent.create(EventType.CRASH, {}),
            ],
            crash_reports=[{"msg": "crash1"}],
            log_tails={"app.log": "line1\nline2"},
            manifest_hash="abc123",
        )

    def test_event_count(self):
        b = self._make_bundle()
        assert b.event_count() == 2

    def test_crash_count(self):
        b = self._make_bundle()
        assert b.crash_count() == 1

    def test_compute_hash_returns_hex(self):
        h = Bundle.compute_hash(b"hello world")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_deterministic(self):
        assert Bundle.compute_hash(b"abc") == Bundle.compute_hash(b"abc")

    def test_compute_hash_different_inputs(self):
        assert Bundle.compute_hash(b"abc") != Bundle.compute_hash(b"def")

    def test_sealed_defaults_false(self):
        b = self._make_bundle()
        assert b.sealed is False
