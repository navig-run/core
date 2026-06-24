"""Hermetic unit tests for navig.gateway.billing_emitter."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from navig.gateway.billing_emitter import (
    _ACTION_MAP,
    BillingEmitter,
    _now_iso,
)

# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------


class TestNowIso:
    def test_returns_string(self):
        assert isinstance(_now_iso(), str)

    def test_format_matches_iso_pattern(self):
        ts = _now_iso()
        # e.g. 2026-02-23T12:00:00.000Z
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)

    def test_ends_with_z(self):
        assert _now_iso().endswith("Z")

    def test_milliseconds_three_digits(self):
        ts = _now_iso()
        # .XXXz — exactly 3 digits before Z
        assert re.search(r"\.\d{3}Z$", ts)


# ---------------------------------------------------------------------------
# _ACTION_MAP
# ---------------------------------------------------------------------------


class TestActionMap:
    def test_is_dict(self):
        assert isinstance(_ACTION_MAP, dict)

    def test_mission_create(self):
        event_type, units = _ACTION_MAP["mission.create"]
        assert event_type == "mission.create"
        assert units == 1

    def test_formation_start_two_units(self):
        _, units = _ACTION_MAP["formation.start"]
        assert units == 2

    def test_daemon_stop_zero_units(self):
        _, units = _ACTION_MAP["daemon.stop"]
        assert units == 0

    def test_all_entries_have_two_elements(self):
        for key, val in _ACTION_MAP.items():
            assert len(val) == 2, f"{key} should have (event_type, units)"

    def test_all_units_are_int(self):
        for key, (_, units) in _ACTION_MAP.items():
            assert isinstance(units, int), f"{key} units must be int"


# ---------------------------------------------------------------------------
# BillingEmitter
# ---------------------------------------------------------------------------


class TestBillingEmitterEmit:
    def test_creates_file_on_emit(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="telegram:123", action="mission.create")
        assert log.exists()

    def test_file_contains_valid_json(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="telegram:123", action="mission.create")
        line = log.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert isinstance(record, dict)

    def test_record_has_required_fields(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="telegram:123", action="mission.create")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        for field in ("ts", "actor", "action", "event_type", "units"):
            assert field in record, f"Missing field: {field}"

    def test_actor_stored_correctly(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="telegram:999", action="run.shell")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["actor"] == "telegram:999"

    def test_action_stored_correctly(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="task.add")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["action"] == "task.add"

    def test_known_action_maps_event_type(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="formation.start")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "formation.start"
        assert record["units"] == 2

    def test_unknown_action_defaults(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="custom.unknown.xyz")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "custom.unknown.xyz"
        assert record["units"] == 1

    def test_metadata_included_when_provided(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="run.shell", metadata={"host": "prod"})
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["metadata"] == {"host": "prod"}

    def test_metadata_omitted_when_none(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="run.shell")
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert "metadata" not in record

    def test_multiple_emits_append_lines(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="mission.create")
        emitter.emit(actor="u", action="task.add")
        lines = [l for l in log.read_text(encoding="utf-8").strip().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_creates_parent_dirs(self, tmp_path):
        log = tmp_path / "deep" / "nested" / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="u", action="run.shell")
        assert log.exists()


class TestBillingEmitterTail:
    def test_tail_returns_empty_list_if_no_file(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        assert emitter.tail() == []

    def test_tail_returns_all_records(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        for i in range(5):
            emitter.emit(actor="u", action="run.shell")
        records = emitter.tail(50)
        assert len(records) == 5

    def test_tail_limits_to_n(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        for i in range(10):
            emitter.emit(actor="u", action="run.shell")
        records = emitter.tail(3)
        assert len(records) == 3

    def test_tail_skips_malformed_lines(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        log.write_text("not-json\n{\"ts\":\"x\",\"actor\":\"u\",\"action\":\"a\",\"event_type\":\"a\",\"units\":1}\n", encoding="utf-8")
        emitter = BillingEmitter(log_path=log)
        records = emitter.tail()
        assert len(records) == 1

    def test_tail_returns_oldest_first(self, tmp_path):
        log = tmp_path / "billing.jsonl"
        emitter = BillingEmitter(log_path=log)
        emitter.emit(actor="first", action="run.shell")
        emitter.emit(actor="second", action="task.add")
        records = emitter.tail(2)
        assert records[0]["actor"] == "first"
        assert records[1]["actor"] == "second"
