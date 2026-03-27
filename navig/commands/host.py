"""
Host Management Commands

The Schema tracks all assets. Every host. Every operation.
"""

import os
import platform
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

from navig import console_helper as ch
from navig.config import get_config_manager

# Lazy import for ServerDiscovery - only loaded when discovery operations are needed
_server_discovery = None


def _get_server_discovery():
    """Lazy import ServerDiscovery to avoid loading paramiko on startup."""
    global _server_discovery
    if _server_discovery is None:
        from navig.discovery import ServerDiscovery

        _server_discovery = ServerDiscovery
    return _server_discovery


config_manager = get_config_manager()


def _is_ppk_format(key_path: str) -> bool:
    """
    Check if an SSH key file is in PuTTY PPK format.

    PPK files start with "PuTTY-User-Key-File" header.
    OpenSSH keys start with "-----BEGIN" or are binary.

    Args:
        key_path: Path to the SSH key file

    Returns:
        True if the file is in PPK format, False otherwise
    """
    try:
        expanded_path = Path(key_path).expanduser()
        if not expanded_path.exists():
            return False

        with open(expanded_path, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline().strip()
            return first_line.startswith("PuTTY-User-Key-File")
    except Exception:
        return False


def _validate_ssh_key(key_path: str) -> tuple[bool, str]:
    """
    Validate an SSH key file and return (valid, message).

    Checks:
    1. File exists
    2. Not in PPK format (needs conversion)
    3. Basic OpenSSH format validation

    Returns:
        Tuple of (is_valid, error_or_info_message)
    """
    expanded_path = Path(key_path).expanduser()

    # Check if file exists
    if not expanded_path.exists():
        return False, f"SSH key file not found: {key_path}"

    # Check for PPK format
    if _is_ppk_format(key_path):
        ppk_msg = (
            f"SSH key '{key_path}' is in PuTTY PPK format.\n"
            f"OpenSSH requires a different format.\n\n"
            f"To convert your key:\n"
            f"  Option 1: Use PuTTYgen GUI:\n"
            f"    1. Open PuTTYgen\n"
            f"    2. Load your .ppk file\n"
            f"    3. Conversions → Export OpenSSH key\n"
            f"    4. Save as: {key_path}_openssh\n\n"
            f"  Option 2: Use command line (if puttygen is installed):\n"
            f'    puttygen "{key_path}" -O private-openssh -o "{key_path}_openssh"\n\n'
            f"Then use the converted key path."
        )
        return False, ppk_msg

    # Check for OpenSSH format (starts with -----BEGIN)
    try:
        with open(expanded_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(100)
            if "-----BEGIN" in content:
                return True, "Valid OpenSSH key format"
            # Could be a binary key or other format - let SSH handle it
            return True, "Key file found (format will be validated by SSH)"
    except Exception as e:
        return False, f"Could not read key file: {e}"


def list_hosts(options: Dict[str, Any]):
    """List all configured hosts."""
    hosts = config_manager.list_hosts()

    if not hosts:
        ch.warning("No hosts configured", "Use 'navig host add <name>' to add one.")
        return

    # Fast path for plain/raw output — just names, no config loading needed
    if options.get("raw") or options.get("plain"):
        for host in hosts:
            ch.raw_print(host)
        return

    active_host = config_manager.get_active_host()
    default_host = config_manager.global_config.get("default_host")
    show_all = options.get("all", False)
    output_format = options.get("format", "table")

    if not hosts:
        ch.warning("No hosts configured", "Use 'navig host add <name>' to add one.")
        return

    # Collect host data
    host_data = []
    for host in hosts:
        try:
            config = config_manager.load_host_config(host)
            apps = config_manager.list_apps(host)

            status_icons = []
            if host == active_host:
                status_icons.append("✓ Active")
            if host == default_host:
                status_icons.append("★ Default")

            host_info = {
                "name": host,
                "host": config.get("host", "N/A"),
                "user": config.get("user", "N/A"),
                "port": config.get("port", 22),
                "apps_count": len(apps),
                "status": " | ".join(status_icons) if status_icons else "",
                "is_active": host == active_host,
                "is_default": host == default_host,
            }

            if show_all:
                host_info["apps"] = apps
                host_info["ssh_key"] = config.get("ssh_key", "N/A")
                if "metadata" in config:
                    host_info["os"] = config["metadata"].get("os", "N/A")

            host_data.append(host_info)
        except Exception as e:
            host_data.append(
                {
                    "name": host,
                    "host": "ERROR",
                    "user": "",
                    "port": 0,
                    "apps_count": 0,
                    "status": str(e),
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
                    "command": "host.list",
                    "success": True,
                    "hosts": host_data,
                    "count": len(host_data),
                    "active_host": active_host,
                    "default_host": default_host,
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif output_format == "json":
        import json

        print(json.dumps(host_data, indent=2))
    elif output_format == "yaml":
        import yaml

        print(yaml.dump(host_data, default_flow_style=False, sort_keys=False))
    else:  # table format
        # Create rich table with color-coded status
        if show_all:
            table = ch.create_table(
                title="Configured Hosts (Detailed)",
                columns=[
                    {"name": "Name", "style": "cyan"},
                    {"name": "Host", "style": "green"},
                    {"name": "User", "style": "blue"},
                    {"name": "Port", "style": "magenta"},
                    {"name": "Apps", "style": "yellow"},
                    {"name": "Status", "style": "yellow"},
                ],
            )

            for host_info in host_data:
                # Color-code active/default hosts
                name_style = (
                    "bold green"
                    if host_info["is_active"]
                    else ("bold yellow" if host_info["is_default"] else "cyan")
                )

                table.add_row(
                    f"[{name_style}]{host_info['name']}[/{name_style}]",
                    host_info["host"],
                    host_info["user"],
                    str(host_info["port"]),
                    str(host_info["apps_count"]),
                    host_info["status"],
                )
        else:
            table = ch.create_table(
                title="Configured Hosts",
                columns=[
                    {"name": "Name", "style": "cyan"},
                    {"name": "Host", "style": "green"},
                    {"name": "User", "style": "blue"},
                    {"name": "Status", "style": "yellow"},
                ],
            )

            for host_info in host_data:
                # Color-code active/default hosts
                name_style = (
                    "bold green"
                    if host_info["is_active"]
                    else ("bold yellow" if host_info["is_default"] else "cyan")
                )

                table.add_row(
                    f"[{name_style}]{host_info['name']}[/{name_style}]",
                    host_info["host"],
                    host_info["user"],
                    host_info["status"],
                )

        ch.print_table(table)


def use_host(name: str, options: Dict[str, Any]):
    """Switch active host context (global).

    Sets the global active host in ~/.navig/cache/active_host.txt.
    This affects all terminals that don't have a project-local config.

    Note: Project-local .navig/config.yaml takes precedence over this global setting.
    """
    if not config_manager.host_exists(name):
        ch.error(
            f"Host '{name}' not found", "Use 'navig host list' to see available hosts."
        )
        return

    config_manager.set_active_host(name)

    if not options.get("quiet"):
        ch.success(f"Switched to host: {name}")

        # Check if there's a local override that would take precedence
        local_navig_dir = Path.cwd() / ".navig"
        if local_navig_dir.exists() and local_navig_dir.is_dir():
            local_config = config_manager.get_local_config()
            local_host = local_config.get("active_host")
            if local_host and local_host != name:
                ch.dim(
                    "💡 Note: This directory has a local override (.navig/config.yaml)"
                )
                ch.dim(f"   Local active_host: {local_host} (takes precedence here)")


def show_current_host(options: Dict[str, Any]):
    """Show currently active host with source information."""
    active, source = config_manager.get_active_host(return_source=True)

    if options.get("raw"):
        ch.raw_print(active if active else "")
        return

    if not active:
        ch.warning("No active host", "Use 'navig host use <name>' to activate one.")
        return

    # Map source to display format
    source_display = {
        "env": "🔧 env (NAVIG_ACTIVE_HOST)",
        "local": "📍 local (.navig/config.yaml)",
        "legacy": "📄 legacy (.navig file)",
        "global": "🌐 global (~/.navig/cache/)",
        "default": "⚙️  default (from config)",
        "none": "none",
    }

    try:
        config = config_manager.load_host_config(active)
        ch.info(f"Source: {source_display.get(source, source)}")
        ch.print_server_info(active, config)
    except Exception as e:
        ch.error("Error loading host config", str(e))


def set_default_host(name: str, options: Dict[str, Any]):
    """Set default host."""
    if not config_manager.host_exists(name):
        ch.error(f"Host '{name}' not found")
        return

    config_manager.update_global_config({"default_host": name})

    if not options.get("quiet"):
        ch.success(f"Default host set to: {name}")


def add_host(name: str, options: Dict[str, Any]):
    """Add new host configuration (interactive wizard with auto-discovery)."""
    if config_manager.host_exists(name):
        ch.error(f"Host '{name}' already exists.")
        return

    ch.info(f"Adding new host: {name}")
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
        while True:
            ssh_key = ch.prompt_input("SSH Key Path", default="~/.ssh/id_rsa")

            # Validate the SSH key
            is_valid, message = _validate_ssh_key(ssh_key)
            if is_valid:
                break
            else:
                ch.error("Invalid SSH key:")
                # Print multi-line message properly
                for line in message.split("\n"):
                    ch.info(f"  {line}")
                ch.newline()
                if not ch.confirm_action("Try a different key path?", default=True):
                    ch.warning("Host setup cancelled.")
                    return
    else:
        ssh_password = ch.prompt_input("SSH Password", password=True)

    # Step 2: Test connection and run auto-discovery
    ch.header("Host Auto-Discovery")

    ssh_config = {
        "host": host,
        "port": port,
        "user": user,
        "ssh_key": ssh_key,
        "ssh_password": ssh_password,
    }

    # Get debug logger from options if available
    debug_logger = options.get("debug_logger") if options else None
    ServerDiscovery = _get_server_discovery()
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)

    # Test connection
    ch.step("Testing SSH connection...")
    if not discovery.test_connection():
        ch.error("✗ Connection failed")
        ch.error("Could not connect to host. Please check your credentials.")
        ch.info("")
        ch.info("Troubleshooting steps:")
        ch.info("  1. Verify SSH key permissions: chmod 600 ~/.ssh/id_rsa")
        ch.info("  2. Test manual connection: ssh user@host")
        ch.info("  3. Check firewall allows port 22 (or custom SSH port)")
        ch.info("  4. Verify host key in ~/.ssh/known_hosts")
        ch.info("  5. Check host SSH logs: tail -f /var/log/auth.log")

        if not ch.confirm_action("Continue without auto-discovery?", default=False):
            ch.warning("Host setup cancelled.")
            return
        discovered = {}
    else:
        ch.success("✓ Connection successful\n")

        # Run auto-discovery (skip web root - that's app-specific)
        if ch.confirm_action(
            "Run auto-discovery to detect host configuration?", default=True
        ):
            discovered = discovery.discover_all(progress=True, skip_web_root=True)
        else:
            discovered = {}

    # Step 3: Database configuration (with auto-detected root credentials)
    ch.header("Database Configuration")

    # Get discovered database info
    discovered_dbs = discovered.get("databases", [])
    if discovered_dbs:
        db_default = discovered_dbs[0]
        db_type = db_default["type"]
        db_port = db_default["port"]
        db_version = db_default.get("version", "Unknown")

        ch.success(f"Detected {db_type.upper()} {db_version} on port {db_port}")

        # Check if root credentials were auto-detected
        root_user = db_default.get("root_user", "root")
        root_password = db_default.get("root_password")

        if root_password and root_password != "<encrypted>":
            ch.success(f"Auto-detected root credentials for user '{root_user}'")
            ch.dim("Root credentials will be stored for server management tasks.")

            # Ask if user wants to use auto-detected credentials
            use_auto_creds = ch.confirm_action(
                "Use auto-detected root credentials?", default=True
            )
            if not use_auto_creds:
                root_user = ch.prompt_input("Database Root User", default=root_user)
                root_password = ch.prompt_input("Database Root Password", password=True)
        elif root_password == "<encrypted>":
            ch.warning("Root credentials are encrypted in mysql_config_editor")
            ch.dim("You can use mysql commands without password on the server.")
            root_user = db_default.get("root_user", "root")
            root_password = None  # Will use mysql_config_editor
        else:
            ch.warning("Could not auto-detect root credentials")
            if ch.confirm_action("Enter root credentials now?", default=True):
                root_user = ch.prompt_input("Database Root User", default="root")
                root_password = ch.prompt_input("Database Root Password", password=True)
            else:
                root_user = "root"
                root_password = None
                ch.warning("Root credentials can be added later by editing the config.")
    else:
        ch.dim("No database detected. Manual configuration required.")
        if ch.confirm_action("Configure database now?", default=True):
            db_type = ch.prompt_input("Database Type", default="mysql")
            db_port = int(ch.prompt_input("Remote Database Port", default="3306"))
            root_user = ch.prompt_input("Database Root User", default="root")
            root_password = ch.prompt_input("Database Root Password", password=True)
        else:
            db_type = "mysql"
            db_port = 3306
            root_user = "root"
            root_password = None
            ch.warning("Skipping database configuration. You can configure it later.")

    # Store root credentials at host level (for server management)
    database = {
        "type": db_type,
        "remote_port": db_port,
        "local_tunnel_port": 3307,
        "root_user": root_user,
        "root_password": root_password,
    }

    # Step 4: Server Paths (host-level only, no web root)
    ch.header("Server Paths")
    ch.dim("Note: Web root is app-specific and will be configured when adding apps.\n")

    paths = {
        "logs": "",
        "php_config": "",
        "nginx_config": "",
    }

    # Get log paths from discovery
    log_paths = discovered.get("log_paths", [])
    if log_paths:
        paths["logs"] = log_paths[0]  # Use first detected log path
        ch.success(f"Detected log directory: {paths['logs']}")

    # Get PHP config path from discovery
    php_info = discovered.get("php_info", {})
    if php_info.get("config_path"):
        paths["php_config"] = php_info["config_path"]
        ch.success(f"Detected PHP config: {paths['php_config']}")

    # Get Nginx config from discovery
    web_servers = discovered.get("web_servers", [])
    for ws in web_servers:
        if ws["type"] == "nginx" and "sites_path" in ws:
            paths["nginx_config"] = ws["sites_path"]
            ch.success(f"Detected Nginx config: {paths['nginx_config']}")

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

    # Create host config
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

    # Save host configuration using the correct method (saves to hosts/ directory)
    try:
        config_manager.save_host_config(name, config)
    except Exception as e:
        ch.error("Failed to save host configuration", str(e))
        return

    ch.success(f"✓ Host '{name}' configured successfully!\n")

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
    if paths.get("web_root"):
        ch.console.print(f"[green]Web Root: {paths['web_root']}[/green]")

    # Ask if they want to make it active
    ch.newline()
    if ch.confirm_action("Make this the active host?", default=True):
        config_manager.set_active_host(name)
        ch.success(f"✓ Active host set to: {name}")


def remove_host(name: str, options: Dict[str, Any]):
    """Remove host configuration."""
    quiet = options.get("quiet", False)

    if not config_manager.host_exists(name):
        if not quiet:
            ch.error(f"Host '{name}' not found.")
        return

    if not options.get("yes"):
        if not ch.confirm_action(f"Are you sure you want to remove host '{name}'?"):
            ch.warning("Cancelled.")
            return

    config_manager.delete_host_config(name)

    # If this was the active host, clear it
    if config_manager.get_active_host() == name:
        config_manager.active_host_file.unlink(missing_ok=True)

    if not quiet:
        ch.success(f"Host '{name}' removed.")


def inspect_host(options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Auto-discover host details and update configuration.

    Returns:
        Dict with discovery results if successful, None if failed:
        {
            'discovered': {...},  # Raw discovery data
            'detected_templates': {...},  # Detected templates
            'updates': {...}  # Metadata updates
        }
    """
    from navig.server_template_manager import ServerTemplateManager

    silent = options.get("silent", False)  # Don't print output if True

    active = config_manager.get_active_host()
    if not active:
        if not silent:
            ch.error("No active host. Use 'navig host use <name>' first.")
        return None

    if not silent:
        ch.info(f"Inspecting host: {active}\n")

    # Load existing config
    try:
        host_config = config_manager.load_host_config(active)
    except Exception as e:
        if not silent:
            ch.error(f"Error loading host config: {e}")
        return None

    # Run discovery
    ssh_config = {
        "host": host_config["host"],
        "port": host_config.get("port", 22),
        "user": host_config["user"],
        "ssh_key": host_config.get("ssh_key"),
        "ssh_password": host_config.get("ssh_password"),
    }

    # Get debug logger from options if available
    debug_logger = options.get("debug_logger") if options else None
    ServerDiscovery = _get_server_discovery()
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)

    # Test connection
    if not discovery.test_connection():
        if not silent:
            ch.error("✗ Could not connect to host")
        return None

    # Run full discovery (suppress progress output if silent)
    discovered = discovery.discover_all(progress=not silent)

    # Discover templates (suppress progress output if silent)
    detected_templates = discovery.discover_templates(progress=not silent)

    if not discovered:
        if not silent:
            ch.warning("No new information discovered")
        return None

    # Update host configuration with discovered info
    updates = {
        "metadata": {
            "os": discovered.get("os", host_config.get("metadata", {}).get("os", "")),
            "kernel": discovered.get("kernel", ""),
            "php_version": discovered.get("version", ""),  # PHP version is flat key
            "mysql_version": "",
        }
    }

    # Update database info
    discovered_dbs = discovered.get("databases", [])
    if discovered_dbs:
        db = discovered_dbs[0]
        if "database" in host_config:
            host_config["database"]["type"] = db["type"]
            host_config["database"]["remote_port"] = db["port"]
        updates["metadata"]["mysql_version"] = db.get("version", "")

    # Update PHP services and paths
    if discovered.get("fpm_service"):
        if "services" not in host_config:
            host_config["services"] = {}
        host_config["services"]["php"] = discovered["fpm_service"]
    if discovered.get("config_path"):
        if "paths" not in host_config:
            host_config["paths"] = {}
        host_config["paths"]["php_config"] = discovered["config_path"]

    # Update web server info
    web_servers = discovered.get("web_servers", [])
    if web_servers:
        ws = web_servers[0]
        if "services" not in host_config:
            host_config["services"] = {}
        host_config["services"]["web"] = ws["type"]

        if ws["type"] == "nginx" and "sites_path" in ws:
            if "paths" not in host_config:
                host_config["paths"] = {}
            host_config["paths"]["nginx_config"] = ws["sites_path"]

    # Update paths
    if discovered.get("web_root"):
        if "paths" not in host_config:
            host_config["paths"] = {}
        if not host_config["paths"].get("web_root"):
            host_config["paths"]["web_root"] = discovered["web_root"]

    log_paths = discovered.get("log_paths", [])
    if log_paths:
        if "paths" not in host_config:
            host_config["paths"] = {}
        if not host_config["paths"].get("logs"):
            host_config["paths"]["logs"] = log_paths[0]

    # Save updated configuration
    config_manager.update_host_metadata(active, updates["metadata"])

    # Initialize detected templates
    if detected_templates and not silent:
        ch.dim("")  # Spacing
        template_manager = ServerTemplateManager(config_manager)
        template_manager.initialize_templates_from_detection(active, detected_templates)

    if not silent:
        ch.success(f"✓ Host '{active}' configuration updated\n")
        ch.header("Updated Information")
        ch.info(f"OS: {updates['metadata']['os']}", style="green")
        if updates["metadata"]["php_version"]:
            ch.info(f"PHP: {updates['metadata']['php_version']}", style="green")
        if updates["metadata"]["mysql_version"]:
            ch.info(f"Database: {updates['metadata']['mysql_version']}", style="green")
        if detected_templates:
            ch.info(f"Templates: {', '.join(detected_templates.keys())}", style="cyan")

    # Return discovery results for silent mode (used by interactive menu)
    return {
        "discovered": discovered,
        "detected_templates": detected_templates,
        "updates": updates,
    }


def edit_host(options: Dict[str, Any]) -> None:
    """Open host configuration in default editor (YAML file)."""
    import os
    import subprocess

    host_name = options.get("host_name")

    if not host_name:
        ch.error("Host name is required")
        return

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    # Get host config file path
    host_file = config_manager.hosts_dir / f"{host_name}.yaml"

    if not host_file.exists():
        # Try legacy format
        host_file = config_manager.apps_dir / f"{host_name}.yaml"

        if not host_file.exists():
            ch.error(f"Configuration file not found for host '{host_name}'")
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

    ch.info(f"Opening {host_file} in {editor}...")

    try:
        # Open editor - use shlex.split for safe argument parsing
        if platform.system() == "Windows" and editor == "notepad":
            subprocess.run([editor, str(host_file)], check=True)
        else:
            # Safely split editor command and add file path
            editor_cmd = shlex.split(editor)
            editor_cmd.append(str(host_file))
            subprocess.run(editor_cmd, check=True)

        ch.success("✓ Configuration file closed")
        ch.dim("Changes will take effect immediately.")
    except subprocess.CalledProcessError as e:
        ch.error("Error opening editor", str(e))
    except Exception as e:
        ch.error("Unexpected error", str(e))


def clone_host(options: Dict[str, Any]) -> None:
    """Clone an existing host configuration."""
    source_name = options.get("source_name")
    new_name = options.get("new_name")

    if not source_name or not new_name:
        ch.error("Both source and new host names are required")
        return

    # Verify source exists
    if not config_manager.host_exists(source_name):
        ch.error(
            f"Source host '{source_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    # Verify new name doesn't exist
    if config_manager.host_exists(new_name):
        ch.error(f"Host '{new_name}' already exists")
        return

    # Load source configuration
    try:
        source_config = config_manager.load_host_config(source_name)
    except Exception as e:
        ch.error("Error loading source host configuration", str(e))
        return

    # Update name in cloned config
    source_config["name"] = new_name

    # Prompt for new host details
    ch.info(f"Cloning host '{source_name}' to '{new_name}'")
    ch.dim("Update the following fields (press Enter to keep current value):\n")

    # Prompt for host
    current_host = source_config.get("host", "")
    new_host = ch.prompt_input(f"Host [{current_host}]")
    if new_host:
        source_config["host"] = new_host

    # Prompt for user
    current_user = source_config.get("user", "")
    new_user = ch.prompt_input(f"User [{current_user}]")
    if new_user:
        source_config["user"] = new_user

    # Prompt for port
    current_port = source_config.get("port", 22)
    new_port_str = ch.prompt_input(f"Port [{current_port}]")
    if new_port_str:
        try:
            source_config["port"] = int(new_port_str)
        except ValueError:
            ch.warning(f"Invalid port number, keeping {current_port}")

    # Save cloned configuration
    try:
        config_manager.save_host_config(new_name, source_config)
        ch.success(f"✓ Host '{new_name}' created as clone of '{source_name}'\n")

        # Show next steps
        ch.header("Next Steps")
        ch.info(f"1. Edit configuration: navig host edit {new_name}")
        ch.info(f"2. Test connection: navig host test {new_name}")
        ch.info(f"3. Set as active: navig host use {new_name}")
    except Exception as e:
        ch.error("Error saving cloned host configuration", str(e))


def test_host(options: Dict[str, Any]) -> None:
    """Test SSH connection to host.

    Raises:
        RuntimeError: If SSH connection fails
    """
    import subprocess

    host_name = options.get("host_name")
    verbose = options.get("verbose", False)
    silent = options.get("silent", False)  # Don't print output if True

    if not host_name:
        # Use active host if not specified
        host_name = config_manager.get_active_host()

    if not host_name:
        ch.error("No host specified and no active host configured")
        raise RuntimeError("No host specified")

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        raise RuntimeError(f"Host '{host_name}' not found")

    # Load host configuration
    try:
        host_config = config_manager.load_host_config(host_name)
    except Exception as e:
        ch.error("Error loading host configuration", str(e))
        raise

    # Show connection details (unless silent mode)
    if not silent:
        ch.info(f"Testing SSH connection to '{host_name}'...")
        ch.dim(f"Host: {host_config.get('host')}")
        ch.dim(f"User: {host_config.get('user')}")
        ch.dim(f"Port: {host_config.get('port', 22)}")

    # Build SSH command — resolve full path on Windows to avoid FileNotFoundError
    # Note: 32-bit Python on 64-bit Windows has System32→SysWOW64 redirection,
    # so ssh.exe (64-bit) must be found via SysNative alias.
    import pathlib
    import shutil

    ssh_binary = shutil.which("ssh") or shutil.which("ssh.exe")
    if ssh_binary is None:
        _sysroot = os.environ.get("SystemRoot", "C:/Windows")
        for _candidate in [
            pathlib.Path(_sysroot)
            / "SysNative"
            / "OpenSSH"
            / "ssh.exe",  # 32-bit process on 64-bit OS
            pathlib.Path(_sysroot) / "System32" / "OpenSSH" / "ssh.exe",  # native path
            pathlib.Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "OpenSSH"
            / "ssh.exe",
            pathlib.Path(os.environ.get("ProgramFiles(x86)", ""))
            / "OpenSSH"
            / "ssh.exe",
        ]:
            if _candidate.exists():
                ssh_binary = str(_candidate)
                break
    if ssh_binary is None:
        ch.error("SSH client not found", "Please install OpenSSH client.")
        raise RuntimeError("SSH client not found")
    ssh_cmd = [
        ssh_binary,
        "-o",
        "ConnectTimeout=10",
        "-o",
        "BatchMode=yes",
        "-p",
        str(host_config.get("port", 22)),
    ]

    # Add verbose flag if requested
    if verbose:
        ssh_cmd.append("-v")

    # Add SSH key if specified
    ssh_key = host_config.get("ssh_key")
    if ssh_key:
        ssh_key_path = os.path.expanduser(ssh_key)
        if verbose and not silent:
            ch.dim(f"SSH Key: {ssh_key_path}")

        # Check if key file exists
        if not os.path.exists(ssh_key_path):
            ch.error("SSH key file not found", ssh_key_path)
            raise RuntimeError(f"SSH key file not found: {ssh_key_path}")

        ssh_cmd.extend(["-i", ssh_key_path])
    else:
        if verbose:
            ch.dim("No SSH key configured (will use default keys)")

    # Add user@host
    ssh_cmd.append(f"{host_config.get('user')}@{host_config.get('host')}")

    # Add test command
    ssh_cmd.append('echo "Connection successful"')

    if verbose:
        ch.dim(f"\nSSH Command: {' '.join(ssh_cmd)}\n")

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)

        if result.returncode == 0:
            if not silent:
                ch.success(f"SSH connection to '{host_name}' successful!")
                if result.stdout.strip():
                    ch.dim(f"Response: {result.stdout.strip()}")
        else:
            error_msg = result.stderr.strip() or "Unknown error"
            if not silent:
                ch.error("SSH connection failed", error_msg)

                # Provide helpful hints based on error message
                if "Permission denied" in error_msg:
                    ch.dim("\n💡 Troubleshooting tips:")
                    if ssh_key:
                        ch.dim("  • Verify the SSH key is authorized on the server")
                        ch.dim("  • Check ~/.ssh/authorized_keys on the server")
                        ch.dim(
                            f"  • Ensure key file has correct permissions: {ssh_key_path}"
                        )
                    else:
                        ch.dim(
                            "  • No SSH key configured - add one with 'navig host edit'"
                        )
                        ch.dim(
                            "  • Or ensure password authentication is enabled on server"
                        )
                elif "Connection refused" in error_msg:
                    ch.dim("\n💡 SSH service may not be running on the server")
                elif "No route to host" in error_msg:
                    ch.dim("\n💡 Check if the host IP address is correct")

            raise RuntimeError(f"SSH connection failed: {error_msg}")

    except subprocess.TimeoutExpired as _exc:
        ch.error(
            "Connection timeout", "Host may be unreachable or SSH service not running."
        )
        raise RuntimeError("Connection timeout") from _exc
    except FileNotFoundError as _exc:
        ch.error("SSH client not found", "Please install OpenSSH client.")
        raise RuntimeError("SSH client not found") from _exc
    except TypeError as e:
        ch.error("Configuration error", f"Invalid SSH configuration: {str(e)}")
        raise
    except RuntimeError:
        # Re-raise RuntimeError (already handled above)
        raise
    except Exception as e:
        ch.error("Connection test failed", str(e))
        raise RuntimeError(f"Connection test failed: {str(e)}") from e


def info_host(options: Dict[str, Any]) -> None:
    """Show detailed host information (IP, port, user, apps count, etc.)."""
    from rich.console import Console

    host_name = options.get("host_name")
    json_output = options.get("json", False)

    if not host_name:
        # Use active host if not specified
        host_name = config_manager.get_active_host()

    if not host_name:
        ch.error("No host specified and no active host configured")
        return

    # Verify host exists
    if not config_manager.host_exists(host_name):
        ch.error(
            f"Host '{host_name}' not found",
            "Use 'navig host list' to see available hosts.",
        )
        return

    # Load host configuration
    try:
        host_config = config_manager.load_host_config(host_name)
    except Exception as e:
        ch.error("Error loading host configuration", str(e))
        return

    # Get additional info
    active_host = config_manager.get_active_host()
    default_host = config_manager.global_config.get("default_host")
    apps = config_manager.list_apps(host_name)

    # Build info dictionary
    info = {
        "name": host_name,
        "host": host_config.get("host"),
        "port": host_config.get("port", 22),
        "user": host_config.get("user"),
        "ssh_key": host_config.get("ssh_key"),
        "default_app": host_config.get("default_app"),
        "apps_count": len(apps),
        "apps": apps,
        "is_active": host_name == active_host,
        "is_default": host_name == default_host,
    }

    # Add metadata if available
    if "metadata" in host_config:
        info["metadata"] = host_config["metadata"]

    # Output format
    if json_output:
        import json

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "host.show",
                    "success": True,
                    "host": info,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        console = Console()

        ch.header(f"Host Information: {host_name}")
        ch.newline()

        # Connection details
        ch.subheader("Connection Details")
        ch.info(f"  Host:     {info['host']}")
        ch.info(f"  Port:     {info['port']}")
        ch.info(f"  User:     {info['user']}")
        if info["ssh_key"]:
            ch.info(f"  SSH Key:  {info['ssh_key']}")
        ch.newline()

        # Status
        ch.subheader("Status")
        status_items = []
        if info["is_active"]:
            status_items.append("✓ Active")
        if info["is_default"]:
            status_items.append("★ Default")
        if status_items:
            ch.info(f"  {', '.join(status_items)}")
        else:
            ch.dim("  Not active or default")
        ch.newline()

        # Apps
        ch.subheader(f"Apps ({info['apps_count']})")
        if apps:
            for app in apps:
                is_default = app == info["default_app"]
                marker = "★ " if is_default else "  "
                ch.info(f"{marker}{app}")
        else:
            ch.dim("  No apps configured")
        ch.newline()

        # Metadata
        if "metadata" in info:
            ch.subheader("System Information")
            metadata = info["metadata"]
            if "os" in metadata:
                ch.info(f"  OS:       {metadata['os']}")
            if "php_version" in metadata:
                ch.info(f"  PHP:      {metadata['php_version']}")
            if "mysql_version" in metadata:
                ch.info(f"  Database: {metadata['mysql_version']}")
            ch.newline()

        # Configuration file location
        host_file = config_manager.hosts_dir / f"{host_name}.yaml"
        if not host_file.exists():
            host_file = config_manager.apps_dir / f"{host_name}.yaml"

        ch.dim(f"Configuration: {host_file}")


from typing import Any, Dict, Optional

import typer

from navig.cli import deprecation_warning, show_subcommand_help

# ============================================================================
# HOST MANAGEMENT COMMANDS
# ============================================================================

host_app = typer.Typer(
    help="Manage remote hosts",
    invoke_without_command=True,
    no_args_is_help=False,
)


@host_app.callback()
def host_callback(ctx: typer.Context):
    """Host management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("host", ctx)
        raise typer.Exit()


@host_app.command("list")
def host_list(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", "-a", help="Show detailed information"),
    format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json, yaml"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one host per line) for scripting"
    ),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all configured hosts."""
    from navig.commands.host import list_hosts

    ctx.obj["all"] = all
    ctx.obj["format"] = "json" if json else format
    ctx.obj["plain"] = plain
    if json:
        ctx.obj["json"] = True
    list_hosts(ctx.obj)


@host_app.command("use")
def host_use(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name to activate"),
    default: bool = typer.Option(
        False, "--default", "-d", help="Also set as default host"
    ),
):
    """Switch active host context (global)."""
    from navig.commands.host import set_default_host, use_host

    use_host(name, ctx.obj)
    if default:
        set_default_host(name, ctx.obj)


@host_app.command("current", hidden=True)
def host_current(ctx: typer.Context):
    """[DEPRECATED: Use 'navig host show --current'] Show currently active host."""
    deprecation_warning("navig host current", "navig host show --current")
    from navig.commands.host import show_current_host

    show_current_host(ctx.obj)


@host_app.command("default", hidden=True)
def host_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name to set as default"),
):
    """[DEPRECATED: Use 'navig host use --default'] Set default host."""
    deprecation_warning("navig host default", "navig host use <name> --default")
    from navig.commands.host import set_default_host

    set_default_host(name, ctx.obj)


@host_app.command("add")
def host_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name"),
    from_host: Optional[str] = typer.Option(
        None, "--from", help="Clone from existing host"
    ),
):
    """Add new host configuration (interactive wizard or clone)."""
    if from_host:
        from navig.commands.host import clone_host

        ctx.obj["source_name"] = from_host
        ctx.obj["new_name"] = name
        clone_host(ctx.obj)
    else:
        from navig.commands.host import add_host

        add_host(name, ctx.obj)


@host_app.command("clone", hidden=True)
def host_clone(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source host name to clone"),
    new_name: str = typer.Argument(..., help="New host name"),
):
    """[DEPRECATED: Use 'navig host add <name> --from <source>'] Clone host."""
    deprecation_warning("navig host clone", "navig host add <name> --from <source>")
    from navig.commands.host import clone_host

    ctx.obj["source_name"] = source
    ctx.obj["new_name"] = new_name
    clone_host(ctx.obj)


@host_app.command("discover-local")
def host_discover_local(
    ctx: typer.Context,
    name: str = typer.Option(
        "localhost", "--name", "-n", help="Name for the local host configuration"
    ),
    auto_confirm: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompts"
    ),
    no_active: bool = typer.Option(
        False, "--no-active", help="Don't set as active host"
    ),
):
    """
    Discover and configure local development environment.

    Automatically detects OS, databases, web servers, PHP, Node.js,
    Docker, and other tools installed on your local machine.

    Creates a 'localhost' host configuration that can be used for
    local development without SSH.

    Examples:
        navig host discover-local
        navig host discover-local --name my-dev
        navig host discover-local --yes --no-active
    """
    from navig.commands.local_discovery import discover_local_host

    discover_local_host(
        name=name,
        auto_confirm=auto_confirm or ctx.obj.get("yes", False),
        set_active=not no_active,
        progress=True,
        no_cache=bool(ctx.obj.get("no_cache")),
    )


@host_app.command("inspect", hidden=True)
def host_inspect(ctx: typer.Context):
    """[DEPRECATED: Use 'navig host show --inspect'] Auto-discover host details."""
    deprecation_warning("navig host inspect", "navig host show --inspect")
    from navig.commands.host import inspect_host

    inspect_host(ctx.obj)


@host_app.command("test")
def host_test(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None, help="Host name to test (uses active host if not specified)"
    ),
):
    """Test SSH connection to host."""
    from navig.commands.host import test_host

    if name:
        ctx.obj["host_name"] = name
    test_host(ctx.obj)


@host_app.command("show")
def host_show(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None, help="Host name (uses active if omitted)"
    ),
    current: bool = typer.Option(False, "--current", help="Show currently active host"),
    inspect: bool = typer.Option(False, "--inspect", help="Auto-discover host details"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show host information (canonical command)."""
    if json:
        ctx.obj["json"] = True
    if current:
        from navig.commands.host import show_current_host

        show_current_host(ctx.obj)
    elif inspect:
        from navig.commands.host import inspect_host

        inspect_host(ctx.obj)
    else:
        from navig.commands.host import info_host

        if name:
            ctx.obj["host_name"] = name
        info_host(ctx.obj)


@host_app.command("info", hidden=True)
def host_info(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None, help="Host name to show info for (uses active host if not specified)"
    ),
):
    """[DEPRECATED: Use 'navig host show'] Show detailed host information."""
    deprecation_warning("navig host info", "navig host show")
    from navig.commands.host import info_host

    if name:
        ctx.obj["host_name"] = name
    info_host(ctx.obj)


# ============================================================================
# HOST NESTED SUBCOMMANDS (Pillar 1: Infrastructure)
# ============================================================================

# Create nested sub-apps for host
host_monitor_app = typer.Typer(
    help="Server monitoring (resources, disk, services, network, health)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_monitor_app, name="monitor")


@host_monitor_app.callback()
def host_monitor_callback(ctx: typer.Context):
    """Host monitoring - run without subcommand for health overview."""
    if ctx.invoked_subcommand is None:
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)
        raise typer.Exit()


@host_monitor_app.command("show")
def host_monitor_show(
    ctx: typer.Context,
    resources: bool = typer.Option(
        False, "--resources", "-r", help="Show resource usage"
    ),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk space"),
    services: bool = typer.Option(
        False, "--services", "-s", help="Show service status"
    ),
    network: bool = typer.Option(False, "--network", "-n", help="Show network stats"),
    threshold: int = typer.Option(
        80, "--threshold", "-t", help="Alert threshold percentage"
    ),
):
    """Show monitoring information."""
    if resources:
        from navig.commands.monitoring import monitor_resources

        monitor_resources(ctx.obj)
    elif disk:
        from navig.commands.monitoring import monitor_disk

        monitor_disk(threshold, ctx.obj)
    elif services:
        from navig.commands.monitoring import monitor_services

        monitor_services(ctx.obj)
    elif network:
        from navig.commands.monitoring import monitor_network

        monitor_network(ctx.obj)
    else:
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)


@host_monitor_app.command("report")
def host_monitor_report(ctx: typer.Context):
    """Generate comprehensive monitoring report."""
    from navig.commands.monitoring import generate_report

    generate_report(ctx.obj)


# Host security subcommand
host_security_app = typer.Typer(
    help="Security management (firewall, fail2ban, SSH, updates)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_security_app, name="security")


@host_security_app.callback()
def host_security_callback(ctx: typer.Context):
    """Host security - run without subcommand for security scan."""
    if ctx.invoked_subcommand is None:
        from navig.commands.security import security_scan

        security_scan(ctx.obj)
        raise typer.Exit()


@host_security_app.command("show")
def host_security_show(
    ctx: typer.Context,
    firewall: bool = typer.Option(
        False, "--firewall", "-f", help="Show firewall status"
    ),
    fail2ban: bool = typer.Option(
        False, "--fail2ban", "-b", help="Show fail2ban status"
    ),
    ssh: bool = typer.Option(False, "--ssh", "-s", help="Show SSH audit"),
    updates: bool = typer.Option(
        False, "--updates", "-u", help="Show security updates"
    ),
    connections: bool = typer.Option(
        False, "--connections", "-c", help="Show network connections"
    ),
):
    """Show security information."""
    if firewall:
        from navig.commands.security import firewall_status

        firewall_status(ctx.obj)
    elif fail2ban:
        from navig.commands.security import fail2ban_status

        fail2ban_status(ctx.obj)
    elif ssh:
        from navig.commands.security import ssh_audit

        ssh_audit(ctx.obj)
    elif updates:
        from navig.commands.security import check_security_updates

        check_security_updates(ctx.obj)
    elif connections:
        from navig.commands.security import audit_connections

        audit_connections(ctx.obj)
    else:
        from navig.commands.security import security_scan

        security_scan(ctx.obj)


@host_security_app.command("edit")
def host_security_edit(
    ctx: typer.Context,
    firewall: bool = typer.Option(
        False, "--firewall", "-f", help="Edit firewall rules"
    ),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet"),
    add: bool = typer.Option(False, "--add", help="Add a rule"),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove a rule"),
    enable: bool = typer.Option(False, "--enable", help="Enable firewall"),
    disable: bool = typer.Option(False, "--disable", help="Disable firewall"),
    unban: Optional[str] = typer.Option(
        None, "--unban", help="Unban IP address from fail2ban"
    ),
    jail: Optional[str] = typer.Option(
        None, "--jail", "-j", help="Jail name for fail2ban"
    ),
):
    """Edit security settings."""
    if firewall:
        if enable:
            from navig.commands.security import firewall_enable

            firewall_enable(ctx.obj)
        elif disable:
            from navig.commands.security import firewall_disable

            firewall_disable(ctx.obj)
        elif add and port:
            from navig.commands.security import firewall_add_rule

            firewall_add_rule(port, protocol, allow_from, ctx.obj)
        elif remove and port:
            from navig.commands.security import firewall_remove_rule

            firewall_remove_rule(port, protocol, ctx.obj)
    elif unban:
        from navig.commands.security import fail2ban_unban

        fail2ban_unban(unban, jail, ctx.obj)
    else:
        ch.error("Specify what to edit: --firewall or --unban")


# Host maintenance subcommand
host_maintenance_app = typer.Typer(
    help="System maintenance (updates, cleaning, log rotation)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_maintenance_app, name="maintenance")


@host_maintenance_app.callback()
def host_maintenance_callback(ctx: typer.Context):
    """Host maintenance - run without subcommand for system info."""
    if ctx.invoked_subcommand is None:
        from navig.commands.maintenance import system_info

        system_info(ctx.obj)
        raise typer.Exit()


@host_maintenance_app.command("show")
def host_maintenance_show(
    ctx: typer.Context,
    info: bool = typer.Option(False, "--info", "-i", help="Show system information"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk usage"),
    memory: bool = typer.Option(False, "--memory", "-m", help="Show memory usage"),
):
    """Show system maintenance information."""
    if disk:
        from navig.commands.monitoring import monitor_disk

        monitor_disk(80, ctx.obj)
    elif memory:
        from navig.commands.monitoring import monitor_resources

        monitor_resources(ctx.obj)
    else:
        from navig.commands.maintenance import system_info

        system_info(ctx.obj)


@host_maintenance_app.command("run")
def host_maintenance_run(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", "-u", help="Update system packages"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Clean package cache"),
    rotate_logs: bool = typer.Option(
        False, "--rotate-logs", "-r", help="Rotate log files"
    ),
    cleanup_temp: bool = typer.Option(
        False, "--cleanup-temp", "-t", help="Clean temp files"
    ),
    all: bool = typer.Option(False, "--all", "-a", help="Full maintenance"),
    reboot: bool = typer.Option(False, "--reboot", help="Reboot server"),
):
    """Run system maintenance operations."""
    if update:
        from navig.commands.maintenance import update_packages

        update_packages(ctx.obj)
    elif clean:
        from navig.commands.maintenance import clean_packages

        clean_packages(ctx.obj)
    elif rotate_logs:
        from navig.commands.maintenance import rotate_logs as rotate_logs_func

        rotate_logs_func(ctx.obj)
    elif cleanup_temp:
        from navig.commands.maintenance import cleanup_temp as cleanup_temp_func

        cleanup_temp_func(ctx.obj)
    elif all:
        from navig.commands.maintenance import system_maintenance

        system_maintenance(ctx.obj)
    elif reboot:
        from navig.commands.remote import run_remote_command

        if ctx.obj.get("yes") or typer.confirm(
            "Are you sure you want to reboot the server?"
        ):
            run_remote_command("sudo reboot", ctx.obj)
    else:
        ch.error(
            "Specify an action: --update, --clean, --rotate-logs, --cleanup-temp, --all, --reboot"
        )


@host_maintenance_app.command("update")
def host_maintenance_update(ctx: typer.Context):
    """Update system packages."""
    from navig.commands.maintenance import update_packages

    update_packages(ctx.obj)


@host_maintenance_app.command("clean")
def host_maintenance_clean(ctx: typer.Context):
    """Clean package cache and orphans."""
    from navig.commands.maintenance import clean_packages

    clean_packages(ctx.obj)


@host_maintenance_app.command("install")
def host_maintenance_install(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Package or command to install"),
):
    """Install a package on the remote host."""
    from navig.commands.remote import install_remote_package

    install_remote_package(package, ctx.obj)
