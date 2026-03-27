"""CLI registrations for deprecated assistant and Hestia command groups.

Extracted from `navig.cli.__init__` to preserve legacy command surfaces while
reducing root module size.
"""

from __future__ import annotations

import typer

from navig.deprecation import deprecation_warning


def register_assistant_hestia_commands(app: typer.Typer) -> None:
    """Register deprecated `assistant` and `hestia` command groups."""

    assistant_app = typer.Typer(
        help="[DEPRECATED: Use 'navig ai'] Proactive AI assistant",
        invoke_without_command=True,
        no_args_is_help=False,
        hidden=True,
    )
    app.add_typer(assistant_app, name="assistant")

    @assistant_app.callback()
    def assistant_callback(ctx: typer.Context):
        """[DEPRECATED: Use 'navig ai'] AI Assistant."""
        deprecation_warning("navig assistant", "navig ai")
        if ctx.invoked_subcommand is None:
            from navig.commands.interactive import launch_assistant_menu

            launch_assistant_menu()
            raise typer.Exit()

    @assistant_app.command("status")
    def assistant_status(ctx: typer.Context):
        """[DEPRECATED: Use 'navig ai show --status']"""
        deprecation_warning("navig assistant status", "navig ai show --status")
        from navig.commands.assistant import status_cmd

        status_cmd(ctx.obj)

    @assistant_app.command("analyze")
    def assistant_analyze(ctx: typer.Context):
        """[DEPRECATED: Use 'navig ai diagnose']"""
        deprecation_warning("navig assistant analyze", "navig ai diagnose")
        from navig.commands.assistant import analyze_cmd

        analyze_cmd(ctx.obj)

    @assistant_app.command("context")
    def assistant_context(
        ctx: typer.Context,
        clipboard: bool = typer.Option(False, "--clipboard", help="Copy context to clipboard"),
        file: str | None = typer.Option(None, "--file", help="Save context to file"),
    ):
        """[DEPRECATED: Use 'navig ai show --context']"""
        deprecation_warning("navig assistant context", "navig ai show --context")
        from navig.commands.assistant import context_cmd

        context_cmd(ctx.obj, clipboard, file)

    @assistant_app.command("reset")
    def assistant_reset(ctx: typer.Context):
        """[DEPRECATED: Use 'navig ai run --reset']"""
        deprecation_warning("navig assistant reset", "navig ai run --reset")
        from navig.commands.assistant import reset_cmd

        reset_cmd(ctx.obj)

    @assistant_app.command("config")
    def assistant_config(ctx: typer.Context):
        """[DEPRECATED: Use 'navig ai edit']"""
        deprecation_warning("navig assistant config", "navig ai edit")
        from navig.commands.assistant import config_cmd

        config_cmd(ctx.obj)

    hestia_app = typer.Typer(
        help="[DEPRECATED: Use 'navig web hestia'] HestiaCP management",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(hestia_app, name="hestia", hidden=True)

    @hestia_app.callback()
    def hestia_callback(ctx: typer.Context):
        """HestiaCP management - DEPRECATED, use 'navig web hestia'."""
        deprecation_warning("navig hestia", "navig web hestia")
        if ctx.invoked_subcommand is None:
            from navig.commands.interactive import launch_hestia_menu

            launch_hestia_menu()
            raise typer.Exit()

    @hestia_app.command("users")
    def hestia_list_users(
        ctx: typer.Context,
        plain: bool = typer.Option(
            False, "--plain", help="Output plain text (one user per line) for scripting"
        ),
    ):
        """List HestiaCP users."""
        from navig.commands.hestia import list_users_cmd

        ctx.obj["plain"] = plain
        list_users_cmd(ctx.obj)

    @hestia_app.command("domains")
    def hestia_list_domains(
        ctx: typer.Context,
        user: str | None = typer.Option(None, "--user", "-u", help="Filter by username"),
        plain: bool = typer.Option(
            False,
            "--plain",
            help="Output plain text (one domain per line) for scripting",
        ),
    ):
        """List HestiaCP domains."""
        from navig.commands.hestia import list_domains_cmd

        ctx.obj["plain"] = plain
        list_domains_cmd(user, ctx.obj)

    @hestia_app.command("add-user")
    def hestia_add_user(
        ctx: typer.Context,
        username: str = typer.Argument(..., help="Username to create"),
        password: str = typer.Argument(..., help="User password"),
        email: str = typer.Argument(..., help="User email address"),
    ):
        """Add new HestiaCP user."""
        from navig.commands.hestia import add_user_cmd

        add_user_cmd(username, password, email, ctx.obj)

    @hestia_app.command("delete-user")
    def hestia_delete_user(
        ctx: typer.Context,
        username: str = typer.Argument(..., help="Username to delete"),
        force: bool = typer.Option(
            False, "--force", "-f", help="Force deletion without confirmation"
        ),
    ):
        """Delete HestiaCP user."""
        ctx.obj["force"] = force
        from navig.commands.hestia import delete_user_cmd

        delete_user_cmd(username, ctx.obj)

    @hestia_app.command("add-domain")
    def hestia_add_domain(
        ctx: typer.Context,
        user: str = typer.Argument(..., help="Username"),
        domain: str = typer.Argument(..., help="Domain name to add"),
    ):
        """Add domain to HestiaCP user."""
        from navig.commands.hestia import add_domain_cmd

        add_domain_cmd(user, domain, ctx.obj)

    @hestia_app.command("delete-domain")
    def hestia_delete_domain(
        ctx: typer.Context,
        user: str = typer.Argument(..., help="Username"),
        domain: str = typer.Argument(..., help="Domain name to delete"),
        force: bool = typer.Option(
            False, "--force", "-f", help="Force deletion without confirmation"
        ),
    ):
        """Delete domain from HestiaCP."""
        ctx.obj["force"] = force
        from navig.commands.hestia import delete_domain_cmd

        delete_domain_cmd(user, domain, ctx.obj)

    @hestia_app.command("renew-ssl")
    def hestia_renew_ssl(
        ctx: typer.Context,
        user: str = typer.Argument(..., help="Username"),
        domain: str = typer.Argument(..., help="Domain name"),
    ):
        """Renew SSL certificate for domain."""
        from navig.commands.hestia import renew_ssl_cmd

        renew_ssl_cmd(user, domain, ctx.obj)

    @hestia_app.command("rebuild-web")
    def hestia_rebuild_web(
        ctx: typer.Context,
        user: str = typer.Argument(..., help="Username"),
    ):
        """Rebuild web configuration for user."""
        from navig.commands.hestia import rebuild_web_cmd

        rebuild_web_cmd(user, ctx.obj)

    @hestia_app.command("backup-user")
    def hestia_backup_user(
        ctx: typer.Context,
        user: str = typer.Argument(..., help="Username to backup"),
    ):
        """Backup HestiaCP user."""
        from navig.commands.hestia import backup_user_cmd

        backup_user_cmd(user, ctx.obj)
