"""Tests for navig.cli.launcher — get_domain_commands and smart_launch."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from navig.cli.launcher import get_domain_commands, smart_launch
from navig.cli.selector import CommandEntry


# ── helpers ──────────────────────────────────────────────────


def _make_cmd(name: str, help_text: str = "", hidden: bool = False):
    """Build a fake typer CommandInfo-like object."""
    cmd = MagicMock()
    cmd.name = name
    cmd.hidden = hidden
    cmd.help = help_text

    cb = MagicMock()
    cb.__name__ = name.replace("-", "_")
    cb.__doc__ = None
    cmd.callback = cb
    return cmd


def _make_app(cmds: list, groups: list | None = None):
    """Build a fake typer.Typer-like object."""
    app = MagicMock()
    app.registered_commands = cmds
    app.registered_groups = groups or []
    return app


# ── get_domain_commands ───────────────────────────────────────


class TestGetDomainCommands:
    def test_returns_empty_for_empty_app(self):
        app = _make_app([])
        assert get_domain_commands("host", app) == []

    def test_single_command(self):
        cmd = _make_cmd("list", "List resources")
        app = _make_app([cmd])
        entries = get_domain_commands("host", app)
        assert len(entries) == 1
        assert entries[0].name == "list"
        assert entries[0].description == "List resources"
        assert entries[0].domain == "host"

    def test_hidden_commands_excluded(self):
        visible = _make_cmd("show", "Show")
        hidden = _make_cmd("internal", hidden=True)
        app = _make_app([visible, hidden])
        entries = get_domain_commands("host", app)
        assert len(entries) == 1
        assert entries[0].name == "show"

    def test_missing_callback_skipped(self):
        cmd = _make_cmd("orphan")
        cmd.callback = None
        app = _make_app([cmd])
        entries = get_domain_commands("host", app)
        assert entries == []

    def test_name_derived_from_callback_when_none(self):
        cmd = _make_cmd("", "No name")
        cmd.name = None
        cmd.callback.__name__ = "my_action"
        app = _make_app([cmd])
        entries = get_domain_commands("host", app)
        assert entries[0].name == "my-action"

    def test_help_text_from_docstring_when_no_stored_help(self):
        cmd = _make_cmd("do-it")
        cmd.help = None
        cmd.callback.__doc__ = "First line.\nSecond line."
        app = _make_app([cmd])
        entries = get_domain_commands("host", app)
        assert entries[0].description == "First line."

    def test_stored_help_takes_priority_over_docstring(self):
        cmd = _make_cmd("do-it", help_text="Stored help")
        cmd.callback.__doc__ = "Docstring"
        app = _make_app([cmd])
        entries = get_domain_commands("host", app)
        assert entries[0].description == "Stored help"

    def test_sorted_alphabetically(self):
        cmds = [_make_cmd("zoo"), _make_cmd("add"), _make_cmd("middle")]
        app = _make_app(cmds)
        entries = get_domain_commands("host", app)
        names = [e.name for e in entries]
        assert names == sorted(names)

    def test_subgroups_included(self):
        group = MagicMock()
        group.name = "monitor"
        typer_instance = MagicMock()
        typer_instance.info.help = "Monitor commands"
        group.typer_instance = typer_instance
        app = _make_app([], groups=[group])
        entries = get_domain_commands("host", app)
        assert len(entries) == 1
        assert entries[0].name == "monitor"
        assert entries[0].description == "Monitor commands"

    def test_subgroup_without_name_skipped(self):
        group = MagicMock()
        group.name = None
        app = _make_app([], groups=[group])
        entries = get_domain_commands("host", app)
        assert entries == []

    def test_mixed_commands_and_groups(self):
        cmd = _make_cmd("list")
        group = MagicMock()
        group.name = "advanced"
        group.typer_instance = None
        app = _make_app([cmd], groups=[group])
        entries = get_domain_commands("host", app)
        names = [e.name for e in entries]
        assert "list" in names
        assert "advanced" in names

    def test_domain_propagated_to_all_entries(self):
        cmds = [_make_cmd("a"), _make_cmd("b")]
        app = _make_app(cmds)
        entries = get_domain_commands("mydom", app)
        assert all(e.domain == "mydom" for e in entries)


# ── smart_launch ─────────────────────────────────────────────


class TestSmartLaunch:
    def test_legacy_env_returns_immediately(self):
        app = _make_app([_make_cmd("list")])
        with patch.dict(os.environ, {"NAVIG_LAUNCHER": "legacy"}):
            # Must return without any SystemExit or side effects
            smart_launch("host", app)

    def test_non_tty_exits_0(self, capsys):
        app = _make_app([_make_cmd("list")])
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=False):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with pytest.raises(SystemExit) as exc_info:
                        smart_launch("host", app)
        assert exc_info.value.code == 0

    def test_no_commands_exits_1(self, capsys):
        app = _make_app([])
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with pytest.raises(SystemExit) as exc_info:
                        smart_launch("host", app)
        assert exc_info.value.code == 1

    def test_user_cancel_exits_0(self):
        app = _make_app([_make_cmd("list")])
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with patch("navig.cli.launcher.fzf_or_fallback", return_value=None):
                        with pytest.raises(SystemExit) as exc_info:
                            smart_launch("host", app)
        assert exc_info.value.code == 0

    def test_user_selects_command_runs_subprocess(self):
        entry = CommandEntry(name="list", description="", domain="host")
        app = _make_app([_make_cmd("list")])
        mock_proc = MagicMock(returncode=0)
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with patch("navig.cli.launcher.fzf_or_fallback", return_value=entry):
                        with patch("navig.cli.launcher.subprocess.run", return_value=mock_proc) as mock_run:
                            with pytest.raises(SystemExit) as exc_info:
                                smart_launch("host", app)
        assert exc_info.value.code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "host" in call_args
        assert "list" in call_args

    def test_subprocess_exit_code_propagated(self):
        entry = CommandEntry(name="list", description="", domain="host")
        app = _make_app([_make_cmd("list")])
        mock_proc = MagicMock(returncode=42)
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with patch("navig.cli.launcher.fzf_or_fallback", return_value=entry):
                        with patch("navig.cli.launcher.subprocess.run", return_value=mock_proc):
                            with pytest.raises(SystemExit) as exc_info:
                                smart_launch("host", app)
        assert exc_info.value.code == 42

    def test_keyboard_interrupt_exits_0(self):
        app = _make_app([_make_cmd("list")])
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(sys.stdout, "isatty", return_value=True):
                    with patch("navig.cli.launcher.fzf_or_fallback", side_effect=KeyboardInterrupt):
                        with pytest.raises(SystemExit) as exc_info:
                            smart_launch("host", app)
        assert exc_info.value.code == 0
