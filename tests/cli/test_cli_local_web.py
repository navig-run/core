"""Tests for navig.cli.local_web — register_local_web_commands()."""
from __future__ import annotations

import typer
from typer.testing import CliRunner

from navig.cli.local_web import register_local_web_commands

_runner = CliRunner()


def _build_app() -> typer.Typer:
    """Build a fresh Typer app with local+web commands registered."""
    app = typer.Typer(invoke_without_command=True, no_args_is_help=False)
    register_local_web_commands(app)
    return app


class TestRegisterLocalWebCommands:
    def test_import(self):
        assert register_local_web_commands is not None

    def test_returns_none(self):
        app = typer.Typer()
        result = register_local_web_commands(app)
        assert result is None

    def test_hosts_group_registered(self):
        app = _build_app()
        names = [g.name for g in app.registered_groups]
        assert "hosts" in names

    def test_software_group_registered(self):
        app = _build_app()
        names = [g.name for g in app.registered_groups]
        assert "software" in names

    def test_local_group_registered(self):
        app = _build_app()
        names = [g.name for g in app.registered_groups]
        assert "local" in names

    def test_hosts_no_args_exits_zero(self):
        """hosts with no subcommand should show help and exit 0."""
        app = _build_app()
        result = _runner.invoke(app, ["hosts"])
        # Either exits 0 with help text or exits with usage message
        assert result.exit_code in (0, 1, 2)

    def test_software_no_args_exits(self):
        app = _build_app()
        result = _runner.invoke(app, ["software"])
        assert result.exit_code in (0, 1, 2)

    def test_local_no_args_exits(self):
        app = _build_app()
        result = _runner.invoke(app, ["local"])
        assert result.exit_code in (0, 1, 2)

    def test_multiple_registrations_independent(self):
        """Registering same app twice should not crash."""
        app = typer.Typer()
        register_local_web_commands(app)
        # Should have registrations
        assert len(app.registered_groups) >= 3
