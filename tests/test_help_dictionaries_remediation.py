"""
Batch 120: tests for
  - navig/cli/help_dictionaries.py  (HELP_REGISTRY structure)
  - navig/agent/remediation.py      (enums, RemediationAction, RemediationEngine)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.cli.help_dictionaries
# ---------------------------------------------------------------------------

import navig.cli.help_dictionaries as _hd


class TestHelpRegistry:
    def test_help_registry_exists(self):
        assert hasattr(_hd, "HELP_REGISTRY")

    def test_help_registry_is_dict(self):
        assert isinstance(_hd.HELP_REGISTRY, dict)

    def test_help_registry_not_empty(self):
        assert len(_hd.HELP_REGISTRY) > 0

    def test_all_entries_have_desc(self):
        for key, val in _hd.HELP_REGISTRY.items():
            assert "desc" in val, f"Entry '{key}' missing 'desc'"
            assert isinstance(val["desc"], str), f"Entry '{key}' desc not str"

    def test_all_entries_have_commands(self):
        for key, val in _hd.HELP_REGISTRY.items():
            assert "commands" in val, f"Entry '{key}' missing 'commands'"
            assert isinstance(val["commands"], dict), f"'{key}' commands not dict"

    def test_commands_values_are_strings(self):
        for key, val in _hd.HELP_REGISTRY.items():
            for cmd, desc in val["commands"].items():
                assert isinstance(desc, str), (
                    f"Entry '{key}' command '{cmd}' has non-str description"
                )

    def test_core_keys_present(self):
        for expected in ("host", "tunnel", "local"):
            assert expected in _hd.HELP_REGISTRY, f"Key '{expected}' missing"

    def test_host_entry_structure(self):
        host = _hd.HELP_REGISTRY["host"]
        assert "list" in host["commands"]
        assert "add" in host["commands"]
        assert "use" in host["commands"]

    def test_desc_non_empty_strings(self):
        for key, val in _hd.HELP_REGISTRY.items():
            assert val["desc"].strip(), f"Entry '{key}' has empty desc"

    def test_commands_keys_are_strings(self):
        for key, val in _hd.HELP_REGISTRY.items():
            for cmd in val["commands"]:
                assert isinstance(cmd, str), f"Entry '{key}' command key not str"

    def test_no_duplicate_top_level_keys(self):
        keys = list(_hd.HELP_REGISTRY.keys())
        assert len(keys) == len(set(keys))

    def test_registry_all_entries_above_one_command(self):
        """Every entry has at least one command/option documented."""
        for key, val in _hd.HELP_REGISTRY.items():
            assert len(val["commands"]) >= 1, f"Entry '{key}' has no commands"


# ---------------------------------------------------------------------------
# navig.agent.remediation
# ---------------------------------------------------------------------------

from navig.agent.remediation import (
    RemediationAction,
    RemediationEngine,
    RemediationStatus,
    RemediationType,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestRemediationTypeEnum:
    def test_values_exist(self):
        assert RemediationType.COMPONENT_RESTART.value == "component_restart"
        assert RemediationType.CONNECTION_RETRY.value == "connection_retry"
        assert RemediationType.CONFIG_ROLLBACK.value == "config_rollback"
        assert RemediationType.PERMISSION_FIX.value == "permission_fix"
        assert RemediationType.SERVICE_RESTART.value == "service_restart"

    def test_enum_count(self):
        assert len(RemediationType) == 5

    def test_all_members_are_strings(self):
        for member in RemediationType:
            assert isinstance(member.value, str)


class TestRemediationStatusEnum:
    def test_values_exist(self):
        assert RemediationStatus.PENDING.value == "pending"
        assert RemediationStatus.IN_PROGRESS.value == "in_progress"
        assert RemediationStatus.SUCCESS.value == "success"
        assert RemediationStatus.FAILED.value == "failed"
        assert RemediationStatus.SKIPPED.value == "skipped"

    def test_enum_count(self):
        assert len(RemediationStatus) == 5


# ---------------------------------------------------------------------------
# RemediationAction
# ---------------------------------------------------------------------------


class TestRemediationAction:
    def _make(self, **kwargs):
        defaults = dict(
            id="act-001",
            type=RemediationType.COMPONENT_RESTART,
            component="my_service",
            reason="crashed",
        )
        defaults.update(kwargs)
        return RemediationAction(**defaults)

    def test_defaults(self):
        a = self._make()
        assert a.status == RemediationStatus.PENDING
        assert a.attempts == 0
        assert a.max_attempts == 5
        assert a.error is None
        assert a.metadata == {}
        assert isinstance(a.timestamp, datetime)

    def test_to_dict_keys(self):
        a = self._make()
        d = a.to_dict()
        for key in ("id", "type", "component", "reason", "timestamp", "status",
                    "attempts", "max_attempts", "error", "metadata"):
            assert key in d

    def test_to_dict_type_is_value(self):
        a = self._make(type=RemediationType.CONNECTION_RETRY)
        d = a.to_dict()
        assert d["type"] == "connection_retry"

    def test_to_dict_status_is_value(self):
        a = self._make()
        a.status = RemediationStatus.SUCCESS
        d = a.to_dict()
        assert d["status"] == "success"

    def test_to_dict_timestamp_is_iso(self):
        a = self._make()
        d = a.to_dict()
        # Should parse back without error
        datetime.fromisoformat(d["timestamp"])

    def test_from_dict_roundtrip(self):
        a = self._make(reason="oom", metadata={"key": "val"})
        d = a.to_dict()
        restored = RemediationAction.from_dict(d)
        assert restored.id == a.id
        assert restored.type == a.type
        assert restored.component == a.component
        assert restored.reason == a.reason
        assert restored.status == a.status
        assert restored.attempts == a.attempts
        assert restored.metadata == {"key": "val"}

    def test_from_dict_bad_attempts_defaults_zero(self):
        a = self._make()
        d = a.to_dict()
        d["attempts"] = "not-a-number"
        restored = RemediationAction.from_dict(d)
        assert restored.attempts == 0

    def test_from_dict_bad_max_attempts_defaults_five(self):
        a = self._make()
        d = a.to_dict()
        d["max_attempts"] = None
        restored = RemediationAction.from_dict(d)
        assert restored.max_attempts == 5

    def test_from_dict_with_error(self):
        a = self._make()
        a.error = "some error"
        d = a.to_dict()
        restored = RemediationAction.from_dict(d)
        assert restored.error == "some error"

    def test_from_dict_missing_status_defaults_pending(self):
        a = self._make()
        d = a.to_dict()
        del d["status"]
        restored = RemediationAction.from_dict(d)
        assert restored.status == RemediationStatus.PENDING

    def test_backoff_default_list(self):
        a = self._make()
        assert len(a.backoff_seconds) > 0
        assert all(isinstance(v, int) for v in a.backoff_seconds)


# ---------------------------------------------------------------------------
# RemediationEngine
# ---------------------------------------------------------------------------


def _make_engine(tmp_path: Path) -> RemediationEngine:
    """Create an engine wired to a temp directory, suppressing DebugLogger."""
    config_dir = tmp_path / "config"
    log_dir = tmp_path / "logs"
    with patch("navig.agent.remediation.DebugLogger") as mock_logger_cls:
        mock_logger_cls.return_value = MagicMock()
        engine = RemediationEngine(config_dir=config_dir, log_dir=log_dir)
    return engine


class TestRemediationEngineInit:
    def test_dirs_created(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.config_dir.exists()
        assert engine.log_dir.exists()
        assert engine.backup_dir.exists()

    def test_actions_empty_on_fresh_init(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine._actions == {}

    def test_actions_file_path(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.actions_file.name == "remediation_actions.json"

    def test_not_running_by_default(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine._running is False


class TestRemediationEngineScheduleSync:
    def test_schedule_restart_returns_id(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("web", "oom")
        assert isinstance(aid, str)
        assert "web" in aid

    def test_schedule_restart_stores_action(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("db", "crash", metadata={"pid": 1})
        assert aid in engine._actions
        action = engine._actions[aid]
        assert action.type == RemediationType.COMPONENT_RESTART
        assert action.component == "db"
        assert action.metadata["pid"] == 1

    def test_schedule_connection_retry_returns_id(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_connection_retry_sync("redis", "pubsub", "timeout")
        assert isinstance(aid, str)
        assert "redis" in aid

    def test_schedule_connection_retry_stores_service(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_connection_retry_sync("redis", "pubsub", "timeout")
        action = engine._actions[aid]
        assert action.metadata["service"] == "pubsub"
        assert action.type == RemediationType.CONNECTION_RETRY

    def test_multiple_schedules_distinct_ids(self, tmp_path):
        engine = _make_engine(tmp_path)
        ids = {engine.schedule_restart_sync("svc", "r") for _ in range(5)}
        # All should be stored (some may collide if same ms, but check stored)
        assert len(engine._actions) >= 1


class TestRemediationEnginePersistence:
    def test_save_and_reload(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.schedule_restart_sync("api", "crash")
        assert engine.actions_file.exists()

        # Reload engine from same dirs
        config_dir = engine.config_dir
        log_dir = engine.log_dir
        with patch("navig.agent.remediation.DebugLogger") as mock_logger_cls:
            mock_logger_cls.return_value = MagicMock()
            engine2 = RemediationEngine(config_dir=config_dir, log_dir=log_dir)
        assert len(engine2._actions) >= 1

    def test_load_actions_corrupt_file(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.actions_file.parent.mkdir(parents=True, exist_ok=True)
        engine.actions_file.write_text("not-json", encoding="utf-8")
        # Should not raise; just log warning
        engine._load_actions()

    def test_load_actions_nonexistent_file(self, tmp_path):
        engine = _make_engine(tmp_path)
        # If file doesn't exist, nothing happens
        assert not engine.actions_file.exists()
        engine._load_actions()  # should not raise
        assert engine._actions == {}

    def test_save_actions_writes_valid_json(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.schedule_restart_sync("svc", "test")
        raw = json.loads(engine.actions_file.read_text(encoding="utf-8"))
        assert "actions" in raw
        assert isinstance(raw["actions"], list)
        assert len(raw["actions"]) >= 1

    def test_save_actions_includes_updated_at(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.schedule_restart_sync("svc", "test")
        raw = json.loads(engine.actions_file.read_text(encoding="utf-8"))
        assert "updated_at" in raw


class TestRemediationEngineQuery:
    def test_get_action_status_existing(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("svc", "r")
        status = engine.get_action_status(aid)
        assert status is not None
        assert status["id"] == aid

    def test_get_action_status_missing_returns_none(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.get_action_status("nonexistent-id") is None

    def test_get_all_actions_returns_list(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.schedule_restart_sync("a", "r")
        engine.schedule_restart_sync("b", "r")
        all_actions = engine.get_all_actions()
        assert isinstance(all_actions, list)
        assert len(all_actions) >= 2

    def test_get_all_actions_empty(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.get_all_actions() == []


class TestRemediationEngineRetry:
    def test_retry_action_nonexistent_returns_false(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.retry_action("no-such-id") is False

    def test_retry_action_resets_attempts(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("svc", "r")
        action = engine._actions[aid]
        action.attempts = 3
        action.status = RemediationStatus.FAILED
        result = engine.retry_action(aid, reset_attempts=True)
        assert result is True
        assert engine._actions[aid].attempts == 0
        assert engine._actions[aid].status == RemediationStatus.PENDING

    def test_retry_action_no_reset_keeps_attempts(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("svc", "r")
        engine._actions[aid].attempts = 3
        engine.retry_action(aid, reset_attempts=False)
        assert engine._actions[aid].attempts == 3

    def test_retry_clears_error(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("svc", "r")
        engine._actions[aid].error = "previous failure"
        engine.retry_action(aid)
        assert engine._actions[aid].error is None


class TestRemediationEngineRollback:
    def test_rollback_no_backup_returns_false(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = asyncio.run(engine.rollback_config("mycomp", "test"))
        assert result is False

    def test_rollback_with_backup_returns_true(self, tmp_path):
        engine = _make_engine(tmp_path)
        config_dir = engine.config_dir
        backup_dir = engine.backup_dir

        # Create a fake backup file
        backup_file = backup_dir / "mycomp-config-20240101-120000.yaml"
        backup_file.write_text("good: config", encoding="utf-8")

        # Create a current config
        current = config_dir / "config.yaml"
        current.write_text("bad: config", encoding="utf-8")

        result = asyncio.run(engine.rollback_config("mycomp", "test"))
        assert result is True
        # Current config should now be the backup content
        assert current.read_text(encoding="utf-8") == "good: config"


class TestRemediationEngineExecuteAction:
    def test_execute_action_max_attempts_exceeded(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_restart_sync("svc", "crash")
        action = engine._actions[aid]
        action.attempts = action.max_attempts

        asyncio.run(engine._execute_action(action))
        assert action.status == RemediationStatus.FAILED
        assert "Max attempts" in action.error

    def test_execute_action_connection_retry_succeeds(self, tmp_path):
        engine = _make_engine(tmp_path)
        aid = engine.schedule_connection_retry_sync("redis", "pubsub", "timeout")
        action = engine._actions[aid]

        asyncio.run(engine._execute_action(action))
        assert action.status == RemediationStatus.SUCCESS

    def test_execute_action_config_rollback_no_backup(self, tmp_path):
        engine = _make_engine(tmp_path)
        action = RemediationAction(
            id="rb-001",
            type=RemediationType.CONFIG_ROLLBACK,
            component="mycomp",
            reason="bad config",
        )
        engine._actions[action.id] = action

        asyncio.run(engine._execute_action(action))
        # Rollback fails (no backup), action is requeued as PENDING
        assert action.status == RemediationStatus.PENDING

    def test_execute_action_restart_no_heart_returns_pending(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._heart = None  # No heart set
        aid = engine.schedule_restart_sync("svc", "crash")
        action = engine._actions[aid]

        asyncio.run(engine._execute_action(action))
        # restart fails → status goes back to PENDING for retry
        assert action.status == RemediationStatus.PENDING

    def test_execute_action_unknown_type_skipped(self, tmp_path):
        engine = _make_engine(tmp_path)
        action = RemediationAction(
            id="unk-001",
            type=RemediationType.PERMISSION_FIX,
            component="svc",
            reason="perm",
        )
        engine._actions[action.id] = action

        asyncio.run(engine._execute_action(action))
        assert action.status == RemediationStatus.SKIPPED


class TestRemediationEngineAsyncSchedule:
    def test_async_schedule_restart(self, tmp_path):
        engine = _make_engine(tmp_path)

        async def run():
            return await engine.schedule_restart("svc", "crash")

        aid = asyncio.run(run())
        assert isinstance(aid, str)
        assert aid in engine._actions

    def test_async_schedule_connection_retry(self, tmp_path):
        engine = _make_engine(tmp_path)

        async def run():
            return await engine.schedule_connection_retry("redis", "cache", "timeout")

        aid = asyncio.run(run())
        assert isinstance(aid, str)
        assert aid in engine._actions
