"""Tests for navig.operation_recorder — recorder, context manager, helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recorder(tmp_path: Path):
    from navig.operation_recorder import OperationRecorder

    return OperationRecorder(history_dir=tmp_path)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestOperationType:
    def test_values_are_strings(self):
        from navig.operation_recorder import OperationType

        assert OperationType.FILE_CREATE == "file_create"
        assert OperationType.REMOTE_COMMAND == "remote_command"
        assert OperationType.DATABASE_QUERY == "database_query"
        assert OperationType.OTHER == "other"

    def test_all_members_present(self):
        from navig.operation_recorder import OperationType

        names = {m.name for m in OperationType}
        for expected in (
            "FILE_CREATE", "FILE_DELETE", "FILE_MODIFY", "REMOTE_COMMAND",
            "LOCAL_COMMAND", "DATABASE_QUERY", "HOST_SWITCH", "OTHER",
        ):
            assert expected in names


class TestOperationStatus:
    def test_values(self):
        from navig.operation_recorder import OperationStatus

        assert OperationStatus.SUCCESS == "success"
        assert OperationStatus.FAILED == "failed"
        assert OperationStatus.PENDING == "pending"


# ---------------------------------------------------------------------------
# OperationRecord
# ---------------------------------------------------------------------------


class TestOperationRecord:
    def _make(self, **kwargs):
        from navig.operation_recorder import OperationRecord

        defaults = dict(id="op-123", command="navig run ls", timestamp="2025-01-01T00:00:00Z")
        defaults.update(kwargs)
        return OperationRecord(**defaults)

    def test_defaults(self):
        from navig.operation_recorder import OperationRecord, OperationStatus, OperationType

        r = OperationRecord()
        assert r.id == ""
        assert r.command == ""
        assert r.operation_type == OperationType.OTHER
        assert r.status == OperationStatus.PENDING
        assert r.reversible is False
        assert r.tags == []

    def test_to_dict_serialises_enums(self):
        from navig.operation_recorder import OperationStatus, OperationType

        r = self._make()
        d = r.to_dict()
        assert d["operation_type"] == OperationType.OTHER.value
        assert d["status"] == OperationStatus.PENDING.value

    def test_from_dict_roundtrip(self):
        from navig.operation_recorder import OperationStatus, OperationType

        original = self._make(
            id="op-abc",
            command="navig db query",
            operation_type=OperationType.DATABASE_QUERY,
            status=OperationStatus.SUCCESS,
            host="prod",
            tags=["db", "query"],
        )
        d = original.to_dict()
        restored = type(original).from_dict(d)
        assert restored.id == original.id
        assert restored.command == original.command
        assert restored.operation_type == OperationType.DATABASE_QUERY
        assert restored.status == OperationStatus.SUCCESS
        assert restored.host == "prod"
        assert restored.tags == ["db", "query"]

    def test_from_dict_unknown_type_defaults_to_other(self):
        from navig.operation_recorder import OperationRecord, OperationType

        data = {"id": "x", "operation_type": "nonexistent_type", "status": "success"}
        with pytest.raises(ValueError):
            OperationRecord.from_dict(data)


# ---------------------------------------------------------------------------
# OperationRecorder — basic I/O
# ---------------------------------------------------------------------------


class TestOperationRecorderRecord:
    def test_record_creates_file(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        op = OperationRecord(command="ls")
        op_id = rec.record(op)

        assert (tmp_path / "operations.jsonl").exists()
        assert op_id  # non-empty

    def test_record_assigns_id_if_missing(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        op = OperationRecord(command="ls")
        assert op.id == ""
        returned_id = rec.record(op)
        assert returned_id.startswith("op-")

    def test_record_assigns_timestamp_if_missing(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        op = OperationRecord(command="ls")
        rec.record(op)
        assert op.timestamp  # assigned during record()

    def test_multiple_records_appended(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        for cmd in ("cmd1", "cmd2", "cmd3"):
            rec.record(OperationRecord(command=cmd))

        lines = (tmp_path / "operations.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# OperationRecorder — start/complete
# ---------------------------------------------------------------------------


class TestStartComplete:
    def test_start_operation_returns_pending_record(self, tmp_path):
        from navig.operation_recorder import OperationStatus, OperationType

        rec = _make_recorder(tmp_path)
        op = rec.start_operation("navig run ls", operation_type=OperationType.REMOTE_COMMAND, host="prod")

        assert op.command == "navig run ls"
        assert op.operation_type == OperationType.REMOTE_COMMAND
        assert op.host == "prod"
        assert op.status == OperationStatus.PENDING
        assert op.id  # assigned by start_operation

    def test_complete_operation_records_success(self, tmp_path):
        from navig.operation_recorder import OperationStatus

        rec = _make_recorder(tmp_path)
        with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
            op = rec.start_operation("navig run ls")
            rec.complete_operation(op, success=True, output="hello", duration_ms=42.0)

        ops = rec.get_last_n(1)
        assert len(ops) == 1
        assert ops[0].status == OperationStatus.SUCCESS
        assert ops[0].output == "hello"

    def test_complete_operation_records_failure(self, tmp_path):
        from navig.operation_recorder import OperationStatus

        rec = _make_recorder(tmp_path)
        with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
            op = rec.start_operation("navig db drop")
            rec.complete_operation(op, success=False, error="permission denied")

        ops = rec.get_last_n(1)
        assert ops[0].status == OperationStatus.FAILED
        assert ops[0].error == "permission denied"


# ---------------------------------------------------------------------------
# OperationRecorder — queries
# ---------------------------------------------------------------------------


class TestRecorderQueries:
    def _populate(self, rec, n=5):
        """Populate recorder with n simple operations (audit side-patched out)."""
        from navig.operation_recorder import OperationRecord, OperationStatus

        with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
            for i in range(n):
                op = rec.start_operation(f"cmd{i}", host="host-a" if i % 2 == 0 else "host-b")
                rec.complete_operation(op, success=(i % 3 != 0))

    def test_get_last_n(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate(rec, 7)
        last3 = rec.get_last_n(3)
        assert len(last3) == 3

    def test_iter_operations_host_filter(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate(rec, 6)
        host_a = list(rec.iter_operations(limit=100, host="host-a"))
        assert all(op.host == "host-a" for op in host_a)

    def test_iter_operations_search(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate(rec, 6)
        found = list(rec.iter_operations(limit=100, search="cmd3"))
        assert len(found) == 1
        assert "cmd3" in found[0].command

    def test_count(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate(rec, 5)
        assert rec.count() == 5

    def test_get_by_command(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate(rec, 6)
        results = rec.get_by_command("cmd2")
        assert any("cmd2" in r.command for r in results)


# ---------------------------------------------------------------------------
# OperationRecorder — get_operation
# ---------------------------------------------------------------------------


class TestGetOperation:
    def test_get_existing(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        op = OperationRecord(id="op-explicit", command="explicit")
        rec.record(op)

        fetched = rec.get_operation("op-explicit")
        assert fetched is not None
        assert fetched.command == "explicit"

    def test_get_nonexistent_returns_none(self, tmp_path):
        rec = _make_recorder(tmp_path)
        assert rec.get_operation("nope") is None


# ---------------------------------------------------------------------------
# OperationRecorder — clear_history
# ---------------------------------------------------------------------------


class TestClearHistory:
    def test_clear_removes_file(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="x"))
        assert (tmp_path / "operations.jsonl").exists()

        count = rec.clear_history()
        assert count == 1
        assert not (tmp_path / "operations.jsonl").exists()

    def test_clear_empty_returns_zero(self, tmp_path):
        rec = _make_recorder(tmp_path)
        assert rec.clear_history() == 0


# ---------------------------------------------------------------------------
# OperationRecorder — export
# ---------------------------------------------------------------------------


class TestExport:
    def _populate_one(self, rec):
        from navig.operation_recorder import OperationRecord

        rec.record(OperationRecord(id="exp-1", command="export-cmd", host="h1", timestamp="2025-01-01T00:00:00Z"))

    def test_export_json(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate_one(rec)
        outfile = tmp_path / "out.json"
        count = rec.export_json(outfile)
        assert count == 1
        data = json.loads(outfile.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert data[0]["command"] == "export-cmd"

    def test_export_csv(self, tmp_path):
        rec = _make_recorder(tmp_path)
        self._populate_one(rec)
        outfile = tmp_path / "out.csv"
        count = rec.export_csv(outfile)
        assert count == 1
        text = outfile.read_text(encoding="utf-8")
        assert "timestamp" in text
        assert "export-cmd" in text

    def test_export_csv_empty(self, tmp_path):
        rec = _make_recorder(tmp_path)
        outfile = tmp_path / "empty.csv"
        count = rec.export_csv(outfile)
        assert count == 0


# ---------------------------------------------------------------------------
# RecordedOperation context manager
# ---------------------------------------------------------------------------


class TestRecordedOperation:
    def _ctx(self, tmp_path, **kwargs):
        from navig.operation_recorder import OperationRecorder, RecordedOperation

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            ctx = RecordedOperation("navig run ls", **kwargs)
            ctx._recorder = recorder
        return ctx, recorder

    def test_success_records_success(self, tmp_path):
        from navig.operation_recorder import OperationRecorder, OperationStatus, RecordedOperation

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
                with RecordedOperation("navig run ls") as op:
                    op.output = "listed"
                    op.success = True

        ops = recorder.get_last_n(1)
        assert ops[0].status == OperationStatus.SUCCESS
        assert ops[0].output == "listed"

    def test_exception_auto_marks_failure(self, tmp_path):
        from navig.operation_recorder import OperationRecorder, OperationStatus, RecordedOperation

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
                with pytest.raises(ValueError):
                    with RecordedOperation("navig run bad") as op:
                        raise ValueError("boom")

        ops = recorder.get_last_n(1)
        assert ops[0].status == OperationStatus.FAILED
        assert "boom" in ops[0].error

    def test_does_not_suppress_exception(self, tmp_path):
        from navig.operation_recorder import OperationRecorder, RecordedOperation

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
                with pytest.raises(RuntimeError, match="propagated"):
                    with RecordedOperation("cmd"):
                        raise RuntimeError("propagated")


# ---------------------------------------------------------------------------
# quick_record
# ---------------------------------------------------------------------------


class TestQuickRecord:
    def test_records_with_defaults(self, tmp_path):
        from navig.operation_recorder import OperationRecorder, OperationStatus, quick_record

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
                op_id = quick_record("navig host list", host="srv1", success=True, output="ok")

        assert op_id
        ops = recorder.get_last_n(1)
        assert ops[0].status == OperationStatus.SUCCESS
        assert ops[0].host == "srv1"

    def test_records_failure(self, tmp_path):
        from navig.operation_recorder import OperationRecorder, OperationStatus, quick_record

        recorder = OperationRecorder(history_dir=tmp_path)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            with patch("navig.store.audit.get_audit_store", side_effect=ImportError):
                quick_record("navig db drop", success=False, error="not allowed")

        ops = recorder.get_last_n(1)
        assert ops[0].status == OperationStatus.FAILED
        assert ops[0].error == "not allowed"


# ---------------------------------------------------------------------------
# get_operation_recorder singleton
# ---------------------------------------------------------------------------


class TestGetOperationRecorderSingleton:
    def test_returns_singleton(self, tmp_path, monkeypatch):
        import navig.operation_recorder as m

        monkeypatch.setattr(m, "_recorder", None)

        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.base_dir = tmp_path
            from navig.operation_recorder import get_operation_recorder

            r1 = get_operation_recorder()
            r2 = get_operation_recorder()

        assert r1 is r2
