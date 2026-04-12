"""
App Management Commands

Manage apps on remote hosts. Each host can have multiple apps.
"""

import os
import platform
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from navig import console_helper as ch
from navig.config import get_config_manager

config_manager = get_config_manager()
console = ch.get_console()


def list_apps(options: dict[str, Any]) -> None:
    """List all apps on the active host (or host specified via --host flag)."""
    host_name = options.get("host")
    show_all = options.get("all", False)
    output_format = options.get("format", "table")
    list_all_hosts = options.get("list_all_hosts", False)

    # If --all flag is used without --host, list apps from all hosts
    if show_all and not host_name:
        list_all_hosts = True

    if list_all_hosts:
        # List apps from all hosts
        all_apps = []
        active_app = config_manager.get_active_app()

        for host in config_manager.list_hosts():
            try:
                apps = config_manager.list_apps(host)
                host_config = config_manager.load_host_config(host)
                default_app = host_config.get("default_app")

                for app_name in apps:
                    try:
                        app_config = config_manager.load_app_config(host, app_name)

                        app_info = {
                            "host": host,
                            "app": app_name,
                            "webserver": app_config.get("webserver", {}).get(
                                "type", "N/A"
                            ),
                            "database": app_config.get("database", {}).get(
                                "type", "N/A"
                            ),
                            "is_active": app_name == active_app,
                            "is_default": app_name == default_app,
                        }

                        if show_all:
                            app_info["paths"] = app_config.get("paths", {})

                        all_apps.append(app_info)
                    except Exception:
                        continue
            except Exception:
                continue

        if not all_apps:
            ch.warning("No apps found on any host")
            return

        # Output based on format
        if options.get("json"):
            import json

            ch.raw_print(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "command": "app.list",
                        "success": True,
                        "apps": all_apps,
                        "count": len(all_apps),
                        "scope": "all-hosts",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        if output_format == "json":
            import json

            print(json.dumps(all_apps, indent=2))
        elif output_format == "yaml":
            import yaml

            print(yaml.dump(all_apps, default_flow_style=False, sort_keys=False))
        elif options.get("plain"):
            # Plain text output - one app per line for scripting
            for app_info in all_apps:
                ch.raw_print(app_info["app"])
        else:  # table format
            table = ch.create_table(
                title="All Apps",
                columns=[
                    {"name": "Host", "style": "cyan"},
                    {"name": "App", "style": "green"},
                    {"name": "Webserver", "style": "blue"},
                    {"name": "Database", "style": "yellow"},
                ],
            )

            for app_info in all_apps:
                # Color-code active/default apps
                app_style = (
                    "bold green"
                    if app_info["is_active"]
                    else ("bold yellow" if app_info["is_default"] else "green")
                )

                table.add_row(
                    app_info["host"],
                    f"[{app_style}]{app_info['app']}[/{app_style}]",
                    app_info["webserver"],
                    app_info["database"],
                )

            ch.print_table(table)
        return

    # Get active host if not specified — interactive recovery if missing
    if not host_name:
        from navig.cli.recovery import require_active_host
        host_name = require_active_host({"host": None}, config_manager)

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    # Get apps on this host
    try:
        apps = config_manager.list_apps(host_name)
        host_config = config_manager.load_host_config(host_name)
        default_app = host_config.get("default_app")
        active_app = config_manager.get_active_app()
    except Exception as e:
        ch.error(f"Error listing apps on host '{host_name}'", str(e))
        return

    if not apps:
        from navig.cli.recovery import empty_list_recovery
        empty_list_recovery("app", f"app add --host {host_name}")
        return

    # Collect app data
    app_data = []
    for app_name in apps:
        try:
            app_config = config_manager.load_app_config(host_name, app_name)

            app_info = {
                "app": app_name,
                "webserver": app_config.get("webserver", {}).get("type", "N/A"),
                "database": app_config.get("database", {}).get("type", "N/A"),
                "is_active": app_name == active_app,
                "is_default": app_name == default_app,
            }

            if show_all:
                app_info["paths"] = app_config.get("paths", {})
                app_info["database_name"] = app_config.get("database", {}).get(
                    "name", "N/A"
                )

            app_data.append(app_info)
        except Exception as e:
            app_data.append(
                {
                    "app": app_name,
                    "webserver": "ERROR",
                    "database": str(e),
                    "is_active": False,
                    "is_default": False,
                }
            )

    # Output based on format
    if options.get("json"):
        import json

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "app.list",
                    "success": True,
                    "host": host_name,
                    "apps": app_data,
                    "count": len(app_data),
                    "scope": "single-host",
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif output_format == "json":
        import json

        print(json.dumps(app_data, indent=2))
    elif output_format == "yaml":
        import yaml

        print(yaml.dump(app_data, default_flow_style=False, sort_keys=False))
    elif options.get("plain"):
        # Plain text output - one app per line for scripting
        for app_info in app_data:
            ch.raw_print(app_info["app"])
    else:  # table format
        if show_all:
            table = ch.create_table(
                title=f"Apps on host '{host_name}' (Detailed)",
                columns=[
                    {"name": "App", "style": "cyan"},
                    {"name": "Webserver", "style": "green"},
                    {"name": "Database", "style": "blue"},
                    {"name": "DB Name", "style": "yellow"},
                ],
            )

            for app_info in app_data:
                # Color-code active/default apps
                app_style = (
                    "bold green"
                    if app_info["is_active"]
                    else ("bold yellow" if app_info["is_default"] else "cyan")
                )

                table.add_row(
                    f"[{app_style}]{app_info['app']}[/{app_style}]",
                    app_info["webserver"],
                    app_info["database"],
                    app_info.get("database_name", "N/A"),
                )
        else:
            table = ch.create_table(
                title=f"Apps on host '{host_name}'",
                columns=[
                    {"name": "App", "style": "cyan"},
                    {"name": "Webserver", "style": "green"},
                    {"name": "Database", "style": "blue"},
                ],
            )

            for app_info in app_data:
                # Color-code active/default apps
                app_style = (
                    "bold green"
                    if app_info["is_active"]
                    else ("bold yellow" if app_info["is_default"] else "cyan")
                )

                table.add_row(
                    f"[{app_style}]{app_info['app']}[/{app_style}]",
                    app_info["webserver"],
                    app_info["database"],
                )

        ch.print_table(table)


def use_app(options: dict[str, Any]) -> None:
    """
    Switch active app (global or local scope).

    Args:
        options: Command options
            - app_name: Name of app to activate
            - local: If True, set as local active app (current directory only)
            - clear_local: If True, clear local active app setting
            - quiet: Suppress success messages
    """
    # Handle --clear-local flag
    if options.get("clear_local"):
        try:
            config_manager.clear_active_app_local()
            if not options.get("quiet"):
                ch.success("✓ Cleared local active app setting")
                ch.info("Commands will now use global active app")
        except FileNotFoundError as e:
            ch.error("Cannot clear local active app", str(e))
        except Exception as e:
            ch.error("Failed to clear local active app", str(e))
        return

    app_name = options.get("app_name")

    if not app_name:
        ch.error("App name is required")
        return

    # Get active host
    from navig.cli.recovery import require_active_host  # noqa: PLC0415
    host_name = require_active_host(options, config_manager)

    # Verify app exists on active host
    if not config_manager.app_exists(host_name, app_name):
        ch.error(
            f"App '{app_name}' not found on host '{host_name}'",
            "Use 'navig app list' to see available apps.",
        )
        return

    # Determine scope (local or global)
    local_scope = options.get("local", False)

    # Set active app
    try:
        config_manager.set_active_app(app_name, local=local_scope)

        if not options.get("quiet"):
            if local_scope:
                ch.success(f"✓ Set local active app to '{app_name}'")
                ch.info("This only affects commands run in this directory")
                ch.dim(f"Location: {Path.cwd() / '.navig' / 'config.yaml'}")
            else:
                ch.success(f"✓ Set global active app to '{app_name}'")
                ch.info("This affects all directories without local active app")
    except FileNotFoundError as e:
        ch.error("Cannot set local active app", str(e))
    except ValueError as e:
        ch.error("Invalid app", str(e))
    except Exception as e:
        ch.error("Failed to set active app", str(e))


def current_app(options: dict[str, Any]) -> None:
    """Show currently active app with source information (local vs global)."""
    active_host = config_manager.get_active_host()
    active_app, source = config_manager.get_active_app(return_source=True)

    if options.get("raw"):
        if active_app:
            ch.raw_print(active_app)
        return

    if not active_host:
        from navig.cli.recovery import require_active_host
        active_host = require_active_host({}, config_manager)

    if not active_app:
        from navig.cli.recovery import empty_list_recovery
        empty_list_recovery("app", "app add")
        return

    # Map source to display format
    source_display = {
        "local": "📍 local (.navig/)",
        "legacy": "📄 legacy (.navig file)",
        "global": "🌐 global (~/.navig/)",
        "default": "⚙️  default (from host config)",
        "none": "none",
    }

    ch.header("Active Context")
    ch.console.print(f"[cyan]  Host:    {active_host}[/cyan]")
    ch.console.print(
        f"[green]  App: {active_app}[/green] {source_display.get(source, '')}"
    )


def add_app(options: dict[str, Any]) -> None:
    """Add new app to a host configuration."""
    app_name = options.get("app_name")
    host_name = options.get("host")

    if not app_name:
        ch.error("App name is required")
        return

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    # Check if app already exists
    if config_manager.app_exists(host_name, app_name):
        ch.error(f"App '{app_name}' already exists on host '{host_name}'")
        return

    ch.info(f"Adding new app '{app_name}' to host '{host_name}'")
    ch.dim("Press Ctrl+C to cancel at any time.\n")

    # Load host configuration to detect webserver type
    try:
        host_config = config_manager.load_host_config(host_name)
    except Exception as e:
        ch.error("Error loading host configuration", str(e))
        return

    # Auto-detect webserver type from host configuration
    detected_webserver = None
    if "services" in host_config and "web" in host_config.get("services", {}):
        web_service = host_config["services"]["web"].lower()
        if "nginx" in web_service:
            detected_webserver = "nginx"
        elif "apache" in web_service:
            detected_webserver = "apache2"

    # Prompt for required fields
    ch.header("App Configuration")

    # Webserver type (auto-detected from host, with option to override)
    if detected_webserver:
        ch.info(f"Detected webserver: {detected_webserver} (from host configuration)")
        if ch.confirm_action(f"Use {detected_webserver} for this app?", default=True):
            webserver_type = detected_webserver
        else:
            webserver_type = ch.prompt_choice(
                "Webserver Type", ["nginx", "apache2"], default=detected_webserver
            )
    else:
        ch.warning("Could not auto-detect webserver type from host configuration")
        webserver_type = ch.prompt_choice(
            "Webserver Type (REQUIRED)", ["nginx", "apache2"], default="nginx"
        )

    # Database configuration
    if ch.confirm_action("Configure database?", default=True):
        db_type = ch.prompt_choice(
            "Database Type", ["mysql", "postgresql", "mariadb"], default="mysql"
        )
        db_name = ch.prompt_input("Database Name")
        db_user = ch.prompt_input("Database User")
        db_pass = ch.prompt_input("Database Password", password=True)

        database = {
            "type": db_type,
            "name": db_name,
            "user": db_user,
            "password": db_pass,
        }
    else:
        database = {}

    # Paths configuration
    if ch.confirm_action("Configure paths?", default=True):
        ch.dim("App Root: Project root directory (contains source code)")
        ch.dim("Web Root: Public directory served by web server (e.g., /public)")
        ch.newline()

        app_root = ch.prompt_input("App Root Path", default=f"/var/www/{app_name}")

        # Suggest web_root based on app_root
        suggested_web_root = app_root
        if webserver_type == "nginx":
            # Common patterns for different frameworks
            suggested_web_root = f"{app_root}/public"  # Laravel, Symfony

        web_root = ch.prompt_input("Web Root Path", default=suggested_web_root)
        log_path = ch.prompt_input("Log Path", default=f"/var/log/{app_name}")

        paths = {
            "app_root": app_root,
            "web_root": web_root,
            "log_path": log_path,
        }
    else:
        paths = {}

    # Create app configuration
    app_config = {"webserver": {"type": webserver_type}}

    if database:
        app_config["database"] = database

    if paths:
        app_config["paths"] = paths

    # Save app configuration
    try:
        config_manager.save_app_config(host_name, app_name, app_config)
        ch.success(f"✓ App '{app_name}' added to host '{host_name}'\n")

        # Show next steps
        ch.header("Next Steps")
        ch.console.print(f"[cyan]1. Set as active app: navig app use {app_name}[/cyan]")
        ch.console.print(
            f"[cyan]2. View configuration: navig config show {host_name}:{app_name}[/cyan]"
        )
        ch.console.print(
            f"[cyan]3. Deploy application to {paths.get('web_root', '/var/www/' + app_name)}[/cyan]"
        )

        # Ask if they want to make it active
        ch.newline()
        if ch.confirm_action("Make this the active app?", default=True):
            config_manager.set_active_app(app_name)
            ch.success(f"✓ Active app set to: {app_name}")
    except Exception as e:
        ch.error("Error saving app configuration", str(e))


def remove_app(options: dict[str, Any]) -> None:
    """Remove app from a host configuration."""
    app_name = options.get("app_name")
    host_name = options.get("host")
    force = options.get("force", False)
    quiet = options.get("quiet", False)

    if not app_name:
        if not quiet:
            ch.error("App name is required")
        return

    # Get active host if not specified
    if not host_name:
        host_name = config_manager.get_active_host()

    if not host_name:
        if not quiet:
            ch.error(
                "No active host configured",
                "Use 'navig host use <name>' or specify --host flag.",
            )
        return

    # Verify app exists
    if not config_manager.app_exists(host_name, app_name):
        if not quiet:
            ch.error(f"App '{app_name}' not found on host '{host_name}'")
        return

    # Confirm deletion
    if not force:
        if not ch.confirm_action(
            f"Are you sure you want to remove app '{app_name}' from host '{host_name}'?"
        ):
            ch.warning("Cancelled.")
            return

    # Delete app
    try:
        config_manager.delete_app_config(host_name, app_name)

        # If this was the active app, clear it
        if config_manager.get_active_app() == app_name:
            config_manager.active_app_file.unlink(missing_ok=True)

        if not quiet:
            ch.success(f"App '{app_name}' removed from host '{host_name}'")
    except Exception as e:
        if not quiet:
            ch.error("Error removing app", str(e))
        raise


def show_app(options: dict[str, Any]) -> None:
    """Show detailed app configuration."""
    app_name = options.get("app_name")
    host_name = options.get("host")
    json_output = options.get("json", False)

    if not app_name:
        ch.error("App name is required")
        return

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Load app configuration
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except Exception as e:
        ch.error("Error loading app configuration", str(e))
        return

    # Output format
    if json_output:
        import json

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "app.show",
                    "success": True,
                    "host": host_name,
                    "app": app_name,
                    "config": app_config,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        ch.header(f"App Configuration: {host_name}:{app_name}")
        ch.newline()

        # Display as YAML
        yaml_output = yaml.dump(app_config, default_flow_style=False, sort_keys=False)
        console.print(yaml_output, style="cyan")

        ch.newline()
        ch.dim(f"Configuration file: ~/.navig/hosts/{host_name}.yaml")


def edit_app(options: dict[str, Any]) -> None:
    """Edit app configuration in default editor."""

    app_name = options.get("app_name")
    host_name = options.get("host")

    if not app_name:
        ch.error("App name is required")
        return

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Verify app exists
    if not config_manager.app_exists(host_name, app_name):
        ch.error(f"App '{app_name}' not found on host '{host_name}'")
        return

    # Try to find individual app file first (new format)
    config_file = None
    app_file = config_manager.apps_dir / f"{app_name}.yaml"

    if app_file.exists():
        config_file = app_file
        file_type = "individual app file"
    else:
        # Fall back to host config file (legacy embedded format)
        host_file = config_manager.hosts_dir / f"{host_name}.yaml"

        if not host_file.exists():
            # Try legacy format
            host_file = config_manager.apps_dir / f"{host_name}.yaml"

        if host_file.exists():
            config_file = host_file
            file_type = "host configuration (legacy embedded format)"
        else:
            ch.error(f"Configuration file not found for app '{app_name}'")
            return

    # Determine editor
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    if not editor:
        # Use platform-specific defaults
        system = platform.system()
        if system == "Windows":
            editor = "notepad"
        elif system == "Darwin":  # macOS
            editor = "open -e"
        else:  # Linux
            editor = "nano"

    ch.info(f"Opening {config_file} ({file_type}) in {editor}...")
    if file_type == "host configuration (legacy embedded format)":
        ch.dim(f"Navigate to app '{app_name}' section in the file.\n")
    else:
        ch.dim("Editing individual app file.\n")

    try:
        # Open editor - use shlex.split for safe argument parsing
        if platform.system() == "Windows" and editor == "notepad":
            subprocess.run([editor, str(config_file)], check=True)
        else:
            # Safely split editor command and add file path
            editor_cmd = shlex.split(editor)
            editor_cmd.append(str(config_file))
            subprocess.run(editor_cmd, check=True)

        ch.success("✓ Configuration file closed")
        ch.dim("Changes will take effect immediately.")
    except subprocess.CalledProcessError as e:
        ch.error("Error opening editor", str(e))
    except Exception as e:
        ch.error("Unexpected error", str(e))


def clone_app(options: dict[str, Any]) -> None:
    """Clone an existing app configuration."""
    source_name = options.get("source_name")
    new_name = options.get("new_name")
    host_name = options.get("host")

    if not source_name or not new_name:
        ch.error("Both source and new app names are required")
        return

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Verify source exists
    if not config_manager.app_exists(host_name, source_name):
        ch.error(f"Source app '{source_name}' not found on host '{host_name}'")
        return

    # Verify new name doesn't exist
    if config_manager.app_exists(host_name, new_name):
        ch.error(f"App '{new_name}' already exists on host '{host_name}'")
        return

    # Load source configuration
    try:
        source_config = config_manager.load_app_config(host_name, source_name)
    except Exception as e:
        ch.error("Error loading source app configuration", str(e))
        return

    ch.info(f"Cloning app '{source_name}' to '{new_name}' on host '{host_name}'")
    ch.dim("The new app will have the same configuration as the source.\n")

    # Update paths if they exist
    if "paths" in source_config:
        paths = source_config["paths"]

        # Update web_root if it contains the source app name
        if "web_root" in paths and source_name in paths["web_root"]:
            paths["web_root"] = paths["web_root"].replace(source_name, new_name)

        # Update log_path if it contains the source app name
        if "log_path" in paths and source_name in paths["log_path"]:
            paths["log_path"] = paths["log_path"].replace(source_name, new_name)

    # Update database name if it contains the source app name
    if "database" in source_config and "name" in source_config["database"]:
        db_name = source_config["database"]["name"]
        if source_name in db_name:
            source_config["database"]["name"] = db_name.replace(source_name, new_name)

    # Save cloned configuration
    try:
        config_manager.save_app_config(host_name, new_name, source_config)
        ch.success(f"✓ App '{new_name}' created as clone of '{source_name}'\n")

        # Show what was updated
        ch.header("Cloned Configuration")
        if "paths" in source_config:
            ch.console.print(
                f"[cyan]Web Root: {source_config['paths'].get('web_root', 'N/A')}[/cyan]"
            )
            ch.console.print(
                f"[cyan]Log Path: {source_config['paths'].get('log_path', 'N/A')}[/cyan]"
            )
        if "database" in source_config:
            ch.console.print(
                f"[cyan]Database: {source_config['database'].get('name', 'N/A')}[/cyan]"
            )

        ch.newline()
        ch.header("Next Steps")
        ch.console.print(
            f"[green]1. Edit configuration: navig app edit {new_name}[/green]"
        )
        ch.console.print(f"[green]2. Set as active: navig app use {new_name}[/green]")
        ch.console.print("[green]3. Deploy application to the new paths[/green]")
    except Exception as e:
        ch.error("Error saving cloned app configuration", str(e))


def info_app(options: dict[str, Any]) -> None:
    """Show detailed app information (webserver type, database, paths, etc.)."""
    app_name = options.get("app_name")
    host_name = options.get("host")
    json_output = options.get("json", False)

    if not app_name:
        ch.error("App name is required")
        return

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Verify app exists
    if not config_manager.app_exists(host_name, app_name):
        ch.error(f"App '{app_name}' not found on host '{host_name}'")
        return

    # Load app configuration
    try:
        app_config = config_manager.load_app_config(host_name, app_name)
    except Exception as e:
        ch.error("Error loading app configuration", str(e))
        return

    # Get additional info
    active_app = config_manager.get_active_app()
    host_config = config_manager.load_host_config(host_name)
    default_app = host_config.get("default_app")

    # Build info dictionary
    info = {
        "name": app_name,
        "host": host_name,
        "webserver": app_config.get("webserver", {}),
        "database": app_config.get("database", {}),
        "paths": app_config.get("paths", {}),
        "is_active": app_name == active_app,
        "is_default": app_name == default_app,
    }

    # Output format
    if json_output:
        import json

        print(json.dumps(info, indent=2))
    else:
        ch.header(f"App Information: {host_name}:{app_name}")
        ch.newline()

        # Status
        ch.subheader("Status")
        status_items = []
        if info["is_active"]:
            status_items.append("✓ Active")
        if info["is_default"]:
            status_items.append("★ Default")
        if status_items:
            ch.console.print(f"[green]  {', '.join(status_items)}[/green]")
        else:
            ch.dim("  Not active or default")
        ch.newline()

        # Webserver
        ch.subheader("Webserver")
        webserver = info["webserver"]
        if webserver:
            ch.console.print(f"[cyan]  Type: {webserver.get('type', 'N/A')}[/cyan]")
        else:
            ch.dim("  Not configured")
        ch.newline()

        # Database
        ch.subheader("Database")
        database = info["database"]
        if database:
            ch.console.print(f"[blue]  Type:     {database.get('type', 'N/A')}[/blue]")
            ch.console.print(f"[blue]  Name:     {database.get('name', 'N/A')}[/blue]")
            ch.console.print(f"[blue]  User:     {database.get('user', 'N/A')}[/blue]")
        else:
            ch.dim("  Not configured")
        ch.newline()

        # Paths
        ch.subheader("Paths")
        paths = info["paths"]
        if paths:
            if "web_root" in paths:
                ch.console.print(f"[green]  Web Root: {paths['web_root']}[/green]")
            if "log_path" in paths:
                ch.console.print(f"[green]  Logs:     {paths['log_path']}[/green]")
        else:
            ch.dim("  Not configured")
        ch.newline()

        # Configuration file location
        host_file = config_manager.hosts_dir / f"{host_name}.yaml"
        if not host_file.exists():
            host_file = config_manager.apps_dir / f"{host_name}.yaml"

        ch.dim(f"Configuration: {host_file}")


def search_apps(options: dict[str, Any]) -> None:
    """Search for apps across all hosts by name or configuration."""
    query = options.get("query")
    json_output = options.get("json", False)

    if not query:
        ch.error("Search query is required")
        return

    query_lower = query.lower()
    results = []

    # Search through all hosts
    for host_name in config_manager.list_hosts():
        try:
            apps = config_manager.list_apps(host_name)

            for app_name in apps:
                # Check if app name matches
                if query_lower in app_name.lower():
                    try:
                        app_config = config_manager.load_app_config(host_name, app_name)
                        results.append(
                            {
                                "host": host_name,
                                "app": app_name,
                                "webserver": app_config.get("webserver", {}).get(
                                    "type", "N/A"
                                ),
                                "database": app_config.get("database", {}).get(
                                    "type", "N/A"
                                ),
                            }
                        )
                    except Exception:
                        continue
        except Exception:
            continue

    if not results:
        ch.warning(f"No apps found matching '{query}'")
        return

    # Output format
    if json_output:
        import json

        print(json.dumps(results, indent=2))
    else:
        ch.header(f"Search Results for '{query}' ({len(results)} found)")
        ch.newline()

        # Create rich table
        table = ch.create_table(
            title=None,
            columns=[
                {"name": "Host", "style": "cyan"},
                {"name": "App", "style": "green"},
                {"name": "Webserver", "style": "blue"},
                {"name": "Database", "style": "yellow"},
            ],
        )

        for result in results:
            table.add_row(
                result["host"], result["app"], result["webserver"], result["database"]
            )

        ch.print_table(table)


def migrate_apps(options: dict[str, Any]) -> None:
    """
    Migrate apps from host YAML (legacy embedded format) to individual files (new format).

    This command converts apps stored in hosts/<host>.yaml under the 'apps:' field
    to individual .navig/apps/<app>.yaml files.
    """
    host_name = options.get("host")
    dry_run = options.get("dry_run", False)

    # Get active host if not specified
    if not host_name:
        from navig.cli.recovery import require_active_host  # noqa: PLC0415
        host_name = require_active_host(options, config_manager)

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    ch.header(f"App Migration: {host_name}")
    ch.info("Converting apps from embedded format to individual files")

    if dry_run:
        ch.warning("DRY RUN MODE - No changes will be made\n")
    else:
        ch.dim("This will move apps from host YAML to individual files\n")

    # Load host configuration to check for embedded apps
    try:
        host_config = config_manager.load_host_config(host_name)
    except Exception as e:
        ch.error("Error loading host configuration", str(e))
        return

    # Check if host has embedded apps
    if "apps" not in host_config or not host_config["apps"]:
        ch.warning(f"No embedded apps found in host '{host_name}'")
        ch.info("This host is already using the new format or has no apps.")
        return

    # Show what will be migrated
    app_count = len(host_config["apps"])
    ch.info(f"Found {app_count} app(s) to migrate:")
    for app_name in host_config["apps"].keys():
        ch.console.print(f"  • {app_name}")
    ch.newline()

    # Confirm migration
    if not dry_run:
        if not ch.confirm_action(
            f"Migrate {app_count} app(s) to individual files?", default=True
        ):
            ch.warning("Migration cancelled.")
            return

    # Perform migration
    if dry_run:
        ch.info("Would migrate the following apps:")
        for app_name in host_config["apps"].keys():
            ch.console.print(f"  ✓ {app_name} → .navig/apps/{app_name}.yaml")
        ch.newline()
        ch.success("Dry run complete - no changes made")
    else:
        results = config_manager.migrate_apps_to_files(host_name, remove_from_host=True)

        # Show results
        ch.newline()
        ch.header("Migration Results")

        if results["migrated"]:
            ch.success(f"✓ Migrated {len(results['migrated'])} app(s):")
            for app_name in results["migrated"]:
                ch.console.print(f"  • {app_name} → .navig/apps/{app_name}.yaml")

        if results["skipped"]:
            ch.warning(
                f"⚠ Skipped {len(results['skipped'])} app(s) (already exist as individual files):"
            )
            for app_name in results["skipped"]:
                ch.console.print(f"  • {app_name}")

        if results["errors"]:
            ch.error(f"✗ Failed to migrate {len(results['errors'])} app(s):")
            for app_name, error in results["errors"].items():
                ch.console.print(f"  • {app_name}: {error}")

        ch.newline()

        if results["migrated"]:
            ch.success("Migration complete!")
            ch.info("Apps are now stored in individual files under .navig/apps/")
            ch.dim("The host configuration has been updated to remove embedded apps.")
        else:
            ch.warning("No apps were migrated.")


from typing import Any

import typer

from navig.cli import show_subcommand_help
from navig.console_helper import get_console
from navig.deprecation import deprecation_warning

app_app = typer.Typer(
    help="Manage apps on hosts",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app_app.callback()
def app_callback(ctx: typer.Context):
    """App management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            show_subcommand_help("app", ctx)
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("app", app_app)


@app_app.command("list")
def app_list(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host to list apps from"
    ),
    all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all apps from all hosts with detailed information",
    ),
    format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json, yaml"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one app per line) for scripting"
    ),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all apps on a host."""
    from navig.commands.app import list_apps

    if host:
        ctx.obj["host"] = host
    ctx.obj["all"] = all
    ctx.obj["format"] = "json" if json else format
    ctx.obj["plain"] = plain
    if json:
        ctx.obj["json"] = True
    list_apps(ctx.obj)


@app_app.command("use")
def app_use(
    ctx: typer.Context,
    app_name: str | None = typer.Argument(None, help="App name to activate"),
    local: bool = typer.Option(
        False, "--local", "-l", help="Set as local active app (current directory only)"
    ),
    clear_local: bool = typer.Option(
        False, "--clear-local", help="Clear local active app setting"
    ),
):
    """
    Set active app (global or local scope).

    Examples:
        navig app use myapp          # Set global active app
        navig app use myapp --local  # Set local active app (current dir)
        navig app use --clear-local     # Remove local active app setting
    """
    from navig.commands.app import use_app

    if app_name:
        ctx.obj["app_name"] = app_name
    ctx.obj["local"] = local
    ctx.obj["clear_local"] = clear_local
    use_app(ctx.obj)


@app_app.command("current", hidden=True)
def app_current(ctx: typer.Context):
    """[DEPRECATED: Use 'navig app show --current'] Show currently active app."""
    deprecation_warning("navig app current", "navig app show --current")
    from navig.commands.app import current_app

    current_app(ctx.obj)


@app_app.command("add")
def app_add(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to add"),
    host: str | None = typer.Option(None, "--host", "-h", help="Host to add app to"),
    from_app: str | None = typer.Option(None, "--from", help="Clone from existing app"),
):
    """Add new app to a host (or clone from existing)."""
    if from_app:
        from navig.commands.app import clone_app

        ctx.obj["source_name"] = from_app
        ctx.obj["new_name"] = app_name
        if host:
            ctx.obj["host"] = host
        clone_app(ctx.obj)
    else:
        from navig.commands.app import add_app

        ctx.obj["app_name"] = app_name
        if host:
            ctx.obj["host"] = host
        add_app(ctx.obj)


@app_app.command("remove")
def app_remove(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to remove"),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host to remove app from"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
):
    """Remove app from a host."""
    from navig.commands.app import remove_app

    ctx.obj["app_name"] = app_name
    ctx.obj["force"] = force
    if host:
        ctx.obj["host"] = host
    remove_app(ctx.obj)


@app_app.command("show")
def app_show(
    ctx: typer.Context,
    app_name: str | None = typer.Argument(None, help="App name to show"),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host containing the app"
    ),
    current: bool = typer.Option(False, "--current", help="Show currently active app"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed app configuration (canonical command)."""
    if json:
        ctx.obj["json"] = True
    if current:
        from navig.commands.app import current_app

        current_app(ctx.obj)
    else:
        from navig.commands.app import show_app

        if app_name:
            ctx.obj["app_name"] = app_name
        if host:
            ctx.obj["host"] = host
        show_app(ctx.obj)


@app_app.command("edit")
def app_edit(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to edit"),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host containing the app"
    ),
):
    """Edit app configuration in default editor."""
    from navig.commands.app import edit_app

    ctx.obj["app_name"] = app_name
    if host:
        ctx.obj["host"] = host
    edit_app(ctx.obj)


@app_app.command("clone", hidden=True)
def app_clone(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source app name to clone"),
    new_name: str = typer.Argument(..., help="New app name"),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host containing the app"
    ),
):
    """[DEPRECATED: Use 'navig app add <name> --from <source>'] Clone app."""
    deprecation_warning("navig app clone", "navig app add <name> --from <source>")
    from navig.commands.app import clone_app

    ctx.obj["source_name"] = source
    ctx.obj["new_name"] = new_name
    if host:
        ctx.obj["host"] = host
    clone_app(ctx.obj)


@app_app.command("info", hidden=True)
def app_info(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to show info for"),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host containing the app"
    ),
):
    """[DEPRECATED: Use 'navig app show'] Show detailed app information."""
    deprecation_warning("navig app info", "navig app show")
    from navig.commands.app import info_app

    ctx.obj["app_name"] = app_name
    if host:
        ctx.obj["host"] = host
    info_app(ctx.obj)


@app_app.command("search")
def app_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (app name)"),
):
    """Search for apps across all hosts by name or configuration."""
    from navig.commands.app import search_apps

    ctx.obj["query"] = query
    search_apps(ctx.obj)


@app_app.command("migrate")
def app_migrate(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host to migrate apps from"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be migrated without making changes"
    ),
):
    """
    Migrate apps from legacy embedded format to individual files.

    Converts apps stored in hosts/<host>.yaml under 'apps:' field
    to individual .navig/apps/<app>.yaml files.

    Examples:
        navig app migrate --host vultr          # Migrate all apps from vultr
        navig app migrate --dry-run             # Preview migration without changes
    """
    from navig.commands.app import migrate_apps

    if host:
        ctx.obj["host"] = host
    ctx.obj["dry_run"] = dry_run
    migrate_apps(ctx.obj)


# ============================================================================
# SSH TUNNEL COMMANDS
# ============================================================================
