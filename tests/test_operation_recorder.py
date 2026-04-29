"""
Batch 82 — navig/operation_recorder.py
Tests for OperationRecord, OperationType, OperationStatus, OperationRecorder.
"""
import json
from pathlib import Path

import pytest

from navig.operation_recorder import (
    OperationRecord,
    OperationRecorder,
    OperationStatus,
    OperationType,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestOperationTypeEnum:
    def test_remote_command_value(self):
        assert OperationType.REMOTE_COMMAND == "remote_command"

    def test_file_create_value(self):
        assert OperationType.FILE_CREATE == "file_create"

    def test_database_query_value(self):
        assert OperationType.DATABASE_QUERY == "database_query"

    def test_other_value(self):
        assert OperationType.OTHER == "other"

    def test_all_values_are_strings(self):
        for item in OperationType:
            assert isinstance(item.value, str)


class TestOperationStatusEnum:
    def test_success_value(self):
        assert OperationStatus.SUCCESS == "success"

    def test_failed_value(self):
        assert OperationStatus.FAILED == "failed"

    def test_pending_value(self):
        assert OperationStatus.PENDING == "pending"

    def test_cancelled_value(self):
        assert OperationStatus.CANCELLED == "cancelled"

    def test_partial_value(self):
        assert OperationStatus.PARTIAL == "partial"


# ---------------------------------------------------------------------------
# OperationRecord dataclass
# ---------------------------------------------------------------------------


class TestOperationRecord:
    def test_default_id_empty(self):
        rec = OperationRecord()
        assert rec.id == ""

    def test_default_status_pending(self):
        rec = OperationRecord()
        assert rec.status == OperationStatus.PENDING

    def test_default_operation_type_other(self):
        rec = OperationRecord()
        assert rec.operation_type == OperationType.OTHER

    def test_default_args_empty_dict(self):
        rec = OperationRecord()
        assert rec.args == {}

    def test_default_tags_empty_list(self):
        rec = OperationRecord()
        assert rec.tags == []

    def test_reversible_false_by_default(self):
        rec = OperationRecord()
        assert rec.reversible is False

    def test_host_none_by_default(self):
        rec = OperationRecord()
        assert rec.host is None

    def test_to_dict_contains_id(self):
        rec = OperationRecord(id="op-123", command="ls")
        d = rec.to_dict()
        assert d["id"] == "op-123"

    def test_to_dict_serializes_enum_values(self):
        rec = OperationRecord(
            id="x", operation_type=OperationType.FILE_CREATE, status=OperationStatus.SUCCESS
        )
        d = rec.to_dict()
        assert d["operation_type"] == "file_create"
        assert d["status"] == "success"

    def test_from_dict_roundtrip(self):
        rec = OperationRecord(
            id="test-op",
            command="navig run ls",
            operation_type=OperationType.REMOTE_COMMAND,
            status=OperationStatus.SUCCESS,
            host="prod",
        )
        d = rec.to_dict()
        restored = OperationRecord.from_dict(d)
        assert restored.id == "test-op"
        assert restored.command == "navig run ls"
        assert restored.operation_type == OperationType.REMOTE_COMMAND
        assert restored.status == OperationStatus.SUCCESS
        assert restored.host == "prod"

    def test_from_dict_defaults_for_missing_type(self):
        restored = OperationRecord.from_dict({"id": "y"})
        assert restored.operation_type == OperationType.OTHER

    def test_from_dict_defaults_for_missing_status(self):
        restored = OperationRecord.from_dict({"id": "y"})
        assert restored.status == OperationStatus.PENDING


# ---------------------------------------------------------------------------
# OperationRecorder
# ---------------------------------------------------------------------------


@pytest.fixture()
def recorder(tmp_path):
    """Return an OperationRecorder backed by a temp directory."""
    return OperationRecorder(history_dir=tmp_path, max_entries=100)


class TestOperationRecorderBasics:
    def test_history_dir_created(self, tmp_path):
        subdir = tmp_path / "hist"
        OperationRecorder(history_dir=subdir)
        assert subdir.exists()

    def test_record_returns_id(self, recorder):
        rec = OperationRecord(command="ls", id="op-001")
        returned = recorder.record(rec)
        assert returned == "op-001"

    def test_record_generates_id_if_empty(self, recorder):
        rec = OperationRecord(command="df -h")
        op_id = recorder.record(rec)
        assert op_id.startswith("op-")

    def test_record_sets_timestamp_if_empty(self, recorder):
        rec = OperationRecord(command="pwd")
        recorder.record(rec)
        assert rec.timestamp != ""

    def test_record_writes_jsonl_file(self, recorder, tmp_path):
        rec = OperationRecord(id="x1", command="echo hi")
        recorder.record(rec)
        lines = recorder.history_file.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["id"] == "x1"

    def test_multiple_records_append(self, recorder):
        for i in range(5):
            recorder.record(OperationRecord(id=f"op-{i:03d}", command=f"cmd{i}"))
        lines = recorder.history_file.read_text().strip().splitlines()
        assert len(lines) == 5


class TestOperationRecorderGet:
    def test_get_existing_operation(self, recorder):
        rec = OperationRecord(id="find-me", command="stat /tmp", status=OperationStatus.SUCCESS)
        recorder.record(rec)
        fetched = recorder.get_operation("find-me")
        assert fetched is not None
        assert fetched.command == "stat /tmp"

    def test_get_nonexistent_returns_none(self, recorder):
        assert recorder.get_operation("no-such-id") is None

    def test_get_last_n(self, recorder):
        for i in range(7):
            recorder.record(OperationRecord(id=f"op-{i:03d}", command=f"cmd{i}"))
        last3 = recorder.get_last_n(3)
        assert len(last3) == 3

    def test_get_last_n_most_recent_first(self, recorder):
        for i in range(5):
            recorder.record(OperationRecord(id=f"op-{i:03d}", command=f"cmd{i}"))
        last2 = recorder.get_last_n(2)
        # Most recent first → last recorded should appear first
        assert last2[0].id == "op-004"


class TestOperationRecorderStartComplete:
    def test_start_operation_returns_pending_record(self, recorder):
        rec = recorder.start_operation("navig run 'ls'", OperationType.REMOTE_COMMAND, host="dev")
        assert rec.status == OperationStatus.PENDING
        assert rec.host == "dev"
        assert rec.command == "navig run 'ls'"

    def test_complete_operation_sets_success(self, recorder):
        rec = recorder.start_operation("navig run 'ls'")
        op_id = recorder.complete_operation(rec, success=True, output="file.txt\n", duration_ms=50)
        assert op_id.startswith("op-")
        fetched = recorder.get_operation(op_id)
        assert fetched.status == OperationStatus.SUCCESS
        assert fetched.output == "file.txt\n"
        assert fetched.duration_ms == 50

    def test_complete_operation_sets_failed(self, recorder):
        rec = recorder.start_operation("navig run 'rm -rf /'")
        op_id = recorder.complete_operation(rec, success=False, error="Permission denied", exit_code=1)
        fetched = recorder.get_operation(op_id)
        assert fetched.status == OperationStatus.FAILED
        assert fetched.exit_code == 1


class TestOperationRecorderFilters:
    def test_iter_by_host(self, recorder):
        recorder.record(OperationRecord(id="a", command="c1", host="prod"))
        recorder.record(OperationRecord(id="b", command="c2", host="staging"))
        results = list(recorder.iter_operations(host="prod"))
        assert all(r.host == "prod" for r in results)
        assert len(results) == 1

    def test_iter_by_status(self, recorder):
        recorder.record(OperationRecord(id="s1", command="ok", status=OperationStatus.SUCCESS))
        recorder.record(OperationRecord(id="f1", command="bad", status=OperationStatus.FAILED))
        successes = list(recorder.iter_operations(status=OperationStatus.SUCCESS))
        assert len(successes) == 1
        assert successes[0].id == "s1"

    def test_iter_search(self, recorder):
        recorder.record(OperationRecord(id="g1", command="navig db query"))
        recorder.record(OperationRecord(id="g2", command="navig file add stuff"))
        results = list(recorder.iter_operations(search="db query"))
        assert len(results) == 1
        assert results[0].id == "g1"

    def test_iter_limit(self, recorder):
        for i in range(10):
            recorder.record(OperationRecord(id=f"op-{i:03d}", command=f"cmd{i}"))
        results = list(recorder.iter_operations(limit=3))
        assert len(results) == 3


class TestOperationRecorderExport:
    def test_export_json(self, recorder, tmp_path):
        recorder.record(OperationRecord(id="e1", command="export-test"))
        out = tmp_path / "out.json"
        count = recorder.export_json(out)
        assert count == 1
        loaded = json.loads(out.read_text())
        assert isinstance(loaded, list)
        assert loaded[0]["id"] == "e1"

    def test_export_csv(self, recorder, tmp_path):
        recorder.record(OperationRecord(id="c1", command="csv-test", host="myhost"))
        out = tmp_path / "out.csv"
        count = recorder.export_csv(out)
        assert count == 1
        text = out.read_text()
        assert "timestamp" in text  # header row
        assert "csv-test" in text

    def test_export_csv_empty(self, recorder, tmp_path):
        out = tmp_path / "empty.csv"
        count = recorder.export_csv(out)
        assert count == 0

    def test_export_json_empty(self, recorder, tmp_path):
        out = tmp_path / "empty.json"
        count = recorder.export_json(out)
        assert count == 0
        data = json.loads(out.read_text())
        assert data == []


class TestOperationRecorderTruncation:
    def test_truncate_output_short_passthrough(self, recorder):
        result = recorder._truncate_output("short output")
        assert result == "short output"

    def test_truncate_output_long_truncated(self, recorder):
        big = "x" * 20_000
        result = recorder._truncate_output(big)
        assert "[TRUNCATED" in result

    def test_truncate_output_empty_passthrough(self, recorder):
        assert recorder._truncate_output("") == ""


class TestOperationRecorderRotation:
    def test_rotation_keeps_recent_entries(self, tmp_path):
        # max_entries=10; add 12 → rotation trims to 5
        rec = OperationRecorder(history_dir=tmp_path, max_entries=10)
        for i in range(12):
            rec.record(OperationRecord(id=f"r-{i:03d}", command=f"cmd{i}"))
        # After rotation, count should be ≤ max_entries
        remaining = list(rec.iter_operations(limit=1000, reverse=False))
        assert len(remaining) <= 10
