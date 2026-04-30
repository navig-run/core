"""
Batch 122 — agent/hands (CommandStatus/CommandResult/PendingAction)
         + bot/command_tools (_build_*/ get_all_tool_names/get_tool_by_name)

Pure-unit tests: no network, no file I/O.
"""

from __future__ import annotations

from datetime import datetime

import pytest

# ---------------------------------------------------------------------------
# agent/hands — CommandStatus
# ---------------------------------------------------------------------------

from navig.agent.hands import CommandResult, CommandStatus, PendingAction


class TestCommandStatus:
    def test_pending_member_exists(self):
        assert hasattr(CommandStatus, "PENDING")

    def test_running_member_exists(self):
        assert hasattr(CommandStatus, "RUNNING")

    def test_completed_member_exists(self):
        assert hasattr(CommandStatus, "COMPLETED")

    def test_failed_member_exists(self):
        assert hasattr(CommandStatus, "FAILED")

    def test_timeout_member_exists(self):
        assert hasattr(CommandStatus, "TIMEOUT")

    def test_cancelled_member_exists(self):
        assert hasattr(CommandStatus, "CANCELLED")

    def test_requires_approval_member_exists(self):
        assert hasattr(CommandStatus, "REQUIRES_APPROVAL")

    def test_all_unique(self):
        values = [s.value for s in CommandStatus]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# agent/hands — CommandResult
# ---------------------------------------------------------------------------


class TestCommandResult:
    def _make(self, status=CommandStatus.COMPLETED, exit_code=0):
        return CommandResult(command="ls -la", status=status, exit_code=exit_code)

    def test_command_stored(self):
        assert self._make().command == "ls -la"

    def test_status_stored(self):
        assert self._make().status == CommandStatus.COMPLETED

    def test_exit_code_stored(self):
        assert self._make().exit_code == 0

    def test_stdout_default_empty(self):
        assert self._make().stdout == ""

    def test_stderr_default_empty(self):
        assert self._make().stderr == ""

    def test_duration_default_zero(self):
        assert self._make().duration_seconds == 0.0

    def test_timestamp_auto_populated(self):
        r = self._make()
        assert isinstance(r.timestamp, datetime)

    # success property
    def test_success_true_when_completed_exit_zero(self):
        r = CommandResult(command="ls", status=CommandStatus.COMPLETED, exit_code=0)
        assert r.success is True

    def test_success_false_when_completed_nonzero_exit(self):
        r = CommandResult(command="ls", status=CommandStatus.COMPLETED, exit_code=1)
        assert r.success is False

    def test_success_false_when_failed(self):
        r = CommandResult(command="ls", status=CommandStatus.FAILED, exit_code=0)
        assert r.success is False

    def test_success_false_when_timeout(self):
        r = CommandResult(command="ls", status=CommandStatus.TIMEOUT, exit_code=None)
        assert r.success is False

    # to_dict
    def test_to_dict_command(self):
        assert self._make().to_dict()["command"] == "ls -la"

    def test_to_dict_status_is_name(self):
        assert self._make().to_dict()["status"] == "COMPLETED"

    def test_to_dict_success_key_present(self):
        d = self._make().to_dict()
        assert "success" in d

    def test_to_dict_timestamp_is_str(self):
        assert isinstance(self._make().to_dict()["timestamp"], str)

    def test_to_dict_stdout_limited_to_10000(self):
        r = CommandResult(command="cmd", status=CommandStatus.COMPLETED, exit_code=0)
        r.stdout = "x" * 20000
        assert len(r.to_dict()["stdout"]) <= 10000


# ---------------------------------------------------------------------------
# agent/hands — PendingAction
# ---------------------------------------------------------------------------


class TestPendingAction:
    def _make(self):
        return PendingAction(
            id="pa-001",
            command="rm -rf /tmp/cache",
            reason="Cache cleanup",
            requested_by="user-1",
        )

    def test_id_stored(self):
        assert self._make().id == "pa-001"

    def test_command_stored(self):
        assert self._make().command == "rm -rf /tmp/cache"

    def test_reason_stored(self):
        assert self._make().reason == "Cache cleanup"

    def test_requested_by_stored(self):
        assert self._make().requested_by == "user-1"

    def test_approved_default_none(self):
        assert self._make().approved is None

    def test_approved_by_default_none(self):
        assert self._make().approved_by is None

    def test_approved_at_default_none(self):
        assert self._make().approved_at is None

    def test_requested_at_auto_populated(self):
        assert isinstance(self._make().requested_at, datetime)

    # to_dict
    def test_to_dict_id(self):
        assert self._make().to_dict()["id"] == "pa-001"

    def test_to_dict_approved_none(self):
        assert self._make().to_dict()["approved"] is None

    def test_to_dict_approved_at_none(self):
        assert self._make().to_dict()["approved_at"] is None

    def test_to_dict_approved_at_iso_when_set(self):
        pa = self._make()
        pa.approved_at = datetime(2024, 1, 1, 12, 0, 0)
        assert pa.to_dict()["approved_at"] == "2024-01-01T12:00:00"


# ---------------------------------------------------------------------------
# bot/command_tools — _build_* helpers
# ---------------------------------------------------------------------------

from navig.bot.command_tools import (
    _build_backup_cmd,
    _build_hestia_cmd,
    _build_remind_cmd,
    _build_tunnel_cmd,
    get_all_tool_names,
    get_tool_by_name,
)


class TestBuildTunnelCmd:
    def test_list_action(self):
        assert _build_tunnel_cmd({"action": "list"}) == "/tunnel"

    def test_no_action_defaults_to_list(self):
        assert _build_tunnel_cmd({}) == "/tunnel"

    def test_start_action_with_name(self):
        result = _build_tunnel_cmd({"action": "start", "tunnel_name": "mydb"})
        assert "start" in result
        assert "mydb" in result


class TestBuildBackupCmd:
    def test_list_action(self):
        assert _build_backup_cmd({"action": "list"}) == "/backup"

    def test_no_action_defaults_to_list(self):
        assert _build_backup_cmd({}) == "/backup"

    def test_run_action_with_target(self):
        result = _build_backup_cmd({"action": "run", "target": "mydb"})
        assert "run" in result
        assert "mydb" in result


class TestBuildHestiaCmd:
    def test_no_resource_returns_base(self):
        assert _build_hestia_cmd({}) == "/hestia"

    def test_with_resource(self):
        result = _build_hestia_cmd({"resource": "domains"})
        assert "domains" in result

    def test_with_resource_and_user(self):
        result = _build_hestia_cmd({"resource": "domains", "user": "admin"})
        assert "admin" in result


class TestBuildRemindCmd:
    def test_default_duration_30_minutes(self):
        result = _build_remind_cmd({"message": "check logs"})
        assert "30m" in result
        assert "check logs" in result

    def test_hours_unit(self):
        result = _build_remind_cmd({"message": "review", "duration": 2, "unit": "hours"})
        assert "2h" in result

    def test_days_unit(self):
        result = _build_remind_cmd({"message": "meeting", "duration": 1, "unit": "days"})
        assert "1d" in result

    def test_unknown_unit_defaults_to_m(self):
        result = _build_remind_cmd({"message": "thing", "duration": 5, "unit": "fortnights"})
        assert "5m" in result


# ---------------------------------------------------------------------------
# bot/command_tools — get_all_tool_names / get_tool_by_name
# ---------------------------------------------------------------------------


class TestGetAllToolNames:
    def test_returns_list(self):
        assert isinstance(get_all_tool_names(), list)

    def test_non_empty(self):
        assert len(get_all_tool_names()) > 0

    def test_all_strings(self):
        for name in get_all_tool_names():
            assert isinstance(name, str)

    def test_no_duplicates(self):
        names = get_all_tool_names()
        assert len(names) == len(set(names))


class TestGetToolByName:
    def test_unknown_name_returns_none(self):
        assert get_tool_by_name("totally_unknown_xyz_tool") is None

    def test_known_name_returns_dict(self):
        names = get_all_tool_names()
        if names:
            result = get_tool_by_name(names[0])
            assert isinstance(result, dict)

    def test_returned_dict_has_function_key(self):
        names = get_all_tool_names()
        if names:
            result = get_tool_by_name(names[0])
            assert "function" in result
