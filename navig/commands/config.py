"""Configuration management commands for NAVIG."""

import json
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.migration import migrate_all_configs
from navig.yaml_utils import load_yaml_with_lines


def _package_schema_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_config_roots(scope: str | None) -> list[tuple[str, Path]]:
    """Return config roots to validate.

    Defaults to project scope when a .navig/ exists in the current directory,
    otherwise defaults to global.
    """
    global_root = Path.home() / ".navig"
    project_root = Path.cwd() / ".navig"

    normalized = (scope or "").strip().lower() if scope else None

    if normalized in {"project", ".navig"}:
        return [("project", project_root)] if project_root.exists() else []
    if normalized in {"global", "home", "~/.navig"}:
        return [("global", global_root)] if global_root.exists() else []
    if normalized in {"both", "all"}:
        roots: list[tuple[str, Path]] = []
        if global_root.exists():
            roots.append(("global", global_root))
        if project_root.exists():
            roots.append(("project", project_root))
        return roots

    # Default
    if project_root.exists():
        return [("project", project_root)]
    if global_root.exists():
        return [("global", global_root)]
    return []


def _file_stem(path: Path) -> str:
    return path.name.rsplit(".", 1)[0]


def _line_for(doc, path_items: tuple[Any, ...]) -> int:
    # Prefer exact path, otherwise fall back to parent paths.
    p = tuple(path_items)
    if p in doc.line_map:
        return doc.line_map[p]

    # Walk back up.
    while p:
        p = p[:-1]
        if p in doc.line_map:
            return doc.line_map[p]
    return 1


def _validate_host_data(
    host_file: Path, data: dict[str, Any], doc, strict: bool
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []

    def issue(severity: str, message: str, key_path: tuple[Any, ...] = ()):
        errors.append(
            {
                "severity": severity,
                "file": str(host_file),
                "line": _line_for(doc, key_path),
                "path": "/".join(str(p) for p in key_path) if key_path else "",
                "message": message,
            }
        )

    def err(message: str, key_path: tuple[Any, ...] = ()):
        issue("error", message, key_path)

    def warn(message: str, key_path: tuple[Any, ...] = ()):
        issue("warning", message, key_path)

    if not isinstance(data, dict):
        err("Host config must be a YAML mapping/object at the root")
        return errors

    for required in ("name", "host", "user"):
        if not data.get(required):
            err(f"Missing required field: {required}", (required,))

    filename = _file_stem(host_file)
    name = data.get("name")
    if isinstance(name, str) and name and name != filename:
        # Common in the wild; keep as warning unless strict.
        (err if strict else warn)(
            f"Host name '{name}' should match filename '{filename}.yaml'", ("name",)
        )

    port = data.get("port")
    if port is not None:
        if isinstance(port, str) and port.isdigit():
            # Allow common YAML quoting.
            port = int(port)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            err("port must be an integer between 1 and 65535", ("port",))

    ssh_key = data.get("ssh_key")
    ssh_password = data.get("ssh_password")
    if not ssh_key and not ssh_password:
        # Some test/sample configs omit creds; treat as warning unless strict.
        (err if strict else warn)(
            "Provide either ssh_key or ssh_password for SSH authentication",
            (),
        )

    return errors


def _validate_app_data(
    app_file: Path, data: dict[str, Any], doc, known_hosts: set[str], strict: bool
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []

    def issue(severity: str, message: str, key_path: tuple[Any, ...] = ()):
        errors.append(
            {
                "severity": severity,
                "file": str(app_file),
                "line": _line_for(doc, key_path),
                "path": "/".join(str(p) for p in key_path) if key_path else "",
                "message": message,
            }
        )

    def err(message: str, key_path: tuple[Any, ...] = ()):
        issue("error", message, key_path)

    def warn(message: str, key_path: tuple[Any, ...] = ()):
        issue("warning", message, key_path)

    if not isinstance(data, dict):
        err("App config must be a YAML mapping/object at the root")
        return errors

    for required in ("name", "host"):
        if not data.get(required):
            err(f"Missing required field: {required}", (required,))

    filename = _file_stem(app_file)
    name = data.get("name")
    if isinstance(name, str) and name and name != filename:
        err(f"App name '{name}' must match filename '{filename}.yaml'", ("name",))

    host = data.get("host")
    if isinstance(host, str) and host and known_hosts and host not in known_hosts:
        (err if strict else warn)(
            f"App references unknown host '{host}'. Add it under hosts/ or fix the app's host.",
            ("host",),
        )

    return errors


def migrate(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be migrated without making changes"
    ),
    no_backup: bool = typer.Option(
        False, "--no-backup", help="Skip creating backups before migration"
    ),
):
    """
    Migrate legacy configurations to new format.

    Converts old format (~/.navig/apps/*.yaml) to new format (~/.navig/hosts/*.yaml).

    Migration process:
    1. Detects old format configurations
    2. Extracts webserver type from services.web field
    3. Converts to new two-tier hierarchy (host → app)
    4. Creates backups (unless --no-backup specified)
    5. Saves new format configurations

    Examples:
        # Preview migration
        navig config migrate --dry-run

        # Perform migration with backups
        navig config migrate

        # Perform migration without backups
        navig config migrate --no-backup
    """
    console = Console()

    # Get configuration directories
    config_manager = get_config_manager()
    old_dir = Path.home() / ".navig" / "apps"
    new_dir = Path.home() / ".navig" / "hosts"

    if not old_dir.exists():
        ch.error(f"Old configuration directory not found: {old_dir}")
        ch.info("No legacy configurations to migrate.")
        raise typer.Exit(0)

    # Perform migration
    ch.info(f"{'[DRY RUN] ' if dry_run else ''}Migrating configurations...")
    ch.info(f"  Old directory: {old_dir}")
    ch.info(f"  New directory: {new_dir}")

    try:
        results = migrate_all_configs(
            old_dir=old_dir, new_dir=new_dir, dry_run=dry_run, backup=not no_backup
        )
    except Exception as e:
        ch.error(f"Migration failed: {str(e)}")
        raise typer.Exit(1) from e

    # Display results
    console.print()

    if results["migrated"]:
        table = Table(title="✅ Migrated Configurations", show_header=True)
        table.add_column("Old File", style="cyan")
        table.add_column("New File", style="green")
        table.add_column("Status", style="yellow")

        for item in results["migrated"]:
            status = "DRY RUN" if item.get("dry_run") else "MIGRATED"
            table.add_row(
                Path(item["old_file"]).name, Path(item["new_file"]).name, status
            )

        console.print(table)
        console.print()

    if results["skipped"]:
        table = Table(title="⏭️  Skipped Configurations", show_header=True)
        table.add_column("File", style="yellow")
        table.add_column("Reason", style="dim")

        for item in results["skipped"]:
            table.add_row(Path(item["file"]).name, item["reason"])

        console.print(table)
        console.print()

    if results["failed"]:
        table = Table(title="❌ Failed Migrations", show_header=True)
        table.add_column("File", style="red")
        table.add_column("Error", style="dim")

        for item in results["failed"]:
            table.add_row(Path(item["file"]).name, item["error"])

        console.print(table)
        console.print()

    if results["backups"] and not dry_run:
        ch.success(f"Created {len(results['backups'])} backup(s)")
        for backup in results["backups"]:
            ch.info(f"  Backup: {backup}")
        console.print()

    # Summary
    if dry_run:
        ch.warning("DRY RUN - No changes were made")
        ch.info(f"Would migrate {len(results['migrated'])} configuration(s)")
    else:
        if results["migrated"]:
            ch.success(
                f"Successfully migrated {len(results['migrated'])} configuration(s)"
            )
            ch.info("Old configurations are still available in ~/.navig/apps/")
            ch.info("Backups created with .backup.<timestamp>.yaml extension")
        else:
            ch.info("No configurations were migrated")

    if results["failed"]:
        ch.error(f"Failed to migrate {len(results['failed'])} configuration(s)")
        raise typer.Exit(1)


def validate(
    host: str | None = typer.Argument(
        None, help="Host name to validate (validates all if not specified)"
    ),
    options: dict[str, Any] | None = None,
):
    """
    Validate configuration files.

    Checks:
    - YAML syntax
    - Required fields present
    - Webserver type specified
    - Valid field values

    Examples:
        # Validate all configurations
        navig config validate

        # Validate specific host
        navig config validate myhost
    """
    options = options or {}
    json_out = bool(options.get("json"))
    strict = bool(options.get("strict"))
    scope = options.get("scope")

    roots = _default_config_roots(scope)
    if not roots:
        ch.error("No NAVIG configuration found")
        ch.dim("  Expected one of:")
        ch.dim("  • Global: ~/.navig")
        ch.dim("  • Project: .navig (in the current directory)")
        ch.dim("")
        ch.dim("  Fix:")
        ch.dim("    navig init")
        raise typer.Exit(1)

    issues: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for scope, root in roots:
        hosts_dir = root / "hosts"
        apps_dir = root / "apps"
        config_file = root / "config.yaml"

        scope_result: dict[str, Any] = {
            "scope": scope,
            "root": str(root),
            "hosts_checked": 0,
            "apps_checked": 0,
            "errors": 0,
        }

        known_hosts: set[str] = set()

        if hosts_dir.exists():
            for host_path in sorted(hosts_dir.glob("*.y*ml")):
                if host and _file_stem(host_path) != host:
                    continue
                try:
                    doc = load_yaml_with_lines(host_path)
                except Exception as e:
                    issues.append(
                        {
                            "severity": "error",
                            "file": str(host_path),
                            "line": 1,
                            "path": "",
                            "message": f"YAML parse error: {e}",
                        }
                    )
                    continue

                data = doc.data
                if isinstance(data, dict) and isinstance(data.get("name"), str):
                    known_hosts.add(data["name"])
                known_hosts.add(_file_stem(host_path))

        if hosts_dir.exists():
            for host_path in sorted(hosts_dir.glob("*.y*ml")):
                if host and _file_stem(host_path) != host:
                    continue
                scope_result["hosts_checked"] += 1
                try:
                    doc = load_yaml_with_lines(host_path)
                except Exception as e:
                    issues.append(
                        {
                            "severity": "error",
                            "file": str(host_path),
                            "line": 1,
                            "path": "",
                            "message": f"YAML parse error: {e}",
                        }
                    )
                    continue
                if isinstance(doc.data, dict):
                    issues.extend(
                        _validate_host_data(host_path, doc.data, doc, strict=strict)
                    )
                else:
                    issues.extend(
                        _validate_host_data(host_path, {}, doc, strict=strict)
                    )

        if apps_dir.exists():
            for app_path in sorted(apps_dir.glob("*.y*ml")):
                scope_result["apps_checked"] += 1
                try:
                    doc = load_yaml_with_lines(app_path)
                except Exception as e:
                    issues.append(
                        {
                            "severity": "error",
                            "file": str(app_path),
                            "line": 1,
                            "path": "",
                            "message": f"YAML parse error: {e}",
                        }
                    )
                    continue
                if isinstance(doc.data, dict):
                    issues.extend(
                        _validate_app_data(
                            app_path, doc.data, doc, known_hosts, strict=strict
                        )
                    )
                else:
                    issues.extend(
                        _validate_app_data(
                            app_path, {}, doc, known_hosts, strict=strict
                        )
                    )

        if config_file.exists():
            try:
                load_yaml_with_lines(config_file)
            except Exception as e:
                issues.append(
                    {
                        "severity": "error",
                        "file": str(config_file),
                        "line": 1,
                        "path": "",
                        "message": f"YAML parse error: {e}",
                    }
                )

        scope_result["issues"] = sum(
            1 for e in issues if e.get("file", "").startswith(str(root))
        )
        results.append(scope_result)

    if json_out:
        payload = {
            "ok": all(i.get("severity") != "error" for i in issues),
            "scope": scope
            or ("project" if any(r[0] == "project" for r in roots) else "global"),
            "strict": strict,
            "results": results,
            "issues": issues,
        }
        ch.raw_print(json.dumps(payload, indent=2))
        raise typer.Exit(0 if payload["ok"] else 1)

    errors_only = [i for i in issues if i.get("severity") == "error"]
    warnings_only = [i for i in issues if i.get("severity") == "warning"]

    if errors_only:
        ch.error("Configuration validation failed")
        ch.dim("")
        for e in errors_only[:60]:
            file = e.get("file")
            line = e.get("line")
            msg = e.get("message")
            ch.dim(f"  {file}:{line}  {msg}")
        if len(errors_only) > 60:
            ch.dim(f"  ... {len(errors_only) - 60} more")
        ch.dim("")
        ch.dim("Fix:")
        ch.dim("  - Edit the YAML files listed above")
        ch.dim("  - Then re-run: navig config validate")
        ch.dim("")
        ch.dim("Tip:")
        ch.dim(
            "  - Validate only the project config: navig config validate --scope project"
        )
        ch.dim("  - Validate both:                 navig config validate --scope both")
        raise typer.Exit(1)

    if warnings_only:
        ch.warning("Configuration validation warnings")
        ch.dim("")
        for w in warnings_only[:60]:
            file = w.get("file")
            line = w.get("line")
            msg = w.get("message")
            ch.dim(f"  {file}:{line}  {msg}")
        if len(warnings_only) > 60:
            ch.dim(f"  ... {len(warnings_only) - 60} more")
        ch.dim("")
        ch.dim("Tip:")
        ch.dim("  - Treat warnings as errors: navig config validate --strict")

    ch.success("Configuration validation OK")
    for r in results:
        ch.dim(
            f"  {r['scope']}: {r['hosts_checked']} host(s), {r['apps_checked']} app(s) checked ({r['root']})"
        )
    raise typer.Exit(0)


def install_schemas(
    scope: str,
    write_vscode_settings: bool,
    options: dict[str, Any] | None = None,
):
    """Install JSON Schemas for YAML config files (VS Code YAML extension)."""
    options = options or {}
    json_out = bool(options.get("json"))

    scope = (scope or "").strip().lower()
    if scope not in {"global", "project"}:
        ch.error("Invalid --scope")
        ch.dim("  Use: --scope global   (installs under ~/.navig)")
        ch.dim("  Or:  --scope project  (installs under .navig in current dir)")
        raise typer.Exit(1)

    target_root = (
        (Path.home() / ".navig") if scope == "global" else (Path.cwd() / ".navig")
    )
    target_dir = target_root / "schemas"
    target_dir.mkdir(parents=True, exist_ok=True)

    src_dir = _package_schema_dir()
    host_src = src_dir / "host.schema.json"
    app_src = src_dir / "app.schema.json"

    host_dst = target_dir / "navig-host.schema.json"
    app_dst = target_dir / "navig-app.schema.json"
    host_dst.write_text(host_src.read_text(encoding="utf-8"), encoding="utf-8")
    app_dst.write_text(app_src.read_text(encoding="utf-8"), encoding="utf-8")

    vscode_settings_written = False
    settings_path = Path.cwd() / ".vscode" / "settings.json"
    if write_vscode_settings:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings: dict[str, Any] = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                settings = {}

        yaml_schemas = settings.get("yaml.schemas")
        if not isinstance(yaml_schemas, dict):
            yaml_schemas = {}

        # VS Code expects file paths or URLs as keys.
        yaml_schemas[str(host_dst)] = [
            "**/.navig/hosts/*.yml",
            "**/.navig/hosts/*.yaml",
            "**/hosts/*.yml",
            "**/hosts/*.yaml",
        ]
        yaml_schemas[str(app_dst)] = [
            "**/.navig/apps/*.yml",
            "**/.navig/apps/*.yaml",
            "**/apps/*.yml",
            "**/apps/*.yaml",
        ]
        settings["yaml.schemas"] = yaml_schemas
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        vscode_settings_written = True

    if json_out:
        ch.raw_print(
            json.dumps(
                {
                    "ok": True,
                    "scope": scope,
                    "installed": [str(host_dst), str(app_dst)],
                    "vscode_settings_written": vscode_settings_written,
                    "vscode_settings_path": (
                        str(settings_path) if write_vscode_settings else None
                    ),
                },
                indent=2,
            )
        )
        raise typer.Exit(0)

    ch.success("Installed NAVIG YAML schemas")
    ch.dim(f"  Host schema: {host_dst}")
    ch.dim(f"  App schema:  {app_dst}")
    if vscode_settings_written:
        ch.success("VS Code settings updated")
        ch.dim(f"  {settings_path}")
    else:
        ch.dim("")
        ch.dim("VS Code (optional):")
        ch.dim("  Run: navig config schema install --write-vscode-settings")
    raise typer.Exit(0)


def show(target: str = typer.Argument(..., help="Host name or host:app to display")):
    """
    Display configuration.

    Examples:
        # Show host configuration
        navig config show myhost

        # Show app configuration
        navig config show myhost:myapp
    """
    config_manager = get_config_manager()

    if ":" in target:
        # Show app configuration
        host_name, app_name = target.split(":", 1)

        try:
            app_config = config_manager.load_app_config(host_name, app_name)

            ch.success(f"App Configuration: {host_name}:{app_name}")
            rprint(app_config)
        except Exception as e:
            ch.error(f"Failed to load app configuration: {str(e)}")
            raise typer.Exit(1) from e
    else:
        # Show host configuration
        host_name = target

        try:
            host_config = config_manager.load_host_config(host_name)

            ch.success(f"Host Configuration: {host_name}")
            rprint(host_config)
        except Exception as e:
            ch.error(f"Failed to load host configuration: {str(e)}")
            raise typer.Exit(1) from e


def set_mode(mode: str):
    """
    Set the default execution mode.

    Modes:
        - interactive: Prompts for confirmation based on confirmation level (default)
        - auto: Bypasses all confirmation prompts

    Examples:
        navig config set-mode interactive
        navig config set-mode auto
    """
    config_manager = get_config_manager()

    try:
        config_manager.set_execution_mode(mode)
        ch.success(f"Execution mode set to: {mode}")

        if mode == "auto":
            ch.warning("⚠️  Auto mode bypasses all confirmation prompts")
            ch.info("Use --confirm flag to force prompts for specific commands")
    except ValueError as e:
        ch.error(str(e))
        raise typer.Exit(1) from e


def set_confirmation_level(level: str):
    """
    Set the confirmation level for interactive mode.

    Levels:
        - critical: Only confirm destructive operations (DROP, DELETE, rm)
        - standard: Confirm state-changing operations (default)
        - verbose: Confirm all operations including reads

    Examples:
        navig config set-confirmation-level critical
        navig config set-confirmation-level standard
        navig config set-confirmation-level verbose
    """
    config_manager = get_config_manager()

    try:
        config_manager.set_confirmation_level(level)
        ch.success(f"Confirmation level set to: {level}")

        level_descriptions = {
            "critical": "Only destructive operations will require confirmation",
            "standard": "State-changing operations will require confirmation",
            "verbose": "All operations will require confirmation",
        }
        ch.info(level_descriptions.get(level, ""))
    except ValueError as e:
        ch.error(str(e))
        raise typer.Exit(1) from e


def show_settings():
    """
    Display current NAVIG settings including execution mode and confirmation level.

    Examples:
        navig config settings
    """
    config_manager = get_config_manager()
    console = Console()

    # Get all settings
    global_config = config_manager.get_global_config()
    execution_settings = config_manager.get_execution_settings()
    active_host = config_manager.get_active_host()
    active_app = config_manager.get_active_app()
    default_host = global_config.get("default_host")

    # Create table
    table = Table(title="NAVIG Settings", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Description", style="dim")

    # Execution settings
    table.add_row(
        "Execution Mode",
        execution_settings["mode"],
        "interactive: prompts | auto: no prompts",
    )
    table.add_row(
        "Confirmation Level",
        execution_settings["confirmation_level"],
        "critical | standard | verbose",
    )

    # Active context
    table.add_row("Active Host", active_host or "(none)", "Currently selected host")
    table.add_row("Active App", active_app or "(none)", "Currently selected app")
    table.add_row(
        "Default Host", default_host or "(none)", "Fallback when no active host"
    )

    # Other settings
    table.add_row(
        "Log Level", global_config.get("log_level", "INFO"), "Logging verbosity"
    )
    table.add_row(
        "Tunnel Auto-Cleanup",
        str(global_config.get("tunnel_auto_cleanup", True)),
        "Auto-stop tunnels when done",
    )
    table.add_row(
        "Config Directory", str(config_manager.base_dir), "Configuration storage path"
    )

    console.print()
    console.print(table)
    console.print()

    # Show API key status (not the key itself)
    api_key = global_config.get("openrouter_api_key", "")
    if api_key:
        ch.success("OpenRouter API Key: ✓ configured")
    else:
        ch.warning("OpenRouter API Key: ✗ not configured")
        ch.dim("  Set with: navig config set openrouter_api_key <key>")


def set_config(key: str, value: str):
    """
    Set a global configuration value.

    Common keys:
        - openrouter_api_key: API key for AI features
        - log_level: DEBUG, INFO, WARNING, ERROR
        - default_host: Default host when none is active

    Examples:
        navig config set openrouter_api_key sk-or-v1-...
        navig config set log_level DEBUG
        navig config set default_host production
    """
    config_manager = get_config_manager()

    # Handle nested keys (e.g., execution.mode)
    if "." in key:
        parts = key.split(".")
        config = config_manager.global_config

        # Navigate to parent
        for part in parts[:-1]:
            if part not in config:
                config[part] = {}
            config = config[part]

        # Set value
        config[parts[-1]] = value
        config_manager._save_global_config(config_manager.global_config)
    else:
        config_manager.update_global_config({key: value})

    ch.success(f"Set {key} = {value}")


def get_config(key: str):
    """
    Get a configuration value.

    Examples:
        navig config get log_level
        navig config get execution.mode
    """
    config_manager = get_config_manager()

    # Handle nested keys
    if "." in key:
        parts = key.split(".")
        config = config_manager.global_config

        for part in parts:
            if isinstance(config, dict) and part in config:
                config = config[part]
            else:
                ch.error(f"Key not found: {key}")
                raise typer.Exit(1)

        ch.info(f"{key} = {config}")
    else:
        value = config_manager.global_config.get(key)
        if value is not None:
            ch.info(f"{key} = {value}")
        else:
            ch.error(f"Key not found: {key}")
            raise typer.Exit(1)


def edit_config(options: dict = None):
    """
    Edit global configuration file in default editor.

    Opens the ~/.navig/config.yaml file in the system's default editor.
    """
    import os
    import platform
    import subprocess

    config_manager = get_config_manager()
    config_path = config_manager.global_config_dir / "config.yaml"

    if not config_path.exists():
        # Create default config if it doesn't exist
        config_manager.ensure_global_config()

    ch.info(f"Opening config file: {config_path}")

    try:
        if platform.system() == "Windows":
            os.startfile(str(config_path))
        elif platform.system() == "Darwin":  # macOS
            subprocess.run(["open", str(config_path)])
        else:  # Linux and others
            # Try common editors
            editor = os.environ.get("EDITOR", "nano")
            subprocess.run([editor, str(config_path)])

        ch.success("Configuration file opened in editor.")
    except Exception as e:
        ch.error(f"Failed to open editor: {e}")
        ch.info(f"You can manually edit: {config_path}")
        raise typer.Exit(1) from e
