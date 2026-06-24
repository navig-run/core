"""
Tests for navig/cli/assistant_hestia.py

Strategy: register commands on a fresh typer.Typer app and verify the
group names, hidden flag, and deprecation_warning calls.
"""

from unittest.mock import patch

import typer
import pytest
from typer.testing import CliRunner

from navig.cli.assistant_hestia import register_assistant_hestia_commands

runner = CliRunner()


def _make_app():
    """Return a fresh Typer app with registered groups."""
    app = typer.Typer()
    register_assistant_hestia_commands(app)
    return app


# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registers_without_error(self):
        app = typer.Typer()
        register_assistant_hestia_commands(app)  # must not raise

    def test_assistant_group_registered(self):
        app = _make_app()
        names = [g.name for g in app.registered_groups]
        assert "assistant" in names

    def test_hestia_group_registered(self):
        app = _make_app()
        names = [g.name for g in app.registered_groups]
        assert "hestia" in names

    def test_only_two_groups_added(self):
        app = typer.Typer()
        register_assistant_hestia_commands(app)
        assert len(app.registered_groups) == 2

    def test_assistant_group_is_hidden(self):
        app = _make_app()
        entry = next(g for g in app.registered_groups if g.name == "assistant")
        # hidden can live in TyperInfo.hidden or in the nested typer_instance.info.hidden
        hidden = bool(entry.hidden) or bool(entry.typer_instance.info.hidden)
        assert hidden is True

    def test_hestia_group_is_hidden(self):
        app = _make_app()
        entry = next(g for g in app.registered_groups if g.name == "hestia")
        hidden = bool(entry.hidden) or bool(entry.typer_instance.info.hidden)
        assert hidden is True


# ---------------------------------------------------------------------------
# Assistant callback triggers deprecation_warning
# ---------------------------------------------------------------------------


class TestAssistantCallback:
    def test_assistant_bare_call_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.interactive.launch_assistant_menu"):
                result = runner.invoke(app, ["assistant"])
        mock_warn.assert_called()
        call_args = mock_warn.call_args[0]
        assert "navig assistant" in call_args[0]

    def test_assistant_status_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.assistant.status_cmd"):
                runner.invoke(app, ["assistant", "status"])
        mock_warn.assert_called()

    def test_assistant_analyze_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.assistant.analyze_cmd"):
                runner.invoke(app, ["assistant", "analyze"])
        mock_warn.assert_called()

    def test_assistant_reset_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.assistant.reset_cmd"):
                runner.invoke(app, ["assistant", "reset"])
        mock_warn.assert_called()

    def test_assistant_config_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.assistant.config_cmd"):
                runner.invoke(app, ["assistant", "config"])
        mock_warn.assert_called()


# ---------------------------------------------------------------------------
# Hestia callback triggers deprecation_warning
# ---------------------------------------------------------------------------


class TestHestiaCallback:
    def test_hestia_bare_call_triggers_deprecation(self):
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.interactive.launch_hestia_menu"):
                runner.invoke(app, ["hestia"])
        mock_warn.assert_called()
        call_args = mock_warn.call_args[0]
        assert "navig hestia" in call_args[0]

    def test_assistant_deprecation_references_ai(self):
        """The deprecation message should point users to 'navig ai'."""
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.interactive.launch_assistant_menu"):
                runner.invoke(app, ["assistant"])
        call_args = mock_warn.call_args[0]
        assert "navig ai" in call_args[1]

    def test_hestia_deprecation_references_web_hestia(self):
        """The deprecation message should point users to 'navig web hestia'."""
        app = _make_app()
        with patch("navig.cli.assistant_hestia.deprecation_warning") as mock_warn:
            with patch("navig.commands.interactive.launch_hestia_menu"):
                runner.invoke(app, ["hestia"])
        call_args = mock_warn.call_args[0]
        assert "navig web hestia" in call_args[1]
