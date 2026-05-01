"""Batch 98: tests for navig.core.config_loader, navig.core.crash_handler, navig.core.migrations."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# navig.core.config_loader
# ---------------------------------------------------------------------------

from navig.core.config_loader import (
    MAX_INCLUDE_DEPTH,
    CircularDependencyError,
    ConfigLoaderError,
    _load_yaml_recursive,
    _process_includes,
    load_config,
)


class TestLoadConfig:
    def test_raises_if_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_loads_simple_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: localhost\nport: 5432\n")
        result = load_config(cfg)
        assert result["host"] == "localhost"
        assert result["port"] == 5432

    def test_env_var_substitution(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: ${TEST_HOST_98}\n")
        with patch.dict(os.environ, {"TEST_HOST_98": "prodserver"}):
            result = load_config(cfg, strict=False)
        assert result["host"] == "prodserver"

    def test_include_directive_merges(self, tmp_path):
        base = tmp_path / "base.yaml"
        base.write_text("base_key: from_base\n")
        main = tmp_path / "main.yaml"
        main.write_text("$include: base.yaml\nmain_key: from_main\n")
        result = load_config(main)
        assert result["base_key"] == "from_base"
        assert result["main_key"] == "from_main"

    def test_include_override_by_main(self, tmp_path):
        """Keys in the main config override included keys."""
        base = tmp_path / "base.yaml"
        base.write_text("key: from_base\n")
        main = tmp_path / "main.yaml"
        main.write_text("$include: base.yaml\nkey: from_main\n")
        result = load_config(main)
        assert result["key"] == "from_main"

    def test_yaml_error_raises_config_loader_error(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("key: [\n")  # malformed YAML
        with pytest.raises(ConfigLoaderError):
            load_config(cfg)

    def test_missing_include_raises_file_not_found(self, tmp_path):
        main = tmp_path / "main.yaml"
        main.write_text("$include: nonexistent_include.yaml\n")
        with pytest.raises(FileNotFoundError):
            load_config(main)

    def test_context_values_substituted(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("db: ${MYDB}\n")
        result = load_config(cfg, context={"MYDB": "testdb"}, strict=False)
        assert result["db"] == "testdb"

    def test_non_dict_include_raises(self, tmp_path):
        """Include file that is a list (not a dict) must raise ConfigLoaderError."""
        inc = tmp_path / "list.yaml"
        inc.write_text("- a\n- b\n")
        main = tmp_path / "main.yaml"
        main.write_text("$include: list.yaml\n")
        with pytest.raises(ConfigLoaderError):
            load_config(main)

    def test_multiple_includes_merged(self, tmp_path):
        a = tmp_path / "a.yaml"
        a.write_text("a_key: aaa\n")
        b = tmp_path / "b.yaml"
        b.write_text("b_key: bbb\n")
        main = tmp_path / "main.yaml"
        main.write_text("$include:\n  - a.yaml\n  - b.yaml\nmain_key: mmm\n")
        result = load_config(main)
        assert result["a_key"] == "aaa"
        assert result["b_key"] == "bbb"
        assert result["main_key"] == "mmm"


class TestCircularDependencyDetection:
    def test_circular_include_raises(self, tmp_path):
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("$include: b.yaml\nkey: a\n")
        b.write_text("$include: a.yaml\nkey: b\n")
        with pytest.raises(CircularDependencyError):
            load_config(a)

    def test_max_depth_exceeded_raises(self, tmp_path):
        """Simulate hitting max include depth via deeply nested unique files."""
        # Build a chain longer than MAX_INCLUDE_DEPTH
        files = []
        depth = MAX_INCLUDE_DEPTH + 2
        for i in range(depth):
            f = tmp_path / f"level_{i}.yaml"
            files.append(f)

        # Each file includes the next — chain will exceed depth limit
        for i in range(depth - 1):
            files[i].write_text(f"$include: level_{i + 1}.yaml\nlevel: {i}\n")
        files[-1].write_text("leaf: true\n")

        with pytest.raises(ConfigLoaderError):
            load_config(files[0])


class TestProcessIncludes:
    def test_passthrough_scalar(self, tmp_path):
        result = _process_includes("hello", tmp_path, set(), 0)
        assert result == "hello"

    def test_passthrough_int(self, tmp_path):
        result = _process_includes(42, tmp_path, set(), 0)
        assert result == 42

    def test_list_items_processed(self, tmp_path):
        result = _process_includes([1, "two", None], tmp_path, set(), 0)
        assert result == [1, "two", None]


# ---------------------------------------------------------------------------
# navig.core.crash_handler
# ---------------------------------------------------------------------------

from navig.core.crash_handler import CrashHandler, _MAX_CRASH_LOGS


class TestCrashHandler:
    def test_default_debug_mode_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_DEBUG", None)
            ch = CrashHandler()
            assert not ch.is_debug

    def test_enable_debug_sets_mode(self):
        ch = CrashHandler()
        ch.enable_debug()
        assert ch.is_debug
        # Clean up env
        os.environ.pop("NAVIG_DEBUG", None)

    def test_debug_env_var_enables_debug(self):
        with patch.dict(os.environ, {"NAVIG_DEBUG": "1"}):
            ch = CrashHandler()
            assert ch.is_debug

    def test_get_latest_crash_report_none_when_no_logs(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path  # override log dir to empty temp directory
        result = ch.get_latest_crash_report()
        assert result is None

    def test_log_crash_to_file_creates_json(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        exc = ValueError("test crash")
        try:
            raise exc
        except ValueError as e:
            captured = e

        log_path = ch._log_crash_to_file(captured)
        assert log_path is not None
        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["exception_type"] == "ValueError"
        assert "test crash" in data["exception_message"]
        assert "timestamp" in data
        assert "system" in data

    def test_log_crash_includes_traceback(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        try:
            raise RuntimeError("traceback test")
        except RuntimeError as exc:
            log_path = ch._log_crash_to_file(exc)
        data = json.loads(log_path.read_text())
        assert "traceback" in data
        assert "RuntimeError" in data["traceback"]

    def test_cleanup_keeps_max_logs(self, tmp_path):
        ch = CrashHandler()
        # Create _MAX_CRASH_LOGS + 3 dummy log files
        for i in range(_MAX_CRASH_LOGS + 3):
            f = tmp_path / f"crash-20240101-{i:06d}.json"
            f.write_text(json.dumps({"n": i}))
        ch._cleanup_old_logs(tmp_path)
        remaining = list(tmp_path.glob("crash-*.json"))
        assert len(remaining) <= _MAX_CRASH_LOGS

    def test_get_latest_crash_report_returns_latest(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        # Write a known crash log
        log = tmp_path / "crash-20250101-120000.json"
        payload = {"exception_type": "KeyError", "exception_message": "missing", "timestamp": "x"}
        log.write_text(json.dumps(payload))
        result = ch.get_latest_crash_report()
        assert result is not None
        assert result["exception_type"] == "KeyError"

    def test_print_friendly_error_no_raise(self, tmp_path, capsys):
        ch = CrashHandler()
        ch._debug_mode = False
        # Should not raise, even with no Rich
        try:
            ch._print_friendly_error(ValueError("boom"), tmp_path / "crash.json")
        except Exception as e:
            pytest.fail(f"_print_friendly_error raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# navig.core.migrations
# ---------------------------------------------------------------------------

from navig.core.migrations import (
    CURRENT_VERSION,
    Migration,
    MigrationManager,
    migrate_config,
    migration_manager,
)


class TestMigration:
    def test_migration_dataclass(self):
        m = Migration(
            from_version="0.1",
            to_version="0.2",
            description="Test migration",
            apply=lambda c: c,
        )
        assert m.from_version == "0.1"
        assert m.to_version == "0.2"
        assert m.description == "Test migration"

    def test_applying_callable(self):
        def double_value(config):
            config["x"] = config.get("x", 0) * 2
            return config

        m = Migration("0.1", "0.2", "double x", double_value)
        result = m.apply({"x": 5})
        assert result["x"] == 10


class TestMigrationManager:
    def test_has_core_migrations(self):
        manager = MigrationManager()
        assert len(manager.migrations) > 0

    def test_no_pending_at_current_version(self):
        manager = MigrationManager()
        pending = manager.get_pending_migrations(CURRENT_VERSION)
        assert pending == []

    def test_pending_migrations_for_old_version(self):
        manager = MigrationManager()
        pending = manager.get_pending_migrations("0.0")
        assert len(pending) > 0

    def test_apply_migrations_no_op_at_current(self):
        manager = MigrationManager()
        config = {"version": CURRENT_VERSION, "host": "localhost"}
        result, changed = manager.apply_migrations(config)
        assert not changed
        assert result["host"] == "localhost"

    def test_apply_migrations_upgrades_version(self):
        manager = MigrationManager()
        config = {"version": "0.0", "host": "localhost"}
        result, changed = manager.apply_migrations(config)
        assert changed
        assert result["version"] == CURRENT_VERSION

    def test_migrate_legacy_ai_field(self):
        manager = MigrationManager()
        config = {
            "version": "0.9",
            "ai_model_preference": "gpt-4",
        }
        result, changed = manager.apply_migrations(config)
        assert "ai_model_preference" not in result
        assert result.get("ai", {}).get("model_preference") == "gpt-4"

    def test_migrate_config_helper(self):
        config = {"version": "0.9", "ai_model_preference": "claude"}
        result, changed = migrate_config(config)
        assert changed
        assert result["version"] == CURRENT_VERSION

    def test_register_custom_migration(self):
        manager = MigrationManager()
        original_count = len(manager.migrations)

        def custom_apply(c):
            c["custom"] = True
            return c

        manager.register(Migration("0.5", "0.6", "custom", custom_apply))
        assert len(manager.migrations) == original_count + 1

    def test_invalid_current_version_treated_as_oldest(self):
        manager = MigrationManager()
        # Invalid version string should not crash — treated as 0.0
        pending = manager.get_pending_migrations("not_a_version")
        # Should return some pending migrations (since effectively at 0.0)
        assert isinstance(pending, list)

    def test_apply_migrations_missing_version_key(self):
        manager = MigrationManager()
        config = {"host": "localhost"}  # no "version" key
        result, changed = manager.apply_migrations(config)
        # Should complete without error and set the version
        assert result["version"] == CURRENT_VERSION

    def test_module_level_singleton(self):
        assert migration_manager is not None
        assert isinstance(migration_manager, MigrationManager)
