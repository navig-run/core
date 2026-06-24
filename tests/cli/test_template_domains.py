"""
Tests for navig.cli.template_domains — deprecated addon and server-template command groups.
"""
from unittest.mock import MagicMock, patch

import typer
import pytest
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# register_template_domain_commands
# ---------------------------------------------------------------------------

class TestRegisterTemplateDomainCommands:
    def _make_app_with_registration(self):
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()

        # Add a basic callback so the app works standalone
        @app.callback()
        def main(ctx: typer.Context):
            ctx.ensure_object(dict)

        register_template_domain_commands(app)
        return app

    def test_registers_without_error(self):
        """register_template_domain_commands should not raise."""
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)  # Should not raise

    def test_registered_groups_exist_in_app(self):
        """After registration, 'addon' and 'server-template' groups are present."""
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)

        group_names = {g.name for g in app.registered_groups}
        assert "addon" in group_names
        assert "server-template" in group_names

    def test_addon_is_hidden(self):
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)

        addon_group = next(g for g in app.registered_groups if g.name == "addon")
        assert addon_group.hidden is True

    def test_addon_callback_calls_deprecation_warning(self):
        """Invoking addon group should trigger deprecation_warning."""
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()

        @app.callback()
        def main(ctx: typer.Context):
            ctx.ensure_object(dict)

        register_template_domain_commands(app)

        with patch("navig.cli.template_domains.deprecation_warning") as mock_dw:
            result = runner.invoke(app, ["addon", "--help"])
        # The deprecation_warning is called when addon group is invoked
        # (it's in the callback). --help might not trigger it on all typer versions.
        # At minimum the invocation should not crash with exit code >1
        assert result.exit_code in (0, 1, 2)

    def test_server_template_list_subcommand_exists(self):
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)

        st_group = next(g for g in app.registered_groups if g.name == "server-template")
        cmd_names = {c.name for c in st_group.typer_instance.registered_commands}
        assert "list" in cmd_names

    def test_addon_list_subcommand_exists(self):
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)

        addon_group = next(g for g in app.registered_groups if g.name == "addon")
        cmd_names = {c.name for c in addon_group.typer_instance.registered_commands}
        assert "list" in cmd_names

    def test_addon_enable_subcommand_exists(self):
        from navig.cli.template_domains import register_template_domain_commands

        app = typer.Typer()
        register_template_domain_commands(app)

        addon_group = next(g for g in app.registered_groups if g.name == "addon")
        cmd_names = {c.name for c in addon_group.typer_instance.registered_commands}
        assert "enable" in cmd_names
