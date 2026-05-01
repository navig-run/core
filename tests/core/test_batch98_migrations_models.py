"""Batch 98 — migrations and models modules.

Tests:
- navig.core.migrations (CURRENT_VERSION, Migration, MigrationManager,
  migrate_config, _migrate_0_9_to_1_0)
- navig.core.models (CommandParameter, NavigCommand, SkillManifest, NavigPack)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from navig.core.migrations import (
    CURRENT_VERSION,
    Migration,
    MigrationManager,
    migrate_config,
)
from navig.core.models import (
    CommandParameter,
    NavigCommand,
    NavigPack,
    PackStep,
    SkillExample,
    SkillManifest,
)


# ===========================================================================
# CURRENT_VERSION
# ===========================================================================


class TestCurrentVersion:
    def test_current_version_is_string(self):
        assert isinstance(CURRENT_VERSION, str)

    def test_current_version_nonempty(self):
        assert CURRENT_VERSION != ""


# ===========================================================================
# Migration dataclass
# ===========================================================================


class TestMigrationDataclass:
    def test_creation(self):
        m = Migration(
            from_version="0.9",
            to_version="1.0",
            description="Test migration",
            apply=lambda cfg: cfg,
        )
        assert m.from_version == "0.9"
        assert m.to_version == "1.0"
        assert m.description == "Test migration"

    def test_apply_callable(self):
        called = []
        m = Migration(
            from_version="0.9",
            to_version="1.0",
            description="Test",
            apply=lambda cfg: called.append(1) or cfg,
        )
        cfg = {"key": "val"}
        result = m.apply(cfg)
        assert result == cfg
        assert called == [1]


# ===========================================================================
# MigrationManager — registration and get_pending
# ===========================================================================


class TestMigrationManagerRegistration:
    def test_new_manager_has_core_migrations(self):
        mgr = MigrationManager()
        assert len(mgr.migrations) >= 1

    def test_register_adds_migration(self):
        mgr = MigrationManager()
        before = len(mgr.migrations)
        mgr.register(
            Migration(
                from_version="0.8",
                to_version="0.9",
                description="Test",
                apply=lambda cfg: cfg,
            )
        )
        assert len(mgr.migrations) == before + 1

    def test_get_pending_empty_for_current_version(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations(CURRENT_VERSION)
        assert pending == []

    def test_get_pending_returns_list(self):
        mgr = MigrationManager()
        result = mgr.get_pending_migrations("0.0")
        assert isinstance(result, list)

    def test_get_pending_for_old_version(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("0.0")
        assert len(pending) >= 1

    def test_get_pending_sorted_by_version(self):
        mgr = MigrationManager()
        mgr.register(
            Migration(
                from_version="0.5",
                to_version="0.6",
                description="Older",
                apply=lambda cfg: cfg,
            )
        )
        pending = mgr.get_pending_migrations("0.0")
        from_versions = [m.from_version for m in pending]
        # versions should be non-decreasing
        from packaging import version as pv
        for i in range(len(from_versions) - 1):
            assert pv.parse(from_versions[i]) <= pv.parse(from_versions[i + 1])

    def test_get_pending_empty_string_version_treated_as_old(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("")
        assert len(pending) >= 1

    def test_get_pending_invalid_version_treated_as_old(self):
        mgr = MigrationManager()
        pending = mgr.get_pending_migrations("not-a-version")
        assert len(pending) >= 1


# ===========================================================================
# MigrationManager — apply_migrations
# ===========================================================================


class TestMigrationManagerApply:
    def _no_console(self):
        """Context manager that suppresses console output."""
        return patch.multiple(
            "navig.console_helper",
            info=lambda *a, **k: None,
            dim=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )

    def test_no_migration_needed_for_current_version(self):
        mgr = MigrationManager()
        cfg = {"version": CURRENT_VERSION, "key": "val"}
        result, modified = mgr.apply_migrations(cfg)
        assert modified is False
        assert result["version"] == CURRENT_VERSION

    def test_returns_tuple(self):
        mgr = MigrationManager()
        cfg = {"version": CURRENT_VERSION}
        result = mgr.apply_migrations(cfg)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_migrated_config_has_updated_version(self):
        mgr = MigrationManager()
        cfg = {"version": "0.9"}
        with self._no_console():
            result, modified = mgr.apply_migrations(cfg)
        assert result["version"] == CURRENT_VERSION

    def test_migration_was_modified_true(self):
        mgr = MigrationManager()
        cfg = {"version": "0.9"}
        with self._no_console():
            _, modified = mgr.apply_migrations(cfg)
        assert modified is True

    def test_config_without_version_field_migrated(self):
        mgr = MigrationManager()
        cfg = {}  # no version key
        with self._no_console():
            result, modified = mgr.apply_migrations(cfg)
        assert result["version"] == CURRENT_VERSION

    def test_migration_still_completes_on_apply_error(self):
        mgr = MigrationManager()
        bad_migration = Migration(
            from_version="0.8",
            to_version="0.9",
            description="Broken",
            apply=lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        mgr.register(bad_migration)
        cfg = {"version": "0.8"}
        with self._no_console():
            result, _ = mgr.apply_migrations(cfg)
        # Should still return a config dict
        assert isinstance(result, dict)


# ===========================================================================
# _migrate_0_9_to_1_0
# ===========================================================================


class TestMigrate09To10:
    def _mgr(self):
        return MigrationManager()

    def test_moves_ai_model_preference(self):
        mgr = self._mgr()
        cfg = {"ai_model_preference": "gpt-4"}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai_model_preference" not in result
        assert result["ai"]["model_preference"] == "gpt-4"

    def test_creates_ai_dict_if_missing(self):
        mgr = self._mgr()
        cfg = {"ai_model_preference": "claude"}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai" in result

    def test_does_not_overwrite_existing_model_preference(self):
        mgr = self._mgr()
        cfg = {
            "ai_model_preference": "gpt-4",
            "ai": {"model_preference": "existing"},
        }
        result = mgr._migrate_0_9_to_1_0(cfg)
        # Legacy removed, ai.model_preference preserved
        assert "ai_model_preference" not in result
        assert result["ai"]["model_preference"] == "existing"

    def test_no_legacy_field_returns_unchanged_ai(self):
        mgr = self._mgr()
        cfg = {"ai": {"model": "gpt-4"}}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "model" in result["ai"]

    def test_empty_config_handled(self):
        mgr = self._mgr()
        cfg = {}
        result = mgr._migrate_0_9_to_1_0(cfg)
        assert "ai" in result


# ===========================================================================
# migrate_config (module-level helper)
# ===========================================================================


class TestMigrateConfig:
    def test_returns_tuple(self):
        cfg = {"version": CURRENT_VERSION}
        result = migrate_config(cfg)
        assert isinstance(result, tuple)

    def test_no_op_for_current_version(self):
        cfg = {"version": CURRENT_VERSION, "data": 1}
        result, modified = migrate_config(cfg)
        assert modified is False


# ===========================================================================
# CommandParameter (models)
# ===========================================================================


class TestCommandParameter:
    def test_required_fields(self):
        p = CommandParameter(type="string", description="A param")
        assert p.type == "string"
        assert p.description == "A param"

    def test_defaults(self):
        p = CommandParameter(type="integer", description="Count")
        assert p.required is False
        assert p.default is None
        assert p.options is None

    def test_with_options(self):
        p = CommandParameter(
            type="string",
            description="Mode",
            options=["fast", "slow"],
        )
        assert p.options == ["fast", "slow"]


# ===========================================================================
# NavigCommand
# ===========================================================================


class TestNavigCommand:
    def test_required_fields(self):
        cmd = NavigCommand(name="run", syntax="navig run", description="Run a command")
        assert cmd.name == "run"
        assert cmd.syntax == "navig run"

    def test_defaults(self):
        cmd = NavigCommand(name="run", syntax="navig run", description="Run")
        assert cmd.risk == "safe"
        assert cmd.confirmation_required is False
        assert cmd.confirmation_msg is None
        assert cmd.parameters is None
        assert cmd.source_skill is None

    def test_custom_risk(self):
        cmd = NavigCommand(name="rm", syntax="navig rm", description="Remove", risk="destructive")
        assert cmd.risk == "destructive"

    def test_with_parameters(self):
        cmd = NavigCommand(
            name="run",
            syntax="navig run",
            description="Run",
            parameters={"cmd": CommandParameter(type="string", description="Command")},
        )
        assert "cmd" in cmd.parameters


# ===========================================================================
# SkillManifest
# ===========================================================================


class TestSkillManifest:
    def test_required_fields(self):
        m = SkillManifest(name="my-skill", description="Does stuff")
        assert m.name == "my-skill"
        assert m.description == "Does stuff"

    def test_defaults(self):
        m = SkillManifest(name="skill", description="desc")
        assert m.version == "0.0.1"
        assert m.author is None
        assert m.category == "uncategorized"
        assert m.requires == []
        assert m.tags == []

    def test_risk_level_alias(self):
        m = SkillManifest.model_validate(
            {"name": "s", "description": "d", "risk-level": "moderate"}
        )
        assert m.risk_level == "moderate"

    def test_user_invocable_alias(self):
        m = SkillManifest.model_validate(
            {"name": "s", "description": "d", "user-invocable": False}
        )
        assert m.user_invocable is False


# ===========================================================================
# NavigPack
# ===========================================================================


class TestNavigPack:
    def test_required_fields(self):
        p = NavigPack(name="my-pack", description="A pack")
        assert p.name == "my-pack"
        assert p.description == "A pack"

    def test_defaults(self):
        p = NavigPack(name="pack", description="desc")
        assert p.version == "1.0.0"
        assert p.author == "unknown"
        assert p.type == "runbook"
        assert p.tags == []
        assert p.steps == []

    def test_with_steps(self):
        step = PackStep(name="step1", command="ls -la")
        pack = NavigPack(name="pack", description="desc", steps=[step])
        assert len(pack.steps) == 1
        assert pack.steps[0].command == "ls -la"


# ===========================================================================
# PackStep
# ===========================================================================


class TestPackStep:
    def test_required_command(self):
        s = PackStep(command="echo hello")
        assert s.command == "echo hello"

    def test_defaults(self):
        s = PackStep(command="ls")
        assert s.name == "unnamed-step"
        assert s.description is None
        assert s.continue_on_error is False
