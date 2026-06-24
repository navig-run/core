"""Tests for navig/core/migrations.py and navig/core/config_loader.py — batch 86."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from navig.core.migrations import (
    CURRENT_VERSION,
    Migration,
    MigrationManager,
    migrate_config,
)


# ---------------------------------------------------------------------------
# Migration dataclass
# ---------------------------------------------------------------------------

class TestMigration:
    def test_fields_stored(self):
        m = Migration(
            from_version="0.9",
            to_version="1.0",
            description="Test migration",
            apply=lambda c: c,
        )
        assert m.from_version == "0.9"
        assert m.to_version == "1.0"
        assert m.description == "Test migration"

    def test_apply_callable(self):
        applied = {}

        def fn(cfg):
            applied["ran"] = True
            return cfg

        m = Migration(from_version="0.1", to_version="0.2", description="x", apply=fn)
        m.apply({})
        assert applied["ran"]


# ---------------------------------------------------------------------------
# MigrationManager
# ---------------------------------------------------------------------------

class TestMigrationManagerRegister:
    def test_register_adds_migration(self):
        mgr = MigrationManager()
        initial_count = len(mgr.migrations)
        m = Migration("0.1", "0.2", "test", lambda c: c)
        mgr.register(m)
        assert len(mgr.migrations) == initial_count + 1

    def test_builtin_migrations_registered(self):
        mgr = MigrationManager()
        versions = [m.from_version for m in mgr.migrations]
        assert "0.9" in versions


class TestMigrationManagerGetPending:
    def test_no_pending_at_current_version(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations(CURRENT_VERSION)
        assert pending == []

    def test_pending_when_old_version(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("0.9")
        assert len(pending) >= 1

    def test_pending_when_very_old(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("0.0")
        assert len(pending) >= 1

    def test_empty_version_treated_as_0_0(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("")
        assert isinstance(pending, list)

    def test_invalid_version_falls_back(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("not-a-version")
        assert isinstance(pending, list)

    def test_pending_sorted_by_version(self):
        mgr = MigrationManager()
        mgr.register(Migration("0.5", "0.6", "extra", lambda c: c))
        pending = mgr.get_pending_migrations("0.0")
        from_vers = [m.from_version for m in pending]
        # Each element should be <= next
        for i in range(len(from_vers) - 1):
            from packaging import version as pv
            assert pv.parse(from_vers[i]) <= pv.parse(from_vers[i + 1])


class TestMigrationManagerApply:
    def test_no_migration_needed_at_current(self):
        mgr = MigrationManager()
        cfg = {"version": CURRENT_VERSION, "some": "value"}
        result, modified = mgr.apply_migrations(cfg)
        assert modified is False
        assert result["some"] == "value"

    def test_migration_applied_from_0_9(self):
        mgr = MigrationManager()
        cfg = {"version": "0.9", "ai_model_preference": "gpt-4"}
        result, modified = mgr.apply_migrations(cfg)
        assert modified is True
        assert result.get("version") == CURRENT_VERSION

    def test_migration_moves_ai_field(self):
        mgr = MigrationManager()
        cfg = {"version": "0.9", "ai_model_preference": "gpt-4"}
        result, _ = mgr.apply_migrations(cfg)
        assert "ai_model_preference" not in result
        assert result.get("ai", {}).get("model_preference") == "gpt-4"

    def test_version_updated_to_current(self):
        mgr = MigrationManager()
        cfg = {"version": "0.0"}
        result, _ = mgr.apply_migrations(cfg)
        assert result["version"] == CURRENT_VERSION

    def test_unknown_old_version_updates_version_tag(self):
        mgr = MigrationManager()
        cfg = {"version": "0.1"}  # No explicit migration from 0.1
        result, _ = mgr.apply_migrations(cfg)
        assert result.get("version") == CURRENT_VERSION


class TestMigrate09To10:
    def test_moves_ai_model_preference(self):
        mgr = MigrationManager()
        cfg = {"ai_model_preference": "claude-3"}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai_model_preference" not in result
        assert result["ai"]["model_preference"] == "claude-3"

    def test_creates_ai_key_if_missing(self):
        mgr = MigrationManager()
        cfg = {"ai_model_preference": "gpt-4"}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai" in result

    def test_does_not_override_existing_ai_model_preference(self):
        mgr = MigrationManager()
        cfg = {"ai_model_preference": "old", "ai": {"model_preference": "new"}}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert result["ai"]["model_preference"] == "new"
        assert "ai_model_preference" not in result

    def test_no_op_when_field_absent(self):
        mgr = MigrationManager()
        cfg = {"other": "val"}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai_model_preference" not in result
        assert "ai" in result


class TestMigrateConfigHelper:
    def test_returns_tuple_bool(self):
        cfg = {"version": CURRENT_VERSION}
        result, was_modified = migrate_config(cfg)
        assert isinstance(result, dict)
        assert isinstance(was_modified, bool)

    def test_modifies_old_config(self):
        cfg = {"version": "0.9", "ai_model_preference": "gpt-3"}
        _, was_modified = migrate_config(cfg)
        assert was_modified is True


# ---------------------------------------------------------------------------
# config_loader
# ---------------------------------------------------------------------------
from navig.core.config_loader import (
    MAX_INCLUDE_DEPTH,
    CircularDependencyError,
    ConfigLoaderError,
    load_config,
    _load_yaml_recursive,
    _process_includes,
)


class TestLoadConfigErrors:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": invalid: yaml: [")
        with pytest.raises(ConfigLoaderError):
            load_config(bad)


class TestLoadConfigBasic:
    def test_loads_simple_dict(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: value\n")
        result = load_config(cfg)
        assert result["key"] == "value"

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        result = load_config(cfg)
        assert result == {}

    def test_nested_structure_preserved(self, tmp_path):
        cfg = tmp_path / "nested.yaml"
        cfg.write_text("outer:\n  inner: 42\n")
        result = load_config(cfg)
        assert result["outer"]["inner"] == 42


class TestLoadConfigEnvSubstitution:
    def test_env_var_substituted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("greeting: ${MY_VAR}\n")
        result = load_config(cfg)
        assert result["greeting"] == "hello"

    def test_context_var_substituted(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: ${TARGET}\n")
        result = load_config(cfg, context={"TARGET": "myserver"})
        assert result["host"] == "myserver"


class TestLoadConfigIncludes:
    def test_include_merges_data(self, tmp_path):
        base = tmp_path / "base.yaml"
        base.write_text("from_base: 1\n")
        main = tmp_path / "main.yaml"
        main.write_text('$include: base.yaml\nfrom_main: 2\n')
        result = load_config(main)
        assert result["from_base"] == 1
        assert result["from_main"] == 2

    def test_main_overrides_included(self, tmp_path):
        base = tmp_path / "base.yaml"
        base.write_text("key: from_base\n")
        main = tmp_path / "main.yaml"
        main.write_text('$include: base.yaml\nkey: overridden\n')
        result = load_config(main)
        assert result["key"] == "overridden"

    def test_missing_include_raises(self, tmp_path):
        main = tmp_path / "main.yaml"
        main.write_text('$include: missing.yaml\nkey: val\n')
        with pytest.raises(FileNotFoundError):
            load_config(main)


class TestCircularDependency:
    def test_circular_include_raises(self, tmp_path):
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("$include: b.yaml\n")
        b.write_text("$include: a.yaml\n")
        with pytest.raises(CircularDependencyError):
            load_config(a)

    def test_max_depth_raises(self, tmp_path):
        # Create a deep include chain exceeding MAX_INCLUDE_DEPTH
        files = []
        for i in range(MAX_INCLUDE_DEPTH + 2):
            f = tmp_path / f"level_{i}.yaml"
            files.append(f)

        for i, f in enumerate(files[:-1]):
            f.write_text(f"$include: level_{i + 1}.yaml\nlevel: {i}\n")
        files[-1].write_text("leaf: true\n")

        with pytest.raises(ConfigLoaderError):
            load_config(files[0])


class TestProcessIncludes:
    def test_list_processed_recursively(self, tmp_path):
        data = [{"key": "val"}, 42, "str"]
        result = _process_includes(data, tmp_path, set(), 0)
        assert result == [{"key": "val"}, 42, "str"]

    def test_scalar_returned_unchanged(self, tmp_path):
        assert _process_includes(42, tmp_path, set(), 0) == 42
        assert _process_includes("hello", tmp_path, set(), 0) == "hello"
        assert _process_includes(None, tmp_path, set(), 0) is None

    def test_dict_without_include_returned(self, tmp_path):
        data = {"a": 1, "b": 2}
        result = _process_includes(data, tmp_path, set(), 0)
        assert result == {"a": 1, "b": 2}
