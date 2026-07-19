"""Batch 63 — commands/debug_cmd, installer/core_cli, installer/migrate_legacy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.commands.debug_cmd
# ---------------------------------------------------------------------------

class TestDebugApp:
    @pytest.fixture(autouse=True)
    def runner(self):
        from typer.testing import CliRunner
        self.runner = CliRunner()

    def _invoke(self, args=None):
        from navig.commands.debug_cmd import debug_app
        return self.runner.invoke(debug_app, args or [])

    @staticmethod
    def _isolate(tmp_path):
        """Redirect the debug command at tmp_path — patching the seams it ACTUALLY uses.

        These tests used to patch only ``debug_cmd.config_dir``. Then debug_cmd was
        fixed to read ``debug_log_path()`` (the real path the logger writes) instead of
        ``config_dir()/debug.log`` — so the config_dir patch became a NO-OP, and every
        test silently fell through to the operator's REAL logs. Two consequences:
        the assertions read the wrong file (spurious failures), and — far worse —
        ``clear --yes`` truncated the operator's actual debug.log on every test run.

        Patch the real seams so the tests control what the command sees, and nothing
        outside tmp_path is ever touched.
        """
        return (
            patch("navig.commands.debug_cmd.debug_log_path", return_value=tmp_path / "debug.log"),
            patch("navig.commands.debug_cmd.log_dir", return_value=tmp_path),
            patch("navig.commands.debug_cmd.config_dir", return_value=tmp_path),
        )

    def _invoke_isolated(self, tmp_path, args=None):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in self._isolate(tmp_path):
                stack.enter_context(p)
            return self._invoke(args)

    def test_default_no_debug_log(self, tmp_path):
        result = self._invoke_isolated(tmp_path, [])
        assert result.exit_code == 0
        assert "No debug.log" in result.output

    def test_default_with_debug_log(self, tmp_path):
        (tmp_path / "debug.log").write_text("some debug info")
        result = self._invoke_isolated(tmp_path, [])
        assert result.exit_code == 0
        assert "debug.log" in result.output

    def test_tail_no_log(self, tmp_path):
        result = self._invoke_isolated(tmp_path, ["tail"])
        assert result.exit_code == 0
        assert "No debug.log" in result.output

    def test_tail_returns_last_lines(self, tmp_path):
        lines = [f"line {i}" for i in range(100)]
        (tmp_path / "debug.log").write_text("\n".join(lines))
        result = self._invoke_isolated(tmp_path, ["tail", "--lines", "5"])
        assert result.exit_code == 0
        assert "line 99" in result.output
        assert "line 95" in result.output

    def test_clear_no_log(self, tmp_path):
        result = self._invoke_isolated(tmp_path, ["clear", "--yes"])
        assert result.exit_code == 0
        assert "Nothing to clear" in result.output

    def test_clear_with_yes_clears_log(self, tmp_path):
        log = tmp_path / "debug.log"
        log.write_text("old content")
        result = self._invoke_isolated(tmp_path, ["clear", "--yes"])
        assert result.exit_code == 0
        assert log.read_text() == ""

    def test_clear_confirms_message(self, tmp_path):
        (tmp_path / "debug.log").write_text("data")
        result = self._invoke_isolated(tmp_path, ["clear", "--yes"])
        assert "cleared" in result.output


# ---------------------------------------------------------------------------
# navig.installer.modules.core_cli
# ---------------------------------------------------------------------------

class TestCoreCLIPlan:
    def test_plan_returns_one_action(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules import core_cli
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = core_cli.plan(ctx)
        assert len(actions) == 1

    def test_plan_action_id(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules import core_cli
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        action = core_cli.plan(ctx)[0]
        assert action.id == "core_cli.verify"

    def test_plan_not_reversible(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules import core_cli
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        action = core_cli.plan(ctx)[0]
        assert action.reversible is False


class TestCoreCLIApply:
    def _make(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        action = Action(id="core_cli.verify", description="verify", module="core_cli", reversible=False)
        return action, ctx

    def test_apply_navig_on_path_returns_applied(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.modules import core_cli
        action, ctx = self._make(tmp_path)
        with patch("navig.installer.modules.core_cli.shutil.which", return_value="/usr/bin/navig"):
            result = core_cli.apply(action, ctx)
        assert result.state == ModuleState.APPLIED
        assert "navig" in result.message.lower()

    def test_apply_not_on_path_subprocess_ok(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.modules import core_cli
        action, ctx = self._make(tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch("navig.installer.modules.core_cli.shutil.which", return_value=None),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = core_cli.apply(action, ctx)
        assert result.state == ModuleState.APPLIED

    def test_apply_not_found_returns_failed(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.modules import core_cli
        action, ctx = self._make(tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        with (
            patch("navig.installer.modules.core_cli.shutil.which", return_value=None),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = core_cli.apply(action, ctx)
        assert result.state == ModuleState.FAILED

    def test_navig_version_returns_string(self):
        from navig.installer.modules.core_cli import _navig_version
        v = _navig_version()
        assert isinstance(v, str)

    def test_navig_version_fallback_on_error(self):
        from navig.installer.modules.core_cli import _navig_version
        with patch("importlib.metadata.version", side_effect=Exception("no pkg")):
            v = _navig_version()
        assert v == "unknown"


# ---------------------------------------------------------------------------
# navig.installer.modules.migrate_legacy
# ---------------------------------------------------------------------------

class TestMigrateLegacyPlan:
    def test_plan_returns_one_action(self, tmp_path):
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules import migrate_legacy
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        actions = migrate_legacy.plan(ctx)
        assert len(actions) == 1
        assert actions[0].id == "migrate_legacy.run"


class TestMigrateLegacyApply:
    def _make(self, tmp_path):
        from navig.installer.contracts import Action, InstallerContext
        ctx = InstallerContext(profile="node", config_dir=tmp_path)
        action = Action(id="migrate_legacy.run", description="migrate", module="migrate_legacy", reversible=False)
        return action, ctx

    def test_apply_both_succeed_returns_applied(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.modules import migrate_legacy
        action, ctx = self._make(tmp_path)
        with (
            patch("navig.commands.init._migrate_legacy_windows_runtime_layout"),
            patch("navig.commands.init._migrate_legacy_documents_dir"),
        ):
            result = migrate_legacy.apply(action, ctx)
        assert result.state == ModuleState.APPLIED

    def test_apply_both_fail_returns_skipped(self, tmp_path):
        from navig.installer.contracts import ModuleState
        from navig.installer.modules import migrate_legacy
        action, ctx = self._make(tmp_path)
        with (
            patch("navig.commands.init._migrate_legacy_windows_runtime_layout",
                  side_effect=Exception("no")),
            patch("navig.commands.init._migrate_legacy_documents_dir",
                  side_effect=Exception("no")),
        ):
            result = migrate_legacy.apply(action, ctx)
        assert result.state == ModuleState.SKIPPED

    def test_apply_skipped_message_no_legacy(self, tmp_path):
        from navig.installer.modules import migrate_legacy
        action, ctx = self._make(tmp_path)
        with (
            patch("navig.commands.init._migrate_legacy_windows_runtime_layout",
                  side_effect=Exception("skip")),
            patch("navig.commands.init._migrate_legacy_documents_dir",
                  side_effect=Exception("skip")),
        ):
            result = migrate_legacy.apply(action, ctx)
        assert "No legacy" in result.message or "skip" in result.message
