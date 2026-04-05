"""
Tests for navig.installer — planner, runner, contracts, core modules.

Run:  pytest tests/test_installer.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────── contracts ────────────────────────────────────────────


class TestContracts:
    def test_result_ok_applied(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="x", state=ModuleState.APPLIED)
        assert r.ok is True

    def test_result_ok_skipped(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="x", state=ModuleState.SKIPPED)
        assert r.ok is True

    def test_result_not_ok_failed(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="x", state=ModuleState.FAILED, error="boom")
        assert r.ok is False

    def test_action_defaults(self):
        from navig.installer.contracts import Action

        a = Action(id="a", description="test", module="m")
        assert a.reversible is True
        assert a.data == {}


# ─────────────────────── profiles ─────────────────────────────────────────────


class TestProfiles:
    def test_all_profiles_non_empty(self):
        from navig.installer.profiles import PROFILE_MODULES

        for name, mods in PROFILE_MODULES.items():
            assert len(mods) >= 1, f"Profile {name!r} has no modules"

    def test_node_subset_of_operator(self):
        from navig.installer.profiles import PROFILE_MODULES

        node_set = set(PROFILE_MODULES["node"])
        operator_set = set(PROFILE_MODULES["operator"])
        assert node_set.issubset(operator_set)

    def test_valid_profiles_matches_keys(self):
        from navig.installer.profiles import PROFILE_MODULES, VALID_PROFILES

        assert set(VALID_PROFILES) == set(PROFILE_MODULES.keys())

    def test_default_profile_is_valid(self):
        from navig.installer.profiles import DEFAULT_PROFILE, VALID_PROFILES

        assert DEFAULT_PROFILE in VALID_PROFILES


# ─────────────────────── planner ──────────────────────────────────────────────


class TestPlanner:
    def test_plan_node_profile(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.planner import plan

        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) >= 1  # at least one action

    def test_plan_invalid_profile_raises(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.planner import plan

        ctx = InstallerContext(profile="nonexistent", config_dir=tmp_path)
        with pytest.raises(ValueError, match="Unknown installer profile"):
            plan(ctx)

    def test_plan_missing_module_produces_placeholder(self, tmp_path, monkeypatch):
        """A profile module that doesn't exist yet yields a placeholder, not an error."""
        from navig.installer import planner
        from navig.installer.contracts import InstallerContext

        monkeypatch.setattr(
            planner,
            "PROFILE_MODULES",
            {"test_profile": ["nonexistent_module_xyz"]},
        )
        monkeypatch.setattr(planner, "VALID_PROFILES", ["test_profile"])

        ctx = InstallerContext(profile="test_profile", config_dir=tmp_path)
        actions = planner.plan(ctx)
        assert len(actions) == 1
        assert actions[0].data.get("placeholder") is True


# ─────────────────────── runner ───────────────────────────────────────────────


class TestRunner:
    def test_dry_run_returns_skipped(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.runner import apply

        ctx = InstallerContext(profile="node", dry_run=True, config_dir=tmp_path)
        actions = [Action(id="a.1", description="test", module="config_paths")]
        results = apply(actions, ctx)
        assert len(results) == 1
        assert results[0].state == ModuleState.SKIPPED

    def test_placeholder_actions_skipped(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.runner import apply

        ctx = InstallerContext(profile="node", dry_run=False, config_dir=tmp_path)
        actions = [
            Action(
                id="x.ph",
                description="placeholder",
                module="nonexistent",
                data={"placeholder": True},
            )
        ]
        results = apply(actions, ctx)
        assert results[0].state == ModuleState.SKIPPED

    def test_halts_on_first_failure(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.runner import apply

        # Patch core_cli.apply to fail
        with patch("navig.installer.modules.core_cli.apply") as mock_apply:
            from navig.installer.contracts import Result

            mock_apply.return_value = Result(
                action_id="core_cli.verify",
                state=ModuleState.FAILED,
                error="simulated failure",
            )

            ctx = InstallerContext(profile="node", dry_run=False, config_dir=tmp_path)
            # Build two sequential actions; second should not be reached
            actions = [
                Action(id="core_cli.verify", description="check cli", module="core_cli"),
                Action(
                    id="config_paths.mkdir.root",
                    description="mkdir root",
                    module="config_paths",
                ),
            ]
            results = apply(actions, ctx)
            assert len(results) == 1
            assert results[0].state == ModuleState.FAILED

    def test_rollback_calls_module_rollback(self, tmp_path):
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )
        from navig.installer.runner import rollback

        with patch("navig.installer.modules.shell_integration.rollback") as mock_rb:
            action = Action(
                id="shell_integration.bashrc",
                description="add to PATH",
                module="shell_integration",
                reversible=True,
            )
            result = Result(
                action_id=action.id,
                state=ModuleState.APPLIED,
                undo_data={"rc": str(tmp_path / ".bashrc"), "snippet": "# navig"},
            )
            ctx = InstallerContext(profile="operator", config_dir=tmp_path)
            rollback([action], [result], ctx)
            mock_rb.assert_called_once()


# ─────────────────────── module: config_paths ─────────────────────────────────


class TestConfigPathsModule:
    def test_plan_creates_actions_for_missing_dirs(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.config_paths import plan

        ctx = InstallerContext(profile="node", config_dir=tmp_path / "new_navig")
        actions = plan(ctx)
        # root + at least a few subdirs should be planned
        assert len(actions) >= 3

    def test_apply_creates_directory(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.config_paths import apply

        d = tmp_path / "subdir"
        assert not d.exists()
        action = Action(
            id="config_paths.mkdir.subdir",
            description=f"Create {d}",
            module="config_paths",
            data={"path": str(d), "existed": False},
        )
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        result = apply(action, ctx)
        assert d.exists()
        assert result.state == ModuleState.APPLIED

    def test_rollback_removes_created_dir(self, tmp_path):
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )
        from navig.installer.modules.config_paths import rollback

        d = tmp_path / "new_empty"
        d.mkdir()
        action = Action(
            id="config_paths.mkdir.new_empty",
            description=f"Create {d}",
            module="config_paths",
            data={"path": str(d), "existed": False},
        )
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"path": str(d), "existed": False},
        )
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        rollback(action, result, ctx)
        assert not d.exists()

    def test_rollback_skips_preexisting_dir(self, tmp_path):
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )
        from navig.installer.modules.config_paths import rollback

        action = Action(
            id="config_paths.mkdir.root",
            description=f"Create {tmp_path}",
            module="config_paths",
            data={"path": str(tmp_path), "existed": True},
        )
        result = Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            undo_data={"path": str(tmp_path), "existed": True},
        )
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        rollback(action, result, ctx)
        assert tmp_path.exists()  # not removed


# ─────────────────────── module: core_cli ─────────────────────────────────────


class TestCoreCLIModule:
    def test_plan_returns_one_action(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.core_cli import plan

        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) == 1
        assert actions[0].reversible is False

    def test_apply_success_when_navig_on_path(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules import core_cli

        action = Action(id="core_cli.verify", description="check", module="core_cli")
        ctx = InstallerContext(profile="node", config_dir=tmp_path)

        with patch("shutil.which", return_value="/usr/local/bin/navig"):
            with patch.object(core_cli, "_navig_version", return_value="1.2.3"):
                result = core_cli.apply(action, ctx)

        assert result.state == ModuleState.APPLIED
        assert "1.2.3" in result.message

    def test_apply_failure_when_navig_not_found(self, tmp_path):
        import subprocess as sp

        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules import core_cli

        action = Action(id="core_cli.verify", description="check", module="core_cli")
        ctx = InstallerContext(profile="node", config_dir=tmp_path)

        with patch("shutil.which", return_value=None):
            with patch.object(sp, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = core_cli.apply(action, ctx)

        assert result.state == ModuleState.FAILED


# ─────────────────────── module: shell_integration ────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="shell rc files are Unix-only")
class TestShellIntegrationModule:
    def test_plan_skips_already_integrated_rc(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.shell_integration import _MARKER, plan

        rc = tmp_path / ".bashrc"
        rc.write_text(f"export FOO=bar\n{_MARKER}\n", encoding="utf-8")

        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        with patch(
            "navig.installer.modules.shell_integration._shell_rc_candidates",
            return_value=[rc],
        ):
            with patch(
                "navig.installer.modules.shell_integration._navig_bin_dir",
                return_value=Path("/usr/local/bin"),
            ):
                actions = plan(ctx)

        assert actions == []  # already integrated, nothing to do

    def test_apply_and_rollback(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.shell_integration import apply, rollback

        rc = tmp_path / ".bashrc"
        rc.write_text("export FOO=bar\n", encoding="utf-8")

        action = Action(
            id="shell_integration.bashrc",
            description="add PATH",
            module="shell_integration",
            data={"rc": str(rc), "bin_dir": "/home/user/.local/bin"},
        )
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)

        result = apply(action, ctx)
        assert result.state == ModuleState.APPLIED
        content = rc.read_text()
        assert "# navig shell integration" in content

        # rollback must remove the snippet
        rollback(action, result, ctx)
        content_after = rc.read_text()
        assert "# navig shell integration" not in content_after


# ─────────────────────── state ────────────────────────────────────────────────


class TestState:
    def test_save_and_load_last(self, tmp_path):
        from navig.installer import state as st
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )

        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = [
            Action(
                id="config_paths.mkdir.root",
                description="Create dir",
                module="config_paths",
            )
        ]
        results = [
            Result(
                action_id="config_paths.mkdir.root",
                state=ModuleState.APPLIED,
                message="ok",
            )
        ]
        manifest = st.save(actions, results, ctx)
        assert manifest.exists()

        records = st.load_last(tmp_path, profile="node")
        assert len(records) == 1
        assert records[0]["action_id"] == "config_paths.mkdir.root"
        assert records[0]["state"] == "applied"

    def test_load_last_empty_when_no_history(self, tmp_path):
        from navig.installer import state as st

        records = st.load_last(tmp_path, profile="node")
        assert records == []


# ─────────────────────── run_install integration ──────────────────────────────


class TestRunInstall:
    def test_dry_run_node_profile(self, tmp_path):
        from navig.installer import run_install
        from navig.installer.contracts import ModuleState

        results = run_install(
            profile="node",
            dry_run=True,
            quiet=True,
            config_dir=tmp_path,
        )
        # All results should be SKIPPED in dry_run
        assert all(r.state == ModuleState.SKIPPED for r in results)

    def test_invalid_profile_raises(self, tmp_path):
        from navig.installer import run_install

        with pytest.raises(ValueError):
            run_install(profile="does_not_exist", quiet=True, config_dir=tmp_path)

    def test_operator_dry_run_produces_vault_action(self, tmp_path):
        from navig.installer import run_install

        results = run_install(
            profile="operator",
            dry_run=True,
            quiet=True,
            config_dir=tmp_path,
        )
        action_ids = [r.action_id for r in results]
        assert any("vault_bootstrap" in aid for aid in action_ids)

    def test_operator_dry_run_has_telegram_action(self, tmp_path):
        """telegram module emits a SKIPPED action when no token — still present."""
        from navig.installer import run_install

        results = run_install(
            profile="operator",
            dry_run=True,
            quiet=True,
            config_dir=tmp_path,
        )
        action_ids = [r.action_id for r in results]
        assert any("telegram" in aid for aid in action_ids)


# ─────────────────────── telegram module ─────────────────────────────────────


class TestTelegramModule:
    def test_plan_no_token_returns_skip_action(self, tmp_path, monkeypatch):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.telegram import plan

        monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) == 1
        assert actions[0].data.get("skipped") is True
        assert actions[0].reversible is False

    def test_plan_returns_empty_when_already_configured(self, tmp_path, monkeypatch):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.telegram import plan

        marker = tmp_path / ".telegram_configured"
        marker.write_text("1")
        monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        assert plan(ctx) == []

    def test_plan_with_token_from_env(self, tmp_path, monkeypatch):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.telegram import plan

        monkeypatch.setenv("NAVIG_TELEGRAM_BOT_TOKEN", "1234567890:AABBCCDDEEFFaabbccddeeff")
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) == 1
        assert actions[0].data.get("token") == "1234567890:AABBCCDDEEFFaabbccddeeff"
        assert actions[0].reversible is True

    def test_plan_with_token_from_legacy_env(self, tmp_path, monkeypatch):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.telegram import plan

        monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token-xyz")
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) == 1
        assert actions[0].data.get("token") == "legacy-token-xyz"
        assert actions[0].reversible is True

    def test_plan_with_token_from_extra(self, tmp_path, monkeypatch):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.telegram import plan

        monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        ctx = InstallerContext(
            profile="operator",
            config_dir=tmp_path,
            extra={"telegram_bot_token": "bot-from-extra"},
        )
        actions = plan(ctx)
        assert actions[0].data["token"] == "bot-from-extra"

    def test_apply_skip_action_returns_skipped(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.telegram import apply

        action = Action(
            id="telegram.skip",
            description="telegram: skip",
            module="telegram",
            data={"skipped": True},
            reversible=False,
        )
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        result = apply(action, ctx)
        assert result.state == ModuleState.SKIPPED

    def test_apply_writes_env_and_marker(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.telegram import apply

        action = Action(
            id="telegram.write",
            description="telegram: write token",
            module="telegram",
            data={"token": "test-token-123"},
            reversible=True,
        )
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        result = apply(action, ctx)
        # vault may be unavailable in test env — that is fine; .env must still be written
        assert result.state == ModuleState.APPLIED
        assert (tmp_path / ".telegram_configured").exists()
        env_content = (tmp_path / ".env").read_text()
        assert "TELEGRAM_BOT_TOKEN=test-token-123" in env_content

    def test_apply_does_not_write_token_to_config_yaml(self, tmp_path):
        """Verify that apply() never writes bot_token to config.yaml (security fix)."""
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.telegram import apply

        action = Action(
            id="telegram.write",
            description="telegram: write token",
            module="telegram",
            data={"token": "secret-token-abc"},
            reversible=True,
        )
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)
        result = apply(action, ctx)
        assert result.state == ModuleState.APPLIED
        config_yaml = tmp_path / "config.yaml"
        # config.yaml must not be created by apply()
        assert not config_yaml.exists(), "apply() must not write bot_token to config.yaml"

    def test_rollback_removes_marker_and_scrubs_env(self, tmp_path, monkeypatch):
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )
        from navig.installer.modules.telegram import rollback

        marker = tmp_path / ".telegram_configured"
        marker.write_text("1")
        env_path = tmp_path / ".env"
        env_path.write_text("FOO=bar\nTELEGRAM_BOT_TOKEN=old\n")

        action = Action(
            id="telegram.write",
            description="telegram",
            module="telegram",
            data={"token": "old"},
            reversible=True,
        )
        result = Result(
            action_id="telegram.write",
            state=ModuleState.APPLIED,
            undo_data={
                "env_path": str(env_path),
            },
        )
        ctx = InstallerContext(profile="operator", config_dir=tmp_path)

        rollback(action, result, ctx)
        assert not marker.exists()
        assert "TELEGRAM_BOT_TOKEN" not in env_path.read_text()


# ─────────────────────── mcp module ──────────────────────────────────────────


class TestMcpModule:
    def test_plan_returns_empty_when_config_exists(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.mcp import plan

        (tmp_path / "mcp_servers.yaml").write_text("servers: []")
        ctx = InstallerContext(profile="architect", config_dir=tmp_path)
        assert plan(ctx) == []

    def test_plan_returns_action_when_missing(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.mcp import plan

        ctx = InstallerContext(profile="architect", config_dir=tmp_path)
        actions = plan(ctx)
        assert len(actions) == 1
        assert actions[0].reversible is True

    def test_apply_creates_stub_config(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext, ModuleState
        from navig.installer.modules.mcp import apply

        action = Action(
            id="mcp.init_config",
            description="mcp: create config",
            module="mcp",
            data={"config_path": str(tmp_path / "mcp_servers.yaml")},
            reversible=True,
        )
        ctx = InstallerContext(profile="architect", config_dir=tmp_path)
        result = apply(action, ctx)
        assert result.state in (ModuleState.APPLIED, ModuleState.SKIPPED)
        if result.state == ModuleState.APPLIED:
            assert (tmp_path / "mcp_servers.yaml").exists()

    def test_rollback_removes_created_config(self, tmp_path):
        from navig.installer.contracts import (
            Action,
            InstallerContext,
            ModuleState,
            Result,
        )
        from navig.installer.modules.mcp import rollback

        cfg = tmp_path / "mcp_servers.yaml"
        cfg.write_text("servers: []")
        action = Action(
            id="mcp.init_config",
            description="mcp",
            module="mcp",
            data={"config_path": str(cfg)},
        )
        result = Result(
            action_id="mcp.init_config",
            state=ModuleState.APPLIED,
            undo_data={"config_path": str(cfg), "created": True},
        )
        ctx = InstallerContext(profile="architect", config_dir=tmp_path)
        rollback(action, result, ctx)
        assert not cfg.exists()


# ─────────────────────── profile coverage ────────────────────────────────────


class TestProfileCoverage:
    def test_operator_includes_telegram(self):
        from navig.installer.profiles import PROFILE_MODULES

        assert "telegram" in PROFILE_MODULES["operator"]

    def test_architect_includes_mcp(self):
        from navig.installer.profiles import PROFILE_MODULES

        assert "mcp" in PROFILE_MODULES["architect"]

    def test_system_standard_includes_service(self):
        from navig.installer.profiles import PROFILE_MODULES

        assert "service" in PROFILE_MODULES["system_standard"]

    def test_system_deep_includes_tray(self):
        from navig.installer.profiles import PROFILE_MODULES

        assert "tray" in PROFILE_MODULES["system_deep"]
