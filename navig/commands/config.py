"""Configuration management commands for NAVIG."""

import json
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from navig.cli._callbacks import show_subcommand_help
from navig import console_helper as ch
from navig.config import get_config_manager
from navig.migration import migrate_all_configs
from navig.platform import paths
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
    global_root = paths.config_dir()
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
    old_dir = paths.config_dir() / "apps"
    new_dir = paths.config_dir() / "hosts"

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
            table.add_row(Path(item["old_file"]).name, Path(item["new_file"]).name, status)

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
            ch.success(f"Successfully migrated {len(results['migrated'])} configuration(s)")
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
                    issues.extend(_validate_host_data(host_path, doc.data, doc, strict=strict))
                else:
                    issues.extend(_validate_host_data(host_path, {}, doc, strict=strict))

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
                        _validate_app_data(app_path, doc.data, doc, known_hosts, strict=strict)
                    )
                else:
                    issues.extend(_validate_app_data(app_path, {}, doc, known_hosts, strict=strict))

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

        scope_result["issues"] = sum(1 for e in issues if e.get("file", "").startswith(str(root)))
        results.append(scope_result)

    if json_out:
        payload = {
            "ok": all(i.get("severity") != "error" for i in issues),
            "scope": scope or ("project" if any(r[0] == "project" for r in roots) else "global"),
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
        ch.dim("  - Validate only the project config: navig config validate --scope project")
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

    target_root = paths.config_dir() if scope == "global" else (Path.cwd() / ".navig")
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
                    "vscode_settings_path": (str(settings_path) if write_vscode_settings else None),
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


# ─────────────────────────────────────────────────────────────────────────────
# Key → env-var mapping used by _source_label()
# ─────────────────────────────────────────────────────────────────────────────
_ENV_KEY_MAP: dict[str, list[str]] = {
    "ai.api_key":          ["NAVIG_API_KEY", "OPENAI_API_KEY"],
    "openrouter_api_key":  ["OPENROUTER_API_KEY"],
    "telegram.bot_token": ["TELEGRAM_BOT_TOKEN", "NAVIG_TELEGRAM_BOT_TOKEN"],
    "openai_api_key":      ["OPENAI_API_KEY"],
    "anthropic_api_key":   ["ANTHROPIC_API_KEY"],
    "groq_api_key":        ["GROQ_API_KEY"],
}

_DEPRECATED_KEYS: dict[str, tuple[str, str]] = {
    "tunnel_auto_cleanup": ("tunnel.auto_cleanup",      "Move to 'tunnel.auto_cleanup'"),
    "tunnel_port_range":   ("tunnel.port_range",        "Move to 'tunnel.port_range'"),
    "ai_model_preference": ("ai.model_preference",     "Move to 'ai.model_preference'"),
    "openrouter_api_key":  ("ai.api_key",              "Move to 'ai.api_key'"),
}


def _source_label(key_path: str, gc: dict, defaults: dict) -> str:
    """Return source tag: env var | config file | default."""
    import os

    for env_var in _ENV_KEY_MAP.get(key_path, []):
        if os.getenv(env_var):
            return "[dim cyan]env var[/]"

    def _walk(d: dict, path: str):
        for part in path.split("."):
            if not isinstance(d, dict):
                return None
            d = d.get(part)  # type: ignore[assignment]
        return d

    gc_val = _walk(gc, key_path)
    def_val = _walk(defaults, key_path)
    if gc_val is not None and gc_val != def_val:
        return "[dim yellow]config file[/]"
    return "[dim]default[/]"


def _mask_secret(value: str | None) -> str:
    if not value:
        return "[red]not set[/]"
    if len(value) <= 12:
        return "[green]✓ configured[/]"
    return f"[green]✓[/] [dim]{value[:6]}…{value[-4:]}[/]"


def _bool_icon(val: bool) -> str:
    return "[green]✓[/]" if val else "[red]✗[/]"


def _section_table(rows: list[tuple[str, str, str]], gc: dict, defaults: dict) -> Table:
    """Build a simple 3-column inner table for a config panel."""
    t = Table(box=box.SIMPLE, show_header=True, pad_edge=False,
              header_style="bold dim", show_edge=False)
    t.add_column("Setting",  style="cyan",    min_width=28, no_wrap=True)
    t.add_column("Value",    min_width=28)
    t.add_column("Source",   min_width=10)
    for label, value, key_path in rows:
        t.add_row(label, value, _source_label(key_path, gc, defaults))
    return t


def show_settings():
    """
    Display current NAVIG settings grouped by functional domain.

    Shows current value, config source, and any deprecated keys.
    Use --json for clean canonical output.

    Examples:
        navig config settings
        navig config settings --json
    """
    config_manager = get_config_manager()
    console = Console()

    # Get all settings
    global_config = config_manager.get_global_config()
    execution_settings = config_manager.get_execution_settings()
    active_host = config_manager.get_active_host()
    active_app = config_manager.get_active_app()
    default_host = global_config.get("default_host")

    # Load defaults for source attribution
    defaults: dict = {}
    try:
        from navig.core.config_loader import load_config as _load_config
        _defaults_file = Path(__file__).resolve().parents[2] / "config" / "defaults.yaml"
        if _defaults_file.exists():
            defaults = _load_config(_defaults_file, strict=False) or {}
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    gc = global_config
    ai = gc.get("ai") or {}
    routing = ai.get("routing") or {}
    models = routing.get("models") or {}

    # Helper: resolve model from nested or legacy flat key
    def _model(slot: str) -> str:
        nested = models.get(slot, {}).get("model")
        if nested:
            return nested
        legacy = routing.get(f"{slot}_model", "")
        return legacy or "[dim]not set[/]"

    # ── 🤖 AI ──────────────────────────────────────────────────────────────
    routing_mode = routing.get("mode", "single")
    routing_enabled = routing.get("enabled", False)
    model_pref = ai.get("model_preference") or []
    from navig.ai import _get_model_preference
    model_pref = _get_model_preference(gc)
    primary = model_pref[0] if model_pref else "[dim]not set[/]"

    console.print(Panel(
        _section_table([
            ("Default provider",       ai.get("default_provider") or "[dim]auto (mode-based routing)[/]", "ai.default_provider"),
            ("Primary model",          primary,                                                             "ai.model_preference"),
            ("Temperature",            str(ai.get("temperature", 0.7)),                                    "ai.temperature"),
            ("Max tokens",             str(ai.get("max_tokens", 4096)),                                    "ai.max_tokens"),
            ("API key",                _mask_secret(ai.get("api_key")
                                         or gc.get("openrouter_api_key")),                                 "ai.api_key"),
            ("Tier routing",           f"{_bool_icon(routing_enabled)} ({routing_mode})",                  "ai.routing.enabled"),
            ("Prefer local (Ollama)",  _bool_icon(routing.get("prefer_local", True)),                      "ai.routing.prefer_local"),
            ("Small model",            _model("small"),                                                    "ai.routing.models.small.model"),
            ("Big model",              _model("big"),                                                      "ai.routing.models.big.model"),
            ("Router model override",   routing.get("router_model") or "[dim]uses small model[/]",         "ai.routing.router_model"),
        ], gc, defaults),
        title="[bold cyan]🤖  AI[/]", border_style="cyan", expand=False,
    ))

    # ── 📱 Telegram ────────────────────────────────────────────────────────
    tg = gc.get("telegram") or {}
    try:
        from navig.messaging.secrets import resolve_telegram_bot_token
        tg_token = resolve_telegram_bot_token(gc)
    except Exception:  # noqa: BLE001
        tg_token = tg.get("bot_token")

    console.print(Panel(
        _section_table([
            ("Bot token",            _mask_secret(tg_token),                                       "telegram.bot_token"),
            ("Allowed users",        str(tg.get("allowed_users") or []),                           "telegram.allowed_users"),
            ("Require auth",         _bool_icon(tg.get("require_auth", True)),                    "telegram.require_auth"),
            ("Session isolation",    _bool_icon(tg.get("session_isolation", True)),               "telegram.session_isolation"),
            ("Group activation",     tg.get("group_activation_mode", "mention"),                  "telegram.group_activation_mode"),
            ("Deck URL",             tg.get("deck_url") or "[dim]not set[/]",                     "telegram.deck_url"),
        ], gc, defaults),
        title="[bold magenta]📱  Telegram[/]", border_style="magenta", expand=False,
    ))

    # ── 🔗 Tunnel ──────────────────────────────────────────────────────────
    tn = gc.get("tunnel") or {}
    console.print(Panel(
        _section_table([
            ("Auto cleanup",   _bool_icon(tn.get("auto_cleanup", True)),      "tunnel.auto_cleanup"),
            ("Port range",     str(tn.get("port_range", [3307, 3399])),       "tunnel.port_range"),
            ("Default timeout", f"{tn.get('default_timeout', 300)}s",         "tunnel.default_timeout"),
        ], gc, defaults),
        title="[bold blue]🔗  Tunnel[/]", border_style="blue", expand=False,
    ))

    # ── 🧠 Memory ──────────────────────────────────────────────────────────
    mem = gc.get("memory") or {}
    console.print(Panel(
        _section_table([
            ("Enabled",             _bool_icon(mem.get("enabled", True)),                              "memory.enabled"),
            ("Index on startup",    _bool_icon(mem.get("index_on_startup", False)),                    "memory.index_on_startup"),
            ("Embedding provider",  mem.get("embedding_provider", "openai"),                         "memory.embedding_provider"),
            ("Embedding model",     mem.get("embedding_model", "text-embedding-3-small"),             "memory.embedding_model"),
        ], gc, defaults),
        title="[bold green]🧠  Memory[/]", border_style="green", expand=False,
    ))

    # ── 🖥️ Deck ────────────────────────────────────────────────────────────
    dk = gc.get("deck") or {}
    console.print(Panel(
        _section_table([
            ("Enabled",   _bool_icon(dk.get("enabled", True)),   "deck.enabled"),
            ("Port",      str(dk.get("port", 3080)),             "deck.port"),
            ("Bind",      dk.get("bind", "127.0.0.1"),           "deck.bind"),
            ("Dev mode",  _bool_icon(dk.get("dev_mode", False)), "deck.dev_mode"),
        ], gc, defaults),
        title="[bold yellow]🖥️  Deck[/]", border_style="yellow", expand=False,
    ))

    # ── 🌐 Gateway ─────────────────────────────────────────────────────────
    gw = gc.get("gateway") or {}
    console.print(Panel(
        _section_table([
            ("Enabled",      _bool_icon(gw.get("enabled", False)),             "gateway.enabled"),
            ("Listen",       f"{gw.get('host', '127.0.0.1')}:{gw.get('port', 8765)}", "gateway.port"),
            ("Require auth", _bool_icon(gw.get("require_auth", True)),         "gateway.require_auth"),
        ], gc, defaults),
        title="[bold]🌐  Gateway[/]", border_style="white", expand=False,
    ))

    # ── ⚙️ Execution & Context ─────────────────────────────────────────────
    ex = gc.get("execution") or {}
    console.print(Panel(
        _section_table([
            ("Mode",               execution_settings.get("mode", "interactive"),            "execution.mode"),
            ("Confirmation level", execution_settings.get("confirmation_level", "standard"), "execution.confirmation_level"),
            ("Auto-confirm safe",  _bool_icon(ex.get("auto_confirm_safe", False)),            "execution.auto_confirm_safe"),
            ("Timeout",            f"{ex.get('timeout_seconds', 60)}s",                       "execution.timeout_seconds"),
            ("Active host",        active_host or "[dim]none[/]",                            "active_host"),
            ("Active app",         active_app  or "[dim]none[/]",                            "active_app"),
            ("Default host",       default_host or "[dim]none[/]",                           "default_host"),
            ("Log level",          gc.get("log_level", "INFO"),                             "log_level"),
            ("Config dir",         str(config_manager.base_dir),                            "config_dir"),
            ("Debug mode",         _bool_icon(gc.get("debug_mode", False)),                 "debug_mode"),
        ], gc, defaults),
        title="[bold]⚙️  Execution & Context[/]", border_style="white", expand=False,
    ))

    # ── ⚠️ Deprecated Keys ─────────────────────────────────────────────────
    found = [(k, *v) for k, v in _DEPRECATED_KEYS.items() if k in gc]
    if found:
        dep = Table(box=box.SIMPLE, show_header=True, pad_edge=False,
                    header_style="bold yellow", show_edge=False)
        dep.add_column("Legacy Key",     style="yellow", min_width=26)
        dep.add_column("Canonical Path", style="cyan",   min_width=26)
        dep.add_column("Migration Hint")
        for legacy_key, canonical, hint in found:
            dep.add_row(legacy_key, canonical, hint)
        console.print(Panel(
            dep,
            title="[bold yellow]⚠️  Deprecated Keys — migrate before v2.0[/]",
            border_style="yellow", expand=False,
        ))
    else:
        console.print("[dim green]✓ No deprecated keys found.[/]")


# Map config key names that are sensitive → (vault provider, credential type)
_SENSITIVE_CFG_TO_VAULT: dict[str, tuple[str, str]] = {
    "openrouter_api_key": ("openrouter", "api_key"),
    "openai_api_key":     ("openai",     "api_key"),
    "anthropic_api_key":  ("anthropic",  "api_key"),
    "groq_api_key":       ("groq",       "api_key"),
    "google_api_key":     ("google",     "api_key"),
    "gemini_api_key":     ("google",     "api_key"),
    "nvidia_api_key":     ("nvidia",     "api_key"),
    "nim_api_key":        ("nvidia",     "api_key"),
    "xai_api_key":        ("xai",        "api_key"),
    "grok_key":           ("xai",        "api_key"),
    "mistral_api_key":    ("mistral",    "api_key"),
    "github_token":       ("github_models", "token"),
    "gh_token":           ("github_models", "token"),
    "telegram_bot_token": ("telegram",   "token"),
}


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

    # Best-effort vault write-through for known sensitive config keys (#62)
    vault_entry = _SENSITIVE_CFG_TO_VAULT.get(key.lower())
    if vault_entry and value:
        vault_provider, vault_cred_type = vault_entry
        try:
            from navig.vault import get_vault  # noqa: PLC0415

            _vault = get_vault()
            existing = _vault.get(vault_provider, caller="config.set")
            if existing is not None:
                _vault.update(existing.id, data={vault_cred_type: value})
            else:
                _vault.add(
                    provider=vault_provider,
                    credential_type=vault_cred_type,
                    data={vault_cred_type: value},
                    profile_id="default",
                    label=vault_provider.replace("_", " ").title(),
                )
            ch.dim(f"  Also stored in vault under [{vault_provider}/{vault_cred_type}]")
        except Exception:  # noqa: BLE001
            pass  # vault unavailable — config write is sufficient


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


config_app = typer.Typer(
    help="Manage NAVIG configuration and settings",
    invoke_without_command=True,
    no_args_is_help=False,
)


@config_app.callback()
def config_callback(ctx: typer.Context):
    """Configuration management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("config", ctx)
        raise typer.Exit()


@config_app.command("migrate-legacy", hidden=True)
def config_migrate_legacy(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be migrated without making changes"
    ),
    no_backup: bool = typer.Option(
        False, "--no-backup", help="Skip creating backups before migration"
    ),
):
    """Migrate legacy configurations to new format."""
    migrate(dry_run=dry_run, no_backup=no_backup)


def _validation_opts_from_ctx(
    ctx: typer.Context,
    *,
    scope: str | None,
    strict: bool,
    json_out: bool,
) -> dict[str, Any]:
    opts = dict(ctx.obj or {})
    if json_out:
        opts["json"] = True
    if scope:
        opts["scope"] = scope
    if strict:
        opts["strict"] = True
    return opts


@config_app.command("test")
def config_test(
    ctx: typer.Context,
    host: str | None = typer.Argument(
        None, help="Host name to validate (validates all if not specified)"
    ),
    scope: str = typer.Option(
        None,
        "--scope",
        help="What to validate: project (.navig), global (~/.navig), or both",
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    json_out: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
):
    """Alias for: navig config validate."""
    validate(host=host, options=_validation_opts_from_ctx(ctx, scope=scope, strict=strict, json_out=json_out))


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    host: str | None = typer.Argument(
        None, help="Host name to validate (validates all if not specified)"
    ),
    scope: str = typer.Option(
        None,
        "--scope",
        help="What to validate: project (.navig), global (~/.navig), or both",
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
    json_out: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
):
    validate(host=host, options=_validation_opts_from_ctx(ctx, scope=scope, strict=strict, json_out=json_out))


schema_app = typer.Typer(
    help="JSON schema tools (VS Code integration)",
    invoke_without_command=True,
    no_args_is_help=False,
)
config_app.add_typer(schema_app, name="schema")


@schema_app.callback()
def schema_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        show_subcommand_help("config schema", ctx)
        raise typer.Exit()


@schema_app.command("install")
def config_schema_install(
    ctx: typer.Context,
    scope: str = typer.Option(
        "global",
        "--scope",
        help="Where to install schemas: global (~/.navig) or project (.navig)",
    ),
    write_vscode_settings: bool = typer.Option(
        False,
        "--write-vscode-settings",
        help="Write .vscode/settings.json yaml.schemas mappings in the current project",
    ),
    json_out: bool = typer.Option(False, "--json", help="Output installation result as JSON"),
):
    """Install NAVIG YAML JSON Schemas for editor validation/autocomplete."""
    opts = dict(ctx.obj or {})
    if json_out:
        opts["json"] = True
    install_schemas(scope=scope, write_vscode_settings=write_vscode_settings, options=opts)


@config_app.command("show-global", hidden=True)
def config_show_legacy(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Host name or host:app to display"),
):
    """Display host or app configuration."""
    show(target=target)


@config_app.command("settings")
def config_settings(ctx: typer.Context):
    """Display current NAVIG settings including execution mode and confirmation level."""
    show_settings()


@config_app.command("set-mode")
def config_set_mode(
    ctx: typer.Context,
    mode: str = typer.Argument(..., help="Execution mode: 'interactive' or 'auto'"),
):
    set_mode(mode)


@config_app.command("set-confirmation-level")
def config_set_confirmation_level(
    ctx: typer.Context,
    level: str = typer.Argument(
        ..., help="Confirmation level: 'critical', 'standard', or 'verbose'"
    ),
):
    set_confirmation_level(level)


@config_app.command("set")
def config_set_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Configuration key (e.g., 'log_level', 'execution.mode')"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a global configuration value."""
    set_config(key, value)


@config_app.command("get-raw", hidden=True)
def config_get_legacy(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Configuration key to retrieve"),
):
    """Get a configuration value."""
    get_config(key)


@config_app.command("edit")
def config_edit(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Host name or host:app to edit"),
):
    """Open configuration in default editor."""
    edit_config({"target": target})


@config_app.command("backup")
def config_backup_cmd(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Output file path (auto-generated if not provided)"
    ),
    format: str = typer.Option(
        "archive", "--format", "-f", help="Output format: 'archive' (tar.gz) or 'json'"
    ),
    include_secrets: bool = typer.Option(
        False,
        "--include-secrets",
        help="Include unredacted secrets (passwords, API keys)",
    ),
    encrypt: bool = typer.Option(
        False, "--encrypt", "-e", help="Encrypt the output with a password"
    ),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Encryption password (prompted if not provided)"
    ),
):
    obj = ctx.obj or {}
    from navig.commands.config_backup import export_config

    export_config(
        {
            "output": output,
            "format": format,
            "include_secrets": include_secrets,
            "encrypt": encrypt,
            "password": password,
            "yes": obj.get("yes", False),
            "confirm": obj.get("confirm", False),
            "json": obj.get("json", False),
        }
    )


@config_app.command("migrate")
def config_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without saving"),
):
    import yaml

    from navig.core.migrations import migrate_config

    cm = get_config_manager()
    global_config_file = cm.global_config_dir / "config.yaml"

    if not global_config_file.exists():
        ch.error("No global configuration found.")
        raise typer.Exit(1)

    try:
        with open(global_config_file, encoding="utf-8") as file_handle:
            raw_config = yaml.safe_load(file_handle) or {}

        migrated, modified = migrate_config(raw_config)

        if not modified:
            ch.success("Configuration is already up to date.")
            return

        if dry_run:
            ch.info("Dry run: Configuration would be updated.")
            ch.info(f"New version: {migrated.get('version')}")
        else:
            with open(global_config_file, "w", encoding="utf-8") as file_handle:
                yaml.dump(migrated, file_handle, default_flow_style=False, sort_keys=False)
            ch.success(f"Configuration migrated to version {migrated.get('version')}")

    except Exception as e:
        ch.error(f"Migration failed: {e}")
        raise typer.Exit(1) from e


@config_app.command("audit")
def config_audit_cmd(
    fix: bool = typer.Option(False, "--fix", help="Attempt to fix issues automatically"),
):
    from navig.commands.security import config_audit as security_config_audit

    security_config_audit({"fix": fix})


@config_app.command("show")
def config_show_cmd(
    scope: str = typer.Argument("global", help="Scope: global or host name"),
):
    cm = get_config_manager()

    if scope == "global":
        config = cm._load_global_config()
        ch.print_json(config)
    else:
        try:
            config = cm.load_host_config(scope)
            ch.print_json(config)
        except Exception as e:
            ch.error(str(e))


@config_app.command("get")
def config_get_cmd(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.default_provider)"),
):
    cm = get_config_manager()
    config = cm._load_global_config()
    keys = key.split(".")
    value: Any = config

    try:
        for key_part in keys:
            if isinstance(value, dict):
                value = value.get(key_part)
            else:
                value = None
                break

        if value is None:
            ch.warning(f"Key '{key}' not found or is empty.")
        elif isinstance(value, (dict, list)):
            ch.print_json(value)
        else:
            ch.console.print(str(value))

    except Exception as e:
        ch.error(f"Error retrieving key: {e}")


@config_app.command("set-raw", hidden=True)
def config_set_legacy_raw(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.model_preference)"),
    value: str = typer.Argument(..., help="Value to set (JSON/YAML format for complex types)"),
):
    import yaml

    try:
        try:
            parsed_value = yaml.safe_load(value)
        except Exception:
            parsed_value = value

        cm = get_config_manager()
        global_config_file = cm.global_config_dir / "config.yaml"

        if not global_config_file.exists():
            ch.error("No global configuration found.")
            raise typer.Exit(1)

        with open(global_config_file, encoding="utf-8") as file_handle:
            config = yaml.safe_load(file_handle) or {}

        keys = key.split(".")
        target = config
        for key_part in keys[:-1]:
            if key_part not in target:
                target[key_part] = {}
            target = target[key_part]
            if not isinstance(target, dict):
                ch.error(f"Cannot set key '{key}' because '{key_part}' is not a dictionary.")
                raise typer.Exit(1)

        target[keys[-1]] = parsed_value

        with open(global_config_file, "w", encoding="utf-8") as file_handle:
            yaml.dump(config, file_handle, default_flow_style=False, sort_keys=False)

        ch.success(f"Updated '{key}' to: {parsed_value}")
    except Exception as e:
        ch.error(f"Error setting config: {e}")
        raise typer.Exit(1) from e
