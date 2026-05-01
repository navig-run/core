"""Tests for operation_recorder.py (types) and safety_guard.py (pure helpers)."""
from __future__ import annotations

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# operation_recorder.py — enums + OperationRecord
# ──────────────────────────────────────────────────────────────────────────────
from navig.operation_recorder import (
    OperationRecord,
    OperationStatus,
    OperationType,
)


class TestOperationType:
    def test_file_create_value(self):
        assert OperationType.FILE_CREATE.value == "file_create"

    def test_remote_command_value(self):
        assert OperationType.REMOTE_COMMAND.value == "remote_command"

    def test_other_value(self):
        assert OperationType.OTHER.value == "other"

    def test_is_str_subclass(self):
        assert isinstance(OperationType.FILE_DELETE, str)

    def test_has_database_types(self):
        assert hasattr(OperationType, "DATABASE_QUERY")
        assert hasattr(OperationType, "DATABASE_DUMP")

    def test_has_docker_type(self):
        assert hasattr(OperationType, "DOCKER_COMMAND")


class TestOperationStatus:
    def test_success_value(self):
        assert OperationStatus.SUCCESS.value == "success"

    def test_failed_value(self):
        assert OperationStatus.FAILED.value == "failed"

    def test_pending_value(self):
        assert OperationStatus.PENDING.value == "pending"

    def test_cancelled_value(self):
        assert OperationStatus.CANCELLED.value == "cancelled"

    def test_is_str_subclass(self):
        assert isinstance(OperationStatus.SUCCESS, str)


class TestOperationRecord:
    def test_defaults(self):
        rec = OperationRecord()
        assert rec.id == ""
        assert rec.command == ""
        assert rec.operation_type == OperationType.OTHER
        assert rec.status == OperationStatus.PENDING
        assert rec.reversible is False

    def test_set_fields(self):
        rec = OperationRecord(
            id="abc",
            command="navig run ls",
            operation_type=OperationType.REMOTE_COMMAND,
            status=OperationStatus.SUCCESS,
        )
        assert rec.id == "abc"
        assert rec.operation_type == OperationType.REMOTE_COMMAND
        assert rec.status == OperationStatus.SUCCESS

    def test_to_dict_returns_dict(self):
        rec = OperationRecord(id="x", command="test")
        d = rec.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_serializes_enum_values(self):
        rec = OperationRecord(
            operation_type=OperationType.FILE_CREATE,
            status=OperationStatus.SUCCESS,
        )
        d = rec.to_dict()
        assert d["operation_type"] == "file_create"
        assert d["status"] == "success"

    def test_from_dict_roundtrip(self):
        rec1 = OperationRecord(
            id="rt1",
            command="navig db list",
            operation_type=OperationType.DATABASE_QUERY,
            status=OperationStatus.SUCCESS,
            tags=["test"],
        )
        d = rec1.to_dict()
        rec2 = OperationRecord.from_dict(d)
        assert rec2.id == "rt1"
        assert rec2.command == "navig db list"
        assert rec2.operation_type == OperationType.DATABASE_QUERY
        assert rec2.status == OperationStatus.SUCCESS

    def test_from_dict_defaults_unknown_type(self):
        d = {"operation_type": "other", "status": "pending"}
        rec = OperationRecord.from_dict(d)
        assert rec.operation_type == OperationType.OTHER

    def test_tags_default_empty(self):
        assert OperationRecord().tags == []

    def test_args_default_empty(self):
        assert OperationRecord().args == {}


# ──────────────────────────────────────────────────────────────────────────────
# safety_guard.py — pure helpers
# ──────────────────────────────────────────────────────────────────────────────
from navig.safety_guard import (
    _coerce_action_text,
    _normalize_confirmation_level,
    _truncate,
    classify_action_risk,
    is_destructive,
    is_risky,
    should_confirm,
)


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_string_truncated(self):
        result = _truncate("a" * 200, 100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_exact_boundary_unchanged(self):
        s = "x" * 100
        assert _truncate(s, 100) == s

    def test_default_maxlen_100(self):
        s = "a" * 150
        result = _truncate(s)
        assert len(result) == 103


class TestCoerceActionText:
    def test_none_returns_empty(self):
        assert _coerce_action_text(None) == ""

    def test_string_unchanged(self):
        assert _coerce_action_text("rm -rf /") == "rm -rf /"

    def test_int_to_str(self):
        assert _coerce_action_text(42) == "42"

    def test_list_to_str(self):
        result = _coerce_action_text(["a", "b"])
        assert isinstance(result, str)


class TestNormalizeConfirmationLevel:
    def test_standard_passthrough(self):
        assert _normalize_confirmation_level("standard") == "standard"

    def test_critical_passthrough(self):
        assert _normalize_confirmation_level("critical") == "critical"

    def test_verbose_passthrough(self):
        assert _normalize_confirmation_level("verbose") == "verbose"

    def test_none_returns_standard(self):
        assert _normalize_confirmation_level(None) == "standard"

    def test_empty_returns_standard(self):
        assert _normalize_confirmation_level("") == "standard"

    def test_unknown_returns_standard(self):
        assert _normalize_confirmation_level("blorp") == "standard"

    def test_case_insensitive(self):
        assert _normalize_confirmation_level("CRITICAL") == "critical"


class TestIsDestructive:
    def test_rm_rf_is_destructive(self):
        assert is_destructive("rm -rf /var/www") is True

    def test_ls_is_not_destructive(self):
        assert is_destructive("ls -la") is False

    def test_drop_database_is_destructive(self):
        assert is_destructive("DROP DATABASE mydb") is True


class TestIsRisky:
    def test_destructive_is_also_risky(self):
        assert is_risky("rm -rf /") is True

    def test_safe_command_not_risky(self):
        assert is_risky("ls /tmp") is False


class TestClassifyActionRisk:
    def test_safe_command(self):
        assert classify_action_risk("ls -la") == "safe"

    def test_rm_rf_is_destructive(self):
        assert classify_action_risk("rm -rf /data") == "destructive"

    def test_returns_one_of_expected_values(self):
        for cmd in ["echo hello", "cat /etc/hosts", "rm -rf /"]:
            result = classify_action_risk(cmd)
            assert result in ("safe", "risky", "destructive")


class TestShouldConfirm:
    def test_safe_critical_no_confirm(self):
        assert should_confirm("ls -la", "critical") is False

    def test_safe_standard_no_confirm(self):
        assert should_confirm("ls -la", "standard") is False

    def test_safe_verbose_confirm(self):
        assert should_confirm("ls -la", "verbose") is True

    def test_destructive_always_confirm(self):
        assert should_confirm("rm -rf /", "critical") is True
        assert should_confirm("rm -rf /", "standard") is True
        assert should_confirm("rm -rf /", "verbose") is True
