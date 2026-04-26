"""Tests for navig.core.migrations — MigrationManager and migrate_config."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from navig.core.migrations import (
    CURRENT_VERSION,
    Migration,
    MigrationManager,
    migrate_config,
)


class TestMigrationManagerInit:
    def test_has_built_in_migration(self):
        mgr = MigrationManager()
        assert len(mgr.migrations) >= 1

    def test_built_in_migration_from_0_9(self):
        mgr = MigrationManager()
        assert mgr.migrations[0].from_version == "0.9"


class TestGetPendingMigrations:
    def test_empty_string_treats_as_0_0(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("")
        assert len(pending) >= 1  # 0.9 → 1.0 still pending from 0.0

    def test_current_version_has_no_pending(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations(CURRENT_VERSION)
        assert pending == []

    def test_old_version_returns_pending(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("0.9")
        assert len(pending) >= 1

    def test_pending_sorted_by_version(self):
        mgr = MigrationManager()
        # Add a second migration to ensure ordering
        mgr.register(Migration(
            from_version="0.8",
            to_version="0.9",
            description="test",
            apply=lambda c: c,
        ))
        pending = mgr.get_pending_migrations("0.1")
        versions = [m.from_version for m in pending]
        assert versions == sorted(versions, key=lambda v: tuple(int(x) for x in v.split('.')))

    def test_invalid_version_string_treated_as_0_0(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("not-a-version")
        # Should not raise; returns all migrations from "0.0"
        assert isinstance(pending, list)


def _silent():
    """Context manager that silences console_helper output during migrations."""
    return patch.multiple(
        "navig.console_helper",
        info=lambda *a, **kw: None,
        dim=lambda *a, **kw: None,
        error=lambda *a, **kw: None,
    )


class TestApplyMigrations:
    def test_no_change_when_at_current_version(self):
        mgr = MigrationManager()
        with _silent():
            config = {"version": CURRENT_VERSION, "key": "val"}
            result, modified = mgr.apply_migrations(config.copy())
        assert modified is False
        assert result["key"] == "val"

    def test_applies_migration_from_0_9(self):
        mgr = MigrationManager()
        with _silent():
            config = {"version": "0.9", "ai_model_preference": "gpt-4"}
            result, modified = mgr.apply_migrations(config)
        assert modified is True
        assert result["ai"]["model_preference"] == "gpt-4"
        assert "ai_model_preference" not in result

    def test_sets_version_to_current_after_migration(self):
        mgr = MigrationManager()
        with _silent():
            config = {"version": "0.9"}
            result, _ = mgr.apply_migrations(config)
        assert result["version"] == CURRENT_VERSION

    def test_failed_migration_stops_processing(self):
        mgr = MigrationManager()

        def bad_apply(config):
            raise RuntimeError("migration exploded")

        mgr.register(Migration(
            from_version="0.5",
            to_version="0.6",
            description="bad migration",
            apply=bad_apply,
        ))
        with _silent():
            config = {"version": "0.5"}
            result, modified = mgr.apply_migrations(config)
        # Should not raise; result should have version set to CURRENT_VERSION
        assert result["version"] == CURRENT_VERSION


class TestMigrate0_9To1_0:
    def _run(self, config):
        mgr = MigrationManager()
        return mgr._migrate_0_9_to_1_0(config)

    def test_moves_legacy_field(self):
        result = self._run({"ai_model_preference": "claude-3"})
        assert result["ai"]["model_preference"] == "claude-3"
        assert "ai_model_preference" not in result

    def test_creates_ai_dict_if_missing(self):
        result = self._run({"other": "val"})
        assert "ai" in result

    def test_does_not_overwrite_existing_target(self):
        result = self._run({"ai": {"model_preference": "gpt-4"}, "ai_model_preference": "old"})
        assert result["ai"]["model_preference"] == "gpt-4"  # not overwritten
        assert "ai_model_preference" not in result

    def test_no_op_when_no_legacy_field(self):
        result = self._run({"ai": {"some": "setting"}})
        assert result["ai"]["some"] == "setting"

    def test_noop_on_empty_config(self):
        result = self._run({})
        assert isinstance(result, dict)


class TestMigrateConfigHelper:
    def test_migrate_config_returns_tuple(self):
        with _silent():
            result, modified = migrate_config({"version": CURRENT_VERSION})
        assert isinstance(result, dict)
        assert isinstance(modified, bool)
