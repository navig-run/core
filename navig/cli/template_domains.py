"""CLI registrations for template-adjacent command groups.

Extracted from `navig.cli.__init__` to keep the root CLI module smaller while
preserving deprecated aliases and command behavior.
"""

from __future__ import annotations

import typer

from navig.deprecation import deprecation_warning


def register_template_domain_commands(app: typer.Typer) -> None:
    """Register deprecated addon and server-template command groups."""

    addon_app = typer.Typer(
        help="[DEPRECATED: Use 'navig flow template'] Addon commands"
    )
    app.add_typer(addon_app, name="addon", hidden=True)

    @addon_app.callback()
    def addon_callback(ctx: typer.Context):
        """Addon management - DEPRECATED, use 'navig flow template'."""
        deprecation_warning("navig addon", "navig flow template")

    @addon_app.command("list")
    def addon_list(ctx: typer.Context):
        """List available templates."""
        from navig.commands.template import addon_list_deprecated

        addon_list_deprecated(ctx.obj)

    @addon_app.command("enable")
    def addon_enable(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Template name to enable"),
    ):
        """Enable a template."""
        from navig.commands.template import addon_enable_deprecated

        addon_enable_deprecated(name, ctx.obj)

    @addon_app.command("disable")
    def addon_disable(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Template name to disable"),
    ):
        """Disable a template."""
        from navig.commands.template import addon_disable_deprecated

        addon_disable_deprecated(name, ctx.obj)

    @addon_app.command("info")
    def addon_info(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Template name to show"),
    ):
        """Show template info."""
        from navig.commands.template import addon_info_deprecated

        addon_info_deprecated(name, ctx.obj)

    @addon_app.command("run")
    def addon_run_with_args(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Template name to run"),
        command: str | None = typer.Argument(None, help="Template command to execute"),
        args: list[str] | None = typer.Argument(
            None, help="Arguments for the template command"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", "-n", help="Preview without changes"
        ),
    ):
        """Run a template command (deprecated; use flow template run)."""
        deprecation_warning("navig addon run", "navig flow template run")
        from navig.commands.template import deploy_template_cmd

        deploy_template_cmd(
            name,
            command_name=command,
            command_args=args or [],
            dry_run=dry_run,
            ctx_obj=ctx.obj,
        )

    @addon_app.command("run")
    def addon_run(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Template name to run/deploy"),
        dry_run: bool = typer.Option(
            False, "--dry-run", "-n", help="Preview without changes"
        ),
    ):
        """Run/deploy a template (deprecated)."""
        from navig.commands.template import addon_run_deprecated

        addon_run_deprecated(name, ctx.obj, dry_run=dry_run)

    server_template_app = typer.Typer(help="Manage per-server template configurations")
    app.add_typer(server_template_app, name="server-template")

    @server_template_app.command("list")
    def server_template_list(
        ctx: typer.Context,
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
        enabled_only: bool = typer.Option(
            False, "--enabled", "-e", help="Show only enabled templates"
        ),
        plain: bool = typer.Option(
            False,
            "--plain",
            help="Output plain text (one template per line) for scripting",
        ),
    ):
        """List template configurations for a server."""
        from navig.commands.server_template import list_server_templates_cmd

        ctx.obj["server"] = server
        ctx.obj["enabled_only"] = enabled_only
        ctx.obj["plain"] = plain
        list_server_templates_cmd(ctx.obj)

    @server_template_app.command("show")
    def server_template_show(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name to show"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
    ):
        """Show merged configuration for a server template."""
        from navig.commands.server_template import show_template_config_cmd

        ctx.obj["server"] = server
        show_template_config_cmd(template_name, ctx.obj)

    @server_template_app.command("enable")
    def server_template_enable(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name to enable"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
    ):
        """Enable an template for a specific server."""
        from navig.commands.server_template import enable_server_template_cmd

        ctx.obj["server"] = server
        enable_server_template_cmd(template_name, ctx.obj)

    @server_template_app.command("disable")
    def server_template_disable(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name to disable"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
    ):
        """Disable an template for a specific server."""
        from navig.commands.server_template import disable_server_template_cmd

        ctx.obj["server"] = server
        disable_server_template_cmd(template_name, ctx.obj)

    @server_template_app.command("set")
    def server_template_set(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name"),
        key_path: str = typer.Argument(
            ..., help="Dot-separated config path (e.g., 'paths.web_root')"
        ),
        value: str = typer.Argument(..., help="Value to set (JSON-parseable)"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
    ):
        """Set a custom value for a server template configuration."""
        from navig.commands.server_template import set_template_value_cmd

        ctx.obj["server"] = server
        set_template_value_cmd(template_name, key_path, value, ctx.obj)

    @server_template_app.command("sync")
    def server_template_sync(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name to sync"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
        force: bool = typer.Option(
            False, "--force", "-f", help="Overwrite all custom settings"
        ),
    ):
        """Sync template configuration from template."""
        from navig.commands.server_template import sync_template_cmd

        ctx.obj["server"] = server
        ctx.obj["force"] = force
        sync_template_cmd(template_name, ctx.obj)

    @server_template_app.command("init")
    def server_template_init(
        ctx: typer.Context,
        template_name: str = typer.Argument(..., help="Template name to initialize"),
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server name (uses active if omitted)"
        ),
        enable: bool = typer.Option(
            False, "--enable", "-e", help="Enable template after initialization"
        ),
    ):
        """Manually initialize an template for a server."""
        from navig.commands.server_template import init_template_cmd

        ctx.obj["server"] = server
        ctx.obj["enable"] = enable
        init_template_cmd(template_name, ctx.obj)
