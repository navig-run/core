"""Batch 59 — commands/explain, memory/paths, commands/whoami."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.commands.explain
# ---------------------------------------------------------------------------

class TestExplainApp:
    @pytest.fixture(autouse=True)
    def runner(self):
        from typer.testing import CliRunner
        self.runner = CliRunner()

    def _invoke(self, args):
        from navig.commands.explain import app
        return self.runner.invoke(app, args)

    def test_no_args_shows_help(self):
        result = self._invoke([])
        # no_args_is_help=True exits with 0 or non-zero but always shows usage
        assert "Usage" in result.output or "help" in result.output.lower() or "command" in result.output.lower()

    def test_command_subcommand_warns(self):
        with patch("navig.console_helper.warn", create=True) as mock_warn:
            result = self._invoke(["command", "vault"])
        assert result.exit_code == 0
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0][0]
        assert "vault" in args

    def test_command_subcommand_includes_not_implemented(self):
        with patch("navig.console_helper.warn", create=True) as mock_warn:
            self._invoke(["command", "db"])
        msg = mock_warn.call_args[0][0]
        assert "not yet implemented" in msg

    def test_config_subcommand_warns(self):
        with patch("navig.console_helper.warn", create=True) as mock_warn:
            result = self._invoke(["config", "llm.provider"])
        assert result.exit_code == 0
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0][0]
        assert "llm.provider" in args

    def test_concept_subcommand_warns(self):
        with patch("navig.console_helper.warn", create=True) as mock_warn:
            result = self._invoke(["concept", "tunnel"])
        assert result.exit_code == 0
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0][0]
        assert "tunnel" in args

    def test_command_requires_argument(self):
        result = self._invoke(["command"])
        assert result.exit_code != 0

    def test_config_requires_argument(self):
        result = self._invoke(["config"])
        assert result.exit_code != 0

    def test_concept_requires_argument(self):
        result = self._invoke(["concept"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# navig.memory.paths
# ---------------------------------------------------------------------------

class TestNavigHome:
    def test_default_uses_platform_config_dir(self, tmp_path):
        from navig.memory import paths as mp
        env_clean = {k: v for k, v in os.environ.items() if k != "NAVIG_HOME"}
        with (
            patch.dict(os.environ, env_clean, clear=True),
            patch.object(mp._platform_paths, "config_dir", return_value=tmp_path),
        ):
            result = mp.navig_home()
        assert result == tmp_path

    def test_navig_home_env_overrides(self, tmp_path):
        from navig.memory import paths as mp
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            result = mp.navig_home()
        assert result == tmp_path

    def test_returns_path_object(self, tmp_path):
        from pathlib import Path
        from navig.memory import paths as mp
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            result = mp.navig_home()
        assert isinstance(result, Path)


class TestMemoryDir:
    def test_returns_memory_subdir(self, tmp_path):
        from navig.memory import paths as mp
        with patch.object(mp, "navig_home", return_value=tmp_path):
            result = mp.memory_dir()
        assert result == tmp_path / "memory"

    def test_creates_directory_if_missing(self, tmp_path):
        from navig.memory import paths as mp
        target = tmp_path / "new_home"
        with patch.object(mp, "navig_home", return_value=target):
            result = mp.memory_dir()
        assert result.exists()
        assert result.is_dir()

    def test_ok_if_already_exists(self, tmp_path):
        from navig.memory import paths as mp
        (tmp_path / "memory").mkdir()
        with patch.object(mp, "navig_home", return_value=tmp_path):
            result = mp.memory_dir()
        assert result.exists()


class TestKeyFactsDbPath:
    def test_ends_with_key_facts_db(self):
        from navig.memory.paths import KEY_FACTS_DB_PATH
        assert KEY_FACTS_DB_PATH.name == "key_facts.db"

    def test_is_path_object(self):
        from pathlib import Path
        from navig.memory.paths import KEY_FACTS_DB_PATH
        assert isinstance(KEY_FACTS_DB_PATH, Path)


# ---------------------------------------------------------------------------
# navig.commands.whoami — run_whoami()
# ---------------------------------------------------------------------------

class TestRunWhoami:
    def test_no_entity_prints_onboard_hint(self, capsys):
        with (
            patch("navig.identity.sigil_store.load_entity", return_value=None, create=True),
            patch("navig.identity.entity.derive_entity", create=True),
            patch("navig.identity.renderer.render_sigil_card", create=True),
        ):
            from navig.commands.whoami import run_whoami
            import importlib
            import navig.commands.whoami as wm
            importlib.reload(wm)

            with patch("navig.commands.whoami.get_console") as mock_console:
                wm.run_whoami()

            mock_console.return_value.print.assert_called_once()
            msg = mock_console.return_value.print.call_args[0][0]
            assert "onboard" in msg.lower() or "No entity" in msg

    def test_with_entity_calls_render_sigil_card(self):
        fake_data = {"seed": "abc123"}
        fake_entity = MagicMock()

        with (
            patch("navig.identity.sigil_store.load_entity", return_value=fake_data, create=True),
            patch("navig.identity.entity.derive_entity", return_value=fake_entity, create=True) as mock_derive,
            patch("navig.identity.renderer.render_sigil_card", create=True) as mock_render,
        ):
            from navig.commands import whoami as wm_module
            import importlib
            importlib.reload(wm_module)

            wm_module.run_whoami()

            mock_derive.assert_called_once_with("abc123")
            mock_render.assert_called_once_with(fake_entity)

    def test_no_entity_returns_early(self):
        """run_whoami returns None (not an error) when no entity is stored."""
        with (
            patch("navig.identity.sigil_store.load_entity", return_value=None, create=True),
            patch("navig.identity.entity.derive_entity", create=True),
            patch("navig.identity.renderer.render_sigil_card", create=True),
        ):
            from navig.commands import whoami as wm_module
            import importlib
            importlib.reload(wm_module)

            with patch("navig.commands.whoami.get_console"):
                result = wm_module.run_whoami()

        assert result is None
