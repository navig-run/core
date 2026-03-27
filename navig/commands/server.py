"""
Server Management Commands

The Schema tracks all assets. Every server. Every operation.
"""

from typing import Any

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.discovery import ServerDiscovery

config_manager = get_config_manager()


def list_servers(options: dict[str, Any]):
    """List all configured servers."""
    servers = config_manager.list_servers()
    active_server = config_manager.get_active_server()
    default_server = config_manager.global_config.get("default_server")

    if not servers:
        ch.warning("No servers configured", "Use 'navig server add <name>' to add one.")
        return

    if options.get("raw"):
        for server in servers:
            ch.raw_print(server)
        return

    # Create rich table
    table = ch.create_table(
        title="Configured Servers",
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Host", "style": "green"},
            {"name": "User", "style": "blue"},
            {"name": "Status", "style": "yellow"},
        ],
    )

    for server in servers:
        try:
            config = config_manager.load_server_config(server)
            status_icons = []
            if server == active_server:
                status_icons.append("✓ Active")
            if server == default_server:
                status_icons.append("★ Default")

            table.add_row(
                server,
                config.get("host", "N/A"),
                config.get("user", "N/A"),
                " | ".join(status_icons) if status_icons else "",
            )
        except Exception as e:
            table.add_row(server, "ERROR", "", ch.status_text(str(e), False))

    ch.print_table(table)


def use_server(name: str, options: dict[str, Any]):
    """Switch active server context."""
    if not config_manager.server_exists(name):
        ch.error(
            f"Server '{name}' not found",
            "Use 'navig server list' to see available servers.",
        )
        return

    config_manager.set_active_server(name)

    if not options.get("quiet"):
        ch.success(f"Switched to server: {name}")


def show_current_server(options: dict[str, Any]):
    """Show currently active server."""
    active = config_manager.get_active_server()

    if options.get("raw"):
        ch.raw_print(active if active else "")
        return

    if not active:
        ch.warning("No active server", "Use 'navig server use <name>' to activate one.")
        return

    try:
        config = config_manager.load_server_config(active)
        ch.print_server_info(active, config)
    except Exception as e:
        ch.error("Error loading server config", str(e))


def set_default_server(name: str, options: dict[str, Any]):
    """Set default server."""
    if not config_manager.server_exists(name):
        ch.error(f"Server '{name}' not found")
        return

    config_manager.update_global_config({"default_server": name})

    if not options.get("quiet"):
        ch.success(f"Default server set to: {name}")


def add_server(name: str, options: dict[str, Any]):
    """Add new server configuration (interactive wizard with auto-discovery)."""
    if config_manager.server_exists(name):
        ch.error(f"Server '{name}' already exists.")
        return

    ch.info(f"Adding new server: {name}")
    ch.dim("Press Ctrl+C to cancel at any time.\n")

    # Step 1: SSH Authentication
    ch.header("SSH Authentication")
    host = ch.prompt_input("SSH Host")
    port = int(ch.prompt_input("SSH Port", default="22"))
    user = ch.prompt_input("SSH User", default="root")

    auth_method = ch.prompt_choice(
        "Authentication method", ["key", "password"], default="key"
    )

    ssh_key = None
    ssh_password = None

    if auth_method == "key":
        ssh_key = ch.prompt_input("SSH Key Path", default="~/.ssh/id_rsa")
    else:
        ssh_password = ch.prompt_input("SSH Password", password=True)

    # Step 2: Test connection and run auto-discovery
    ch.header("Server Auto-Discovery")

    ssh_config = {
        "host": host,
        "port": port,
        "user": user,
        "ssh_key": ssh_key,
        "ssh_password": ssh_password,
    }

    # Get debug logger from options if available
    debug_logger = options.get("debug_logger") if options else None
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)

    # Test connection
    ch.step("Testing SSH connection...")
    if not discovery.test_connection():
        ch.error("✗ Connection failed")
        ch.error("Could not connect to server. Please check your credentials.")
        ch.info("")
        ch.info("Troubleshooting steps:")
        ch.info("  1. Verify SSH key permissions: chmod 600 ~/.ssh/id_rsa")
        ch.info("  2. Test manual connection: ssh user@host")
        ch.info("  3. Check firewall allows port 22 (or custom SSH port)")
        ch.info("  4. Verify host key in ~/.ssh/known_hosts")
        ch.info("  5. Check server SSH logs: tail -f /var/log/auth.log")

        if not ch.confirm_action("Continue without auto-discovery?", default=False):
            ch.warning("Server setup cancelled.")
            return
        discovered = {}
    else:
        ch.success("✓ Connection successful\n")

        # Run auto-discovery
        if ch.confirm_action(
            "Run auto-discovery to detect server configuration?", default=True
        ):
            discovered = discovery.discover_all(progress=True)
        else:
            discovered = {}

    # Step 3: Database configuration (with smart defaults from discovery)
    ch.header("Database Configuration")

    # Get discovered database info
    discovered_dbs = discovered.get("databases", [])
    if discovered_dbs:
        db_default = discovered_dbs[0]
        ch.success(
            f"Detected {db_default['type'].upper()} on port {db_default['port']}"
        )
        use_discovered = ch.confirm_action(
            "Use detected database settings?", default=True
        )

        if use_discovered:
            db_type = db_default["type"]
            db_port = db_default["port"]
        else:
            db_type = ch.prompt_input("Database Type", default=db_default["type"])
            db_port = int(
                ch.prompt_input("Remote Database Port", default=str(db_default["port"]))
            )
    else:
        ch.dim("No database detected. Manual configuration required.")
        if ch.confirm_action("Configure database now?", default=True):
            db_type = ch.prompt_input("Database Type", default="mysql")
            db_port = int(ch.prompt_input("Remote Database Port", default="3306"))
        else:
            db_type = "mysql"
            db_port = 3306
            ch.warning("Skipping database configuration. You can configure it later.")

    # Database credentials (always required from user)
    if ch.confirm_action("Enter database credentials now?", default=True):
        db_name = ch.prompt_input("Database Name")
        db_user = ch.prompt_input("Database User")
        db_pass = ch.prompt_input("Database Password", password=True)
    else:
        db_name = ""
        db_user = ""
        db_pass = ""
        ch.warning("Database credentials can be added later by editing the config.")

    database = {
        "type": db_type,
        "remote_port": db_port,
        "local_tunnel_port": 3307,
        "name": db_name,
        "user": db_user,
        "password": db_pass,
    }

    # Step 4: Paths (with smart defaults from discovery)
    ch.header("Application Paths")

    paths = {
        "web_root": "",
        "logs": "",
        "php_config": "",
        "nginx_config": "",
        "app_storage": "",
    }

    if discovered.get("web_root"):
        ch.success(f"Detected web root: {discovered['web_root']}")
        if ch.confirm_action("Use detected web root?", default=True):
            paths["web_root"] = discovered["web_root"]
        else:
            paths["web_root"] = ch.prompt_input(
                "Web Root Path", default=discovered["web_root"]
            )
    elif ch.confirm_action("Configure paths now?", default=False):
        paths["web_root"] = ch.prompt_input("Web Root Path", default="/var/www/html")

    log_paths = discovered.get("log_paths", [])
    if log_paths:
        paths["logs"] = log_paths[0]  # Use first detected log path

    # Get PHP config path from discovery
    php_info = discovered.get("php_info", {})
    if php_info.get("config_path"):
        paths["php_config"] = php_info["config_path"]

    # Get Nginx config from discovery
    web_servers = discovered.get("web_servers", [])
    for ws in web_servers:
        if ws["type"] == "nginx" and "sites_path" in ws:
            paths["nginx_config"] = ws["sites_path"]

    # Step 5: Services (with smart defaults from discovery)
    services = {
        "web": "nginx",
        "php": "php-fpm",
        "database": "mysql",
        "cache": "redis-server",
    }

    # Update services from discovery
    if web_servers:
        services["web"] = web_servers[0]["type"]

    if discovered_dbs:
        services["database"] = discovered_dbs[0].get("service_name", "mysql")

    if php_info.get("fpm_service"):
        services["php"] = php_info["fpm_service"]

    # Step 6: Metadata
    metadata = {
        "os": discovered.get("os", ""),
        "php_version": php_info.get("version", "") if php_info else "",
        "mysql_version": discovered_dbs[0].get("version", "") if discovered_dbs else "",
        "last_inspected": None,
    }

    # Create server config
    config = config_manager.create_server_config(
        name=name,
        host=host,
        port=port,
        user=user,
        ssh_key=ssh_key,
        ssh_password=ssh_password,
        database=database,
        paths=paths,
        services=services,
    )

    # Update metadata
    config["metadata"].update(metadata)
    config_manager.save_server_config(name, config)

    ch.success(f"✓ Server '{name}' configured successfully!\n")

    # Show summary
    ch.header("Configuration Summary")
    ch.console.print(f"[green]Host: {host}:{port}[/green]")
    ch.console.print(f"[green]User: {user}[/green]")
    ch.console.print(f"[green]OS: {metadata['os']}[/green]")
    if metadata["php_version"]:
        ch.console.print(f"[green]PHP: {metadata['php_version']}[/green]")
    if metadata["mysql_version"]:
        ch.console.print(
            f"[green]Database: {db_type} {metadata['mysql_version']}[/green]"
        )
    if paths["web_root"]:
        ch.console.print(f"[green]Web Root: {paths['web_root']}[/green]")

    # Ask if they want to make it active
    ch.newline()
    if ch.confirm_action("Make this the active server?", default=True):
        config_manager.set_active_server(name)
        ch.success(f"✓ Active server set to: {name}")


def remove_server(name: str, options: dict[str, Any]):
    """Remove server configuration."""
    if not config_manager.server_exists(name):
        ch.error(f"Server '{name}' not found.")
        return

    if not options.get("yes"):
        if not ch.confirm_action(f"Are you sure you want to remove server '{name}'?"):
            ch.warning("Cancelled.")
            return

    config_manager.delete_server_config(name)

    # If this was the active server, clear it
    if config_manager.get_active_server() == name:
        config_manager.active_server_file.unlink(missing_ok=True)

    ch.success(f"Server '{name}' removed.")


def inspect_server(options: dict[str, Any]):
    """Auto-discover server details and update configuration."""
    from navig.server_template_manager import ServerTemplateManager

    active = config_manager.get_active_server()
    if not active:
        ch.error("No active server. Use 'navig server use <name>' first.")
        return

    ch.info(f"Inspecting server: {active}\n")

    # Load existing config
    try:
        server_config = config_manager.load_server_config(active)
    except Exception as e:
        ch.error(f"Error loading server config: {e}")
        return

    # Run discovery
    ssh_config = {
        "host": server_config["host"],
        "port": server_config.get("port", 22),
        "user": server_config["user"],
        "ssh_key": server_config.get("ssh_key"),
        "ssh_password": server_config.get("ssh_password"),
    }

    # Get debug logger from options if available
    debug_logger = options.get("debug_logger") if options else None
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)

    # Test connection
    if not discovery.test_connection():
        ch.error("✗ Could not connect to server")
        return

    # Run full discovery
    discovered = discovery.discover_all(progress=True)

    # Discover templates
    detected_templates = discovery.discover_templates()

    if not discovered:
        ch.warning("No new information discovered")
        return

    # Update server configuration with discovered info
    updates = {
        "metadata": {
            "os": discovered.get("os", server_config.get("metadata", {}).get("os", "")),
            "kernel": discovered.get("kernel", ""),
            "php_version": discovered.get("version", ""),  # PHP version is flat key
            "mysql_version": "",
        }
    }

    # Update database info
    discovered_dbs = discovered.get("databases", [])
    if discovered_dbs:
        db = discovered_dbs[0]
        if "database" in server_config:
            server_config["database"]["type"] = db["type"]
            server_config["database"]["remote_port"] = db["port"]
        updates["metadata"]["mysql_version"] = db.get("version", "")

    # Update PHP services and paths
    if discovered.get("fpm_service"):
        if "services" not in server_config:
            server_config["services"] = {}
        server_config["services"]["php"] = discovered["fpm_service"]
    if discovered.get("config_path"):
        if "paths" not in server_config:
            server_config["paths"] = {}
        server_config["paths"]["php_config"] = discovered["config_path"]

    # Update web server info
    web_servers = discovered.get("web_servers", [])
    if web_servers:
        ws = web_servers[0]
        if "services" not in server_config:
            server_config["services"] = {}
        server_config["services"]["web"] = ws["type"]

        if ws["type"] == "nginx" and "sites_path" in ws:
            if "paths" not in server_config:
                server_config["paths"] = {}
            server_config["paths"]["nginx_config"] = ws["sites_path"]

    # Update paths
    if discovered.get("web_root"):
        if "paths" not in server_config:
            server_config["paths"] = {}
        if not server_config["paths"].get("web_root"):
            server_config["paths"]["web_root"] = discovered["web_root"]

    log_paths = discovered.get("log_paths", [])
    if log_paths:
        if "paths" not in server_config:
            server_config["paths"] = {}
        if not server_config["paths"].get("logs"):
            server_config["paths"]["logs"] = log_paths[0]

    # Save updated configuration
    config_manager.update_server_metadata(active, updates["metadata"])

    # Initialize detected templates
    if detected_templates:
        ch.dim("")  # Spacing
        template_manager = ServerTemplateManager(config_manager)
        template_manager.initialize_templates_from_detection(active, detected_templates)

    ch.success(f"✓ Server '{active}' configuration updated\n")
    ch.header("Updated Information")
    ch.console.print(f"[green]OS: {updates['metadata']['os']}[/green]")
    if updates["metadata"]["php_version"]:
        ch.console.print(f"[green]PHP: {updates['metadata']['php_version']}[/green]")
    if updates["metadata"]["mysql_version"]:
        ch.console.print(
            f"[green]Database: {updates['metadata']['mysql_version']}[/green]"
        )
    if detected_templates:
        ch.info(f"Templates: {', '.join(detected_templates.keys())}", style="cyan")


from typing import Any

import typer

from navig.cli import deprecation_warning

server_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host'] Server operations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@server_app.callback()
def server_callback(ctx: typer.Context):
    """Server management - DEPRECATED, use 'navig host'."""
    deprecation_warning("navig server", "navig host")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_web_menu

        launch_web_menu()
        raise typer.Exit()


@server_app.command("list")
def server_list(
    ctx: typer.Context,
    vhosts: bool = typer.Option(False, "--vhosts", help="List virtual hosts"),
    containers: bool = typer.Option(
        False, "--containers", help="List Docker containers"
    ),
    all: bool = typer.Option(False, "--all", "-a", help="Show all (including stopped)"),
    filter: str | None = typer.Option(None, "--filter", "-f", help="Filter by name"),
    hestia_users: bool = typer.Option(
        False, "--hestia-users", help="List HestiaCP users"
    ),
    hestia_domains: bool = typer.Option(
        False, "--hestia-domains", help="List HestiaCP domains"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List server resources (vhosts, containers, etc.)."""
    if vhosts:
        from navig.commands.webserver import list_vhosts

        list_vhosts(ctx.obj)
    elif containers:
        from navig.commands.docker import docker_ps

        docker_ps(ctx.obj, all=all, filter=filter, format="table")
    elif hestia_users:
        from navig.commands.hestia import list_users_cmd

        ctx.obj["plain"] = plain
        list_users_cmd(ctx.obj)
    elif hestia_domains:
        from navig.commands.hestia import list_domains_cmd

        ctx.obj["plain"] = plain
        list_domains_cmd(None, ctx.obj)
    else:
        # Default: show containers
        from navig.commands.docker import docker_ps

        docker_ps(ctx.obj, all=all, filter=filter, format="table")


@server_app.command("show")
def server_show(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Container to inspect"
    ),
    stats: bool = typer.Option(False, "--stats", help="Show container stats"),
):
    """Show server details."""
    if container:
        if stats:
            from navig.commands.docker import docker_stats

            docker_stats(ctx.obj, container=container, no_stream=True)
        else:
            from navig.commands.docker import docker_inspect

            docker_inspect(container, ctx.obj, format=None)
    else:
        ch.error("Specify --container <name>")


@server_app.command("test")
def server_test(
    ctx: typer.Context,
    filesystem: bool = typer.Option(False, "--filesystem", help="Check filesystem"),
):
    """Test server configuration."""
    if filesystem:
        from navig.commands.maintenance import check_filesystem

        check_filesystem(ctx.obj)
    else:
        from navig.commands.webserver import test_config

        test_config(ctx.obj)


@server_app.command("run")
def server_run(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Container name"
    ),
    command: str | None = typer.Option(
        None, "--command", "--cmd", help="Command to execute"
    ),
    enable: str | None = typer.Option(None, "--enable", help="Enable site/container"),
    disable: str | None = typer.Option(
        None, "--disable", help="Disable site/container"
    ),
    restart: str | None = typer.Option(
        None, "--restart", help="Restart service/container"
    ),
    stop: str | None = typer.Option(None, "--stop", help="Stop container"),
    start: str | None = typer.Option(None, "--start", help="Start container"),
    reload: bool = typer.Option(False, "--reload", help="Reload web server"),
    update_packages: bool = typer.Option(
        False, "--update-packages", help="Update system packages"
    ),
    clean_packages: bool = typer.Option(
        False, "--clean-packages", help="Clean package cache"
    ),
    cleanup_temp: bool = typer.Option(False, "--cleanup-temp", help="Clean temp files"),
    maintenance: bool = typer.Option(False, "--maintenance", help="Full maintenance"),
):
    """Run server operations."""
    if container and command:
        from navig.commands.docker import docker_exec

        docker_exec(
            container, command, ctx.obj, interactive=False, user=None, workdir=None
        )
    elif enable:
        from navig.commands.webserver import enable_site

        ctx.obj["site_name"] = enable
        enable_site(ctx.obj)
    elif disable:
        from navig.commands.webserver import disable_site

        ctx.obj["site_name"] = disable
        disable_site(ctx.obj)
    elif restart:
        from navig.commands.docker import docker_restart

        docker_restart(restart, ctx.obj, timeout=10)
    elif stop:
        from navig.commands.docker import docker_stop

        docker_stop(stop, ctx.obj, timeout=10)
    elif start:
        from navig.commands.docker import docker_start

        docker_start(start, ctx.obj)
    elif reload:
        from navig.commands.webserver import reload_server

        reload_server(ctx.obj)
    elif update_packages:
        from navig.commands.maintenance import update_packages

        update_packages(ctx.obj)
    elif clean_packages:
        from navig.commands.maintenance import clean_packages

        clean_packages(ctx.obj)
    elif cleanup_temp:
        from navig.commands.maintenance import cleanup_temp

        cleanup_temp(ctx.obj)
    elif maintenance:
        from navig.commands.maintenance import system_maintenance

        system_maintenance(ctx.obj)
    else:
        ch.error("Specify an action (--restart, --enable, --disable, etc.)")


# ============================================================================
# TASK/WORKFLOW (Canonical 'task' group - alias for workflow)
# ============================================================================
