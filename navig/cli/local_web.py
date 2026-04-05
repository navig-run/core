"""CLI registrations for local machine and web management commands.

Extracted from `navig.cli.__init__` to keep the root CLI module focused on
bootstrap and routing while preserving command names and legacy aliases.
"""

from __future__ import annotations

import typer

from navig.cli._callbacks import show_subcommand_help
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")


def register_local_web_commands(app: typer.Typer) -> None:
    """Register local machine and web management command groups."""

    hosts_app = typer.Typer(
        help="System hosts file management (view, edit, add entries)",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(hosts_app, name="hosts")

    @hosts_app.callback()
    def hosts_callback(ctx: typer.Context):
        """Hosts file operations - run without subcommand for help."""
        if ctx.invoked_subcommand is None:
            show_subcommand_help("hosts", ctx)
            raise typer.Exit()

    @hosts_app.command("view")
    def hosts_view_cmd(ctx: typer.Context):
        """View the system hosts file with syntax highlighting."""
        from navig.commands.local import hosts_view

        hosts_view(ctx.obj)

    @hosts_app.command("edit")
    def hosts_edit_cmd(ctx: typer.Context):
        """Open hosts file in editor (requires admin)."""
        from navig.commands.local import hosts_edit

        hosts_edit(ctx.obj)

    @hosts_app.command("add")
    def hosts_add_cmd(
        ctx: typer.Context,
        ip: str = typer.Argument(..., help="IP address"),
        hostname: str = typer.Argument(..., help="Hostname to add"),
    ):
        """Add an entry to the hosts file (requires admin)."""
        from navig.commands.local import hosts_add

        hosts_add(ip, hostname, ctx.obj)

    software_app = typer.Typer(
        help="Local software package management (list, search)",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(software_app, name="software")

    @software_app.callback()
    def software_callback(ctx: typer.Context):
        """Software management - run without subcommand to list packages."""
        if ctx.invoked_subcommand is None:
            from navig.commands.local import software_list

            software_list(ctx.obj)
            raise typer.Exit()

    @software_app.command("list")
    def software_list_cmd(
        ctx: typer.Context,
        limit: int | None = typer.Option(None, "--limit", "-l", help="Limit number of results"),
    ):
        """List installed software packages."""
        from navig.commands.local import software_list

        ctx.obj["limit"] = limit
        software_list(ctx.obj)

    @software_app.command("search")
    def software_search_cmd(
        ctx: typer.Context,
        query: str = typer.Argument(..., help="Search term"),
    ):
        """Search installed packages by name."""
        from navig.commands.local import software_search

        software_search(query, ctx.obj)

    local_app = typer.Typer(
        help="Local machine management (system info, security, network)",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(local_app, name="local")

    @local_app.callback()
    def local_callback(ctx: typer.Context):
        """Local system management - run without subcommand for help."""
        if ctx.invoked_subcommand is None:
            show_subcommand_help("local", ctx)
            raise typer.Exit()

    @local_app.command("show")
    def local_show_cmd(
        ctx: typer.Context,
        info: bool = typer.Option(True, "--info", "-i", help="Show system information"),
        resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
    ):
        """Show local system information."""
        if resources:
            from navig.commands.local import resource_usage

            resource_usage(ctx.obj)
        else:
            from navig.commands.local import system_info

            system_info(ctx.obj)

    @local_app.command("audit")
    def local_audit_cmd(
        ctx: typer.Context,
        ai: bool = typer.Option(False, "--ai", "-a", help="Include AI analysis"),
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed information"),
    ):
        """Run local security audit."""
        from navig.commands.local import security_audit

        ctx.obj["ai"] = ai
        ctx.obj["verbose"] = verbose
        security_audit(ctx.obj)

    @local_app.command("ports")
    def local_ports_cmd(ctx: typer.Context):
        """Show open/listening ports on local machine."""
        from navig.commands.local import security_ports

        security_ports(ctx.obj)

    @local_app.command("firewall")
    def local_firewall_cmd(ctx: typer.Context):
        """Show local firewall status."""
        from navig.commands.local import security_firewall

        security_firewall(ctx.obj)

    @local_app.command("ping")
    def local_ping_cmd(
        ctx: typer.Context,
        host: str = typer.Argument(..., help="Host to ping"),
        count: int = typer.Option(4, "--count", "-c", help="Number of pings"),
    ):
        """Ping a host from local machine."""
        from navig.commands.local import network_ping

        network_ping(host, count, ctx.obj)

    @local_app.command("dns")
    def local_dns_cmd(
        ctx: typer.Context,
        hostname: str = typer.Argument(..., help="Hostname to lookup"),
    ):
        """Perform DNS lookup."""
        from navig.commands.local import network_dns

        network_dns(hostname, ctx.obj)

    @local_app.command("interfaces")
    def local_interfaces_cmd(ctx: typer.Context):
        """Show network interfaces."""
        from navig.commands.local import network_interfaces

        network_interfaces(ctx.obj)

    web_app = typer.Typer(
        help="Web server management (Nginx/Apache vhosts, sites, modules)",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(web_app, name="web")

    @web_app.callback()
    def web_callback(ctx: typer.Context):
        """Web server management - run without subcommand for help."""
        if ctx.invoked_subcommand is None:
            show_subcommand_help("web", ctx)
            raise typer.Exit()

    @web_app.command("vhosts")
    def web_vhosts_new(ctx: typer.Context):
        """List virtual hosts (enabled and available)."""
        from navig.commands.webserver import list_vhosts

        list_vhosts(ctx.obj)

    @web_app.command("test")
    def web_test_new(ctx: typer.Context):
        """Test web server configuration syntax."""
        from navig.commands.webserver import test_config

        test_config(ctx.obj)

    @web_app.command("enable")
    def web_enable_new(
        ctx: typer.Context,
        site_name: str = typer.Argument(..., help="Site name to enable"),
    ):
        """Enable a web server site."""
        from navig.commands.webserver import enable_site

        ctx.obj["site_name"] = site_name
        enable_site(ctx.obj)

    @web_app.command("disable")
    def web_disable_new(
        ctx: typer.Context,
        site_name: str = typer.Argument(..., help="Site name to disable"),
    ):
        """Disable a web server site."""
        from navig.commands.webserver import disable_site

        ctx.obj["site_name"] = site_name
        disable_site(ctx.obj)

    @web_app.command("module-enable")
    def web_module_enable_new(
        ctx: typer.Context,
        module_name: str = typer.Argument(..., help="Module name to enable"),
    ):
        """Enable Apache module (Apache only)."""
        from navig.commands.webserver import enable_module

        ctx.obj["module_name"] = module_name
        enable_module(ctx.obj)

    @web_app.command("module-disable")
    def web_module_disable_new(
        ctx: typer.Context,
        module_name: str = typer.Argument(..., help="Module name to disable"),
    ):
        """Disable Apache module (Apache only)."""
        from navig.commands.webserver import disable_module

        ctx.obj["module_name"] = module_name
        disable_module(ctx.obj)

    @web_app.command("reload")
    def web_reload_new(ctx: typer.Context):
        """Safely reload web server (tests config first)."""
        from navig.commands.webserver import reload_server

        reload_server(ctx.obj)

    @web_app.command("recommend")
    def web_recommend_new(ctx: typer.Context):
        """Display performance tuning recommendations."""
        from navig.commands.webserver import get_recommendations

        get_recommendations(ctx.obj)

    web_hestia_app = typer.Typer(
        help="HestiaCP control panel management",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    web_app.add_typer(web_hestia_app, name="hestia")

    @web_hestia_app.callback()
    def web_hestia_callback(ctx: typer.Context):
        """HestiaCP management - run without subcommand for interactive menu."""
        if ctx.invoked_subcommand is None:
            from navig.commands.interactive import launch_hestia_menu

            launch_hestia_menu()
            raise typer.Exit()

    @web_hestia_app.command("list")
    def web_hestia_list(
        ctx: typer.Context,
        users: bool = typer.Option(False, "--users", "-u", help="List HestiaCP users"),
        domains: bool = typer.Option(False, "--domains", "-d", help="List HestiaCP domains"),
        user_filter: str | None = typer.Option(None, "--user", help="Filter domains by username"),
        plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    ):
        """List HestiaCP resources (users, domains)."""
        ctx.obj["plain"] = plain
        if users:
            from navig.commands.hestia import list_domains_cmd

            list_domains_cmd(user_filter, ctx.obj)
        else:
            from navig.commands.hestia import list_users_cmd

            list_users_cmd(ctx.obj)

    @web_hestia_app.command("add")
    def web_hestia_add(
        ctx: typer.Context,
        resource: str = typer.Argument(..., help="Resource type: user or domain"),
        name: str = typer.Argument(..., help="Username or domain name"),
        password: str | None = typer.Option(None, "--password", "-p", help="Password (for user)"),
        email: str | None = typer.Option(None, "--email", "-e", help="Email (for user)"),
        user: str | None = typer.Option(None, "--user", "-u", help="Username (for domain)"),
    ):
        """Add HestiaCP user or domain."""
        if resource == "user":
            if not password or not email:
                ch.error("Password and email required for user creation")
                raise typer.Exit(1)
            from navig.commands.hestia import add_user_cmd

            add_user_cmd(name, password, email, ctx.obj)
        elif resource == "domain":
            if not user:
                ch.error("Username required for domain creation (--user)")
                raise typer.Exit(1)
            from navig.commands.hestia import add_domain_cmd

            add_domain_cmd(user, name, ctx.obj)
        else:
            ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
            raise typer.Exit(1)

    @web_hestia_app.command("remove")
    def web_hestia_remove(
        ctx: typer.Context,
        resource: str = typer.Argument(..., help="Resource type: user or domain"),
        name: str = typer.Argument(..., help="Username or domain name"),
        user: str | None = typer.Option(None, "--user", "-u", help="Username (for domain)"),
        force: bool = typer.Option(
            False, "--force", "-f", help="Force deletion without confirmation"
        ),
    ):
        """Remove HestiaCP user or domain."""
        ctx.obj["force"] = force
        if resource == "user":
            from navig.commands.hestia import delete_user_cmd

            delete_user_cmd(name, ctx.obj)
        elif resource == "domain":
            if not user:
                ch.error("Username required for domain deletion (--user)")
                raise typer.Exit(1)
            from navig.commands.hestia import delete_domain_cmd

            delete_domain_cmd(user, name, ctx.obj)
        else:
            ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
            raise typer.Exit(1)

    @app.command("webserver-list-vhosts", hidden=True)
    def webserver_list_vhosts_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig web vhosts']"""
        ch.warning("'navig webserver-list-vhosts' is deprecated. Use 'navig web vhosts' instead.")
        from navig.commands.webserver import list_vhosts

        list_vhosts(ctx.obj)

    @app.command("webserver-test-config", hidden=True)
    def webserver_test_config_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig web test']"""
        ch.warning("'navig webserver-test-config' is deprecated. Use 'navig web test' instead.")
        from navig.commands.webserver import test_config

        test_config(ctx.obj)

    @app.command("webserver-reload", hidden=True)
    def webserver_reload_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig web reload']"""
        ch.warning("'navig webserver-reload' is deprecated. Use 'navig web reload' instead.")
        from navig.commands.webserver import reload_server

        reload_server(ctx.obj)
