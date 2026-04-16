"""
Web server management commands for Apache and Nginx.

This module provides comprehensive web server management capabilities including
virtual host listing, configuration testing, site/module management, and safe
reloading/restarting operations.
"""

import json
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from navig import console_helper as ch
from navig.cli._callbacks import show_subcommand_help
from navig.config import get_config_manager
from navig.console_helper import get_console
from navig.remote import RemoteOperations

console = get_console()


def list_vhosts(options: dict[str, Any]) -> None:
    """
    List virtual hosts for Apache or Nginx.

    Shows both enabled and available sites, with visual indicators.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        from datetime import datetime

        result_data = {
            "timestamp": datetime.now().isoformat(),
            "action": "list_vhosts",
            "server": server_type,
            "enabled": [],
            "available": [],
        }

        # Normalize server type (apache2 → apache for paths)
        server_type_normalized = "apache" if server_type == "apache2" else server_type

        if server_type_normalized == "apache":
            enabled_path = "/etc/apache2/sites-enabled/"
            available_path = "/etc/apache2/sites-available/"
        else:
            enabled_path = "/etc/nginx/sites-enabled/"
            available_path = "/etc/nginx/sites-available/"

        # Get enabled sites
        enabled_cmd = f"ls -1 {enabled_path} 2>/dev/null || echo ''"
        if dry_run:
            console.print(f"[yellow][DRY RUN] Would list: {enabled_cmd}[/yellow]")
            result_data["enabled"] = ["example.com", "default"]
        else:
            enabled_result = remote_ops.execute_command(enabled_cmd, host_config)
            if enabled_result.returncode == 0 and enabled_result.stdout:
                enabled_sites = [
                    s.strip() for s in enabled_result.stdout.strip().split("\n") if s.strip()
                ]
                result_data["enabled"] = enabled_sites

        # Get available sites
        available_cmd = f"ls -1 {available_path} 2>/dev/null || echo ''"
        if dry_run:
            console.print(f"[yellow][DRY RUN] Would list: {available_cmd}[/yellow]")
            result_data["available"] = ["example.com", "default", "test.com"]
        else:
            available_result = remote_ops.execute_command(available_cmd, host_config)
            if available_result.returncode == 0 and available_result.stdout:
                available_sites = [
                    s.strip() for s in available_result.stdout.strip().split("\n") if s.strip()
                ]
                result_data["available"] = available_sites

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        # Display results in Rich table
        console.rule(f"[bold cyan]{server_type.upper()} Virtual Hosts[/bold cyan]")

        table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
        table.add_column("Status", style="cyan", width=10)
        table.add_column("Site Name", style="white")

        # Add enabled sites
        for site in result_data["enabled"]:
            table.add_row("✓ Enabled", f"[green]{site}[/green]")

        # Add disabled sites
        disabled = [s for s in result_data["available"] if s not in result_data["enabled"]]
        for site in disabled:
            table.add_row("- Disabled", f"[dim]{site}[/dim]")

        console.print(table)
        console.print(
            f"\n[cyan]Enabled: {len(result_data['enabled'])}[/cyan]  |  [dim]Available: {len(result_data['available'])}[/dim]"
        )

    except Exception as e:
        console.print(f"[red]✗ Error listing virtual hosts: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


web_app = typer.Typer(
    help="Web server management (Nginx/Apache vhosts, sites, modules)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@web_app.callback()
def web_callback(ctx: typer.Context):
    """Web server management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("web", ctx)
        raise typer.Exit()


@web_app.command("vhosts")
def web_vhosts_new(ctx: typer.Context):
    """List virtual hosts (enabled and available)."""
    list_vhosts(ctx.obj)


@web_app.command("test")
def web_test_new(ctx: typer.Context):
    """Test web server configuration syntax."""
    test_config(ctx.obj)


@web_app.command("enable")
def web_enable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to enable"),
):
    """Enable a web server site."""
    ctx.obj["site_name"] = site_name
    enable_site(ctx.obj)


@web_app.command("disable")
def web_disable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to disable"),
):
    """Disable a web server site."""
    ctx.obj["site_name"] = site_name
    disable_site(ctx.obj)


@web_app.command("module-enable")
def web_module_enable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to enable"),
):
    """Enable Apache module (Apache only)."""
    ctx.obj["module_name"] = module_name
    enable_module(ctx.obj)


@web_app.command("module-disable")
def web_module_disable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to disable"),
):
    """Disable Apache module (Apache only)."""
    ctx.obj["module_name"] = module_name
    disable_module(ctx.obj)


@web_app.command("reload")
def web_reload_new(ctx: typer.Context):
    """Safely reload web server (tests config first)."""
    reload_server(ctx.obj)


@web_app.command("recommend")
def web_recommend_new(ctx: typer.Context):
    """Display performance tuning recommendations."""
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
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
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


def test_config(options: dict[str, Any]) -> None:
    """
    Test web server configuration syntax.

    Validates configuration files before reload/restart to prevent downtime.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "test_config",
            "server": server_type,
            "valid": False,
            "output": "",
        }

        # Normalize server type (apache2 → apache for commands)
        server_type_normalized = "apache" if server_type == "apache2" else server_type

        if server_type_normalized == "apache":
            test_cmd = "apache2ctl configtest 2>&1"
            success_pattern = "Syntax OK"
        else:
            test_cmd = "nginx -t 2>&1"
            success_pattern = "test is successful"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would run: {test_cmd}[/yellow]")
            result_data["valid"] = True
            result_data["output"] = f"{server_type}: Syntax OK"
        else:
            test_result = remote_ops.execute_command(test_cmd, host_config)
            # Both stdout and stderr can contain output, combine them
            output = test_result.stdout + test_result.stderr
            result_data["output"] = output

            if success_pattern in output:
                result_data["valid"] = True

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        console.rule(f"[bold cyan]{server_type.upper()} Configuration Test[/bold cyan]")

        if result_data["valid"]:
            console.print(
                Panel(
                    f"[green]✓ Configuration is valid[/green]\n\n{result_data['output']}",
                    title=f"{server_type.upper()} Config Test",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    f"[red]✗ Configuration has errors[/red]\n\n{result_data['output']}",
                    title=f"{server_type.upper()} Config Test",
                    border_style="red",
                )
            )

    except Exception as e:
        console.print(f"[red]✗ Error testing configuration: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def enable_site(options: dict[str, Any]) -> None:
    """
    Enable a web server site.

    Creates symlink for site configuration (Nginx) or uses a2ensite (Apache).
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including site_name, dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)
    site_name = options.get("site_name")

    if not site_name:
        console.print("[red]✗ Site name is required[/red]")
        return

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "enable_site",
            "server": server_type,
            "site": site_name,
            "success": False,
        }

        # Normalize server type (apache2 → apache for commands)
        server_type_normalized = "apache" if server_type == "apache2" else server_type

        if server_type_normalized == "apache":
            enable_cmd = f"a2ensite {site_name}"
        else:
            enable_cmd = f"ln -sf /etc/nginx/sites-available/{site_name} /etc/nginx/sites-enabled/{site_name} 2>&1"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would run: {enable_cmd}[/yellow]")
            result_data["success"] = True
        else:
            enable_result = remote_ops.execute_command(enable_cmd, host_config)
            result_data["success"] = enable_result.returncode == 0

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        if result_data["success"]:
            console.print(f"[green]✓ Site '{site_name}' enabled successfully[/green]")
            console.print("[cyan]→ Run 'navig webserver-reload' to apply changes[/cyan]")
        else:
            console.print(f"[red]✗ Failed to enable site '{site_name}'[/red]")

    except Exception as e:
        console.print(f"[red]✗ Error enabling site: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def disable_site(options: dict[str, Any]) -> None:
    """
    Disable a web server site.

    Removes symlink (Nginx) or uses a2dissite (Apache).
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including site_name, dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)
    site_name = options.get("site_name")

    if not site_name:
        console.print("[red]✗ Site name is required[/red]")
        return

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "disable_site",
            "server": server_type,
            "site": site_name,
            "success": False,
        }

        # Normalize server type (apache2 → apache for commands)
        server_type_normalized = "apache" if server_type == "apache2" else server_type

        if server_type_normalized == "apache":
            disable_cmd = f"a2dissite {site_name}"
        else:
            disable_cmd = f"rm -f /etc/nginx/sites-enabled/{site_name} 2>&1"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would run: {disable_cmd}[/yellow]")
            result_data["success"] = True
        else:
            disable_result = remote_ops.execute_command(disable_cmd, host_config)
            result_data["success"] = disable_result.returncode == 0

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        if result_data["success"]:
            console.print(f"[green]✓ Site '{site_name}' disabled successfully[/green]")
            console.print("[cyan]→ Run 'navig webserver-reload' to apply changes[/cyan]")
        else:
            console.print(f"[red]✗ Failed to disable site '{site_name}'[/red]")

    except Exception as e:
        console.print(f"[red]✗ Error disabling site: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def enable_module(options: dict[str, Any]) -> None:
    """
    Enable a web server module (Apache only).

    Uses a2enmod for Apache module management.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including module_name, dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)
    module_name = options.get("module_name")

    if not module_name:
        console.print("[red]✗ Module name is required[/red]")
        return

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Verify this is Apache (module management is Apache-only)
    if server_type not in ["apache", "apache2"]:
        console.print(
            f"[red]✗ Module management is only available for Apache (current: {server_type})[/red]"
        )
        return

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "enable_module",
            "server": "apache",
            "module": module_name,
            "success": False,
        }

        enable_cmd = f"a2enmod {module_name}"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would run: {enable_cmd}[/yellow]")
            result_data["success"] = True
        else:
            enable_result = remote_ops.execute_command(enable_cmd, host_config)
            result_data["success"] = enable_result.returncode == 0

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        if result_data["success"]:
            console.print(f"[green]✓ Module '{module_name}' enabled successfully[/green]")
            console.print("[cyan]→ Run 'navig webserver-reload' to apply changes[/cyan]")
        else:
            console.print(f"[red]✗ Failed to enable module '{module_name}'[/red]")

    except Exception as e:
        console.print(f"[red]✗ Error enabling module: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def disable_module(options: dict[str, Any]) -> None:
    """
    Disable a web server module (Apache only).

    Uses a2dismod for Apache module management.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including module_name, dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)
    module_name = options.get("module_name")

    if not module_name:
        console.print("[red]✗ Module name is required[/red]")
        return

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Verify this is Apache (module management is Apache-only)
    if server_type not in ["apache", "apache2"]:
        console.print(
            f"[red]✗ Module management is only available for Apache (current: {server_type})[/red]"
        )
        return

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "disable_module",
            "server": "apache",
            "module": module_name,
            "success": False,
        }

        disable_cmd = f"a2dismod {module_name}"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would run: {disable_cmd}[/yellow]")
            result_data["success"] = True
        else:
            disable_result = remote_ops.execute_command(disable_cmd, host_config)
            result_data["success"] = disable_result.returncode == 0

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        if result_data["success"]:
            console.print(f"[green]✓ Module '{module_name}' disabled successfully[/green]")
            console.print("[cyan]→ Run 'navig webserver-reload' to apply changes[/cyan]")
        else:
            console.print(f"[red]✗ Failed to disable module '{module_name}'[/red]")

    except Exception as e:
        console.print(f"[red]✗ Error disabling module: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def reload_server(options: dict[str, Any]) -> None:
    """
    Safely reload web server configuration.

    Tests configuration first, then reloads if valid. Reload is preferred over
    restart as it doesn't drop existing connections.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including dry_run, json, host, app
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host")
    app_name = options.get("app")
    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Load host configuration for SSH connection
    host_config = config_manager.load_host_config(host_name)

    try:
        result_data = {
            "timestamp": "2024-01-01",
            "action": "reload_server",
            "server": server_type,
            "config_valid": False,
            "reload_success": False,
            "service_active": False,
        }

        # Test configuration first
        if not json_output:
            console.print("[cyan]→ Testing configuration before reload...[/cyan]")

        # Normalize server type (apache2 → apache for commands)
        server_type_normalized = "apache" if server_type == "apache2" else server_type

        if server_type_normalized == "apache":
            test_cmd = "apache2ctl configtest 2>&1"
            success_pattern = "Syntax OK"
            service_name = "apache2"
        else:
            test_cmd = "nginx -t 2>&1"
            success_pattern = "test is successful"
            service_name = "nginx"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would test: {test_cmd}[/yellow]")
            result_data["config_valid"] = True
        else:
            test_result = remote_ops.execute_command(test_cmd, host_config)
            output = test_result.stdout + test_result.stderr
            result_data["config_valid"] = success_pattern in output

        if not result_data["config_valid"] and not dry_run:
            console.print("[red]✗ Configuration test failed. Aborting reload.[/red]")
            if json_output:
                console.print(json.dumps(result_data, indent=2))
            return

        # Reload service
        reload_cmd = f"systemctl reload {service_name}"

        if dry_run:
            console.print(f"[yellow][DRY RUN] Would reload: {reload_cmd}[/yellow]")
            result_data["reload_success"] = True
            result_data["service_active"] = True
        else:
            reload_result = remote_ops.execute_command(reload_cmd, host_config)
            result_data["reload_success"] = reload_result.returncode == 0

            if reload_result.returncode == 0:
                # Verify service is active
                import time

                time.sleep(1)
                status_cmd = f"systemctl is-active {service_name}"
                status_result = remote_ops.execute_command(status_cmd, host_config)
                result_data["service_active"] = "active" in status_result.stdout

        if json_output:
            console.print(json.dumps(result_data, indent=2))
            return

        if result_data["reload_success"]:
            console.print(f"[green]✓ {server_type.upper()} reloaded successfully[/green]")
            if result_data["service_active"]:
                console.print("[green]✓ Service is active[/green]")
            else:
                console.print("[yellow]⚠ Service status could not be verified[/yellow]")
        else:
            console.print(f"[red]✗ Failed to reload {server_type.upper()}[/red]")

    except Exception as e:
        console.print(f"[red]✗ Error reloading server: {e}[/red]")
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))


def get_recommendations(options: dict[str, Any]) -> None:
    """
    Display performance tuning recommendations.

    Shows server-specific optimization tips for Apache or Nginx.
    Webserver type is auto-detected from app configuration.

    Args:
        options: Command options including json, host, app
    """
    config_manager = get_config_manager()

    host_name = options.get("host")
    app_name = options.get("app")
    json_output = options.get("json", False)

    # Get active host and app
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    if not app_name:
        from navig.cli.recovery import require_active_app  # noqa: PLC0415
        app_name = require_active_app(options, config_manager)

    # Load app configuration and extract webserver type
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except ValueError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]")
        return
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        return

    # Extract webserver type from app config (REQUIRED field)
    server_type = app_config["webserver"]["type"]  # nginx or apache2

    # Normalize server type (apache2 → apache for recommendations)
    server_type_normalized = "apache" if server_type == "apache2" else server_type

    recommendations = {
        "apache": [
            {
                "title": "Enable mod_deflate for compression",
                "description": "Compress text-based content to reduce bandwidth",
                "command": "a2enmod deflate",
            },
            {
                "title": "Enable mod_expires for browser caching",
                "description": "Set cache headers for static resources",
                "command": "a2enmod expires",
            },
            {
                "title": "Use mod_cache for server-side caching",
                "description": "Cache dynamic content on the server",
                "command": "a2enmod cache cache_disk",
            },
            {
                "title": "Optimize MaxRequestWorkers",
                "description": "Set based on available RAM (RAM_GB * 1024 / 25)",
                "config": "MaxRequestWorkers in mpm_prefork.conf",
            },
            {
                "title": "Enable HTTP/2 with mod_http2",
                "description": "Improve performance with HTTP/2 protocol",
                "command": "a2enmod http2",
            },
            {
                "title": "Enable mod_pagespeed",
                "description": "Automatic optimization of web pages",
                "command": "Install and configure mod_pagespeed",
            },
        ],
        "nginx": [
            {
                "title": "Enable gzip compression",
                "description": "Compress text-based content",
                "config": "gzip on; gzip_types text/plain text/css application/json;",
            },
            {
                "title": "Configure browser caching",
                "description": "Set expires headers for static files",
                "config": 'expires 1y; add_header Cache-Control "public, immutable";',
            },
            {
                "title": "Use fastcgi_cache for PHP",
                "description": "Cache PHP application responses",
                "config": "fastcgi_cache_path /var/cache/nginx levels=1:2 keys_zone=PHPCACHE:100m;",
            },
            {
                "title": "Optimize worker_processes",
                "description": "Set to number of CPU cores",
                "config": "worker_processes auto;",
            },
            {
                "title": "Tune worker_connections",
                "description": "Increase for high-traffic sites",
                "config": "worker_connections 4096;",
            },
            {
                "title": "Enable HTTP/2",
                "description": "Add http2 to listen directive",
                "config": "listen 443 ssl http2;",
            },
        ],
    }

    tips = recommendations.get(server_type_normalized, recommendations["nginx"])

    result_data = {"server": server_type, "recommendations": tips}

    if json_output:
        console.print(json.dumps(result_data, indent=2))
        return

    console.rule(
        f"[bold cyan]{server_type_normalized.upper()} Performance Recommendations[/bold cyan]"
    )

    for i, tip in enumerate(tips, 1):
        console.print(f"\n[bold cyan]{i}. {tip['title']}[/bold cyan]")
        console.print(f"   [dim]{tip['description']}[/dim]")

        if "command" in tip:
            console.print(f"   [yellow]Command:[/yellow] {tip['command']}")
        if "config" in tip:
            console.print(f"   [yellow]Config:[/yellow] {tip['config']}")

    console.print()
