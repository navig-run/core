"""
NAVIG Main Entry Point

New modular entry point with plugin discovery and loading.
Maintains 100% backward compatibility with existing CLI commands.

Entry Points:
- main(): Called by 'navig' command (pyproject.toml)
- app: Typer app instance (for testing)

Plugin Loading:
1. Core commands from navig/cli.py are loaded first
2. Built-in plugins from navig/plugins/ are discovered
3. User plugins from ~/.navig/plugins/ are discovered
4. Project plugins from .navig/plugins/ are discovered
5. All enabled plugins with satisfied dependencies are registered
"""

import logging
import os
import shutil
import sys
from pathlib import Path

from navig.platform import paths

_log = logging.getLogger(__name__)


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _fast_status_context() -> tuple[str, str]:
    active_host = os.environ.get("NAVIG_ACTIVE_HOST", "").strip()
    if not active_host:
        active_host = _read_text_file(paths.cache_dir() / "active_host.txt")
    if not active_host:
        active_host = "none"

    active_profile = os.environ.get("NAVIG_PROFILE", "").strip()
    if not active_profile:
        active_profile = _read_text_file(paths.config_dir() / "active_profile")
    if not active_profile:
        active_profile = "default"

    return active_host, active_profile


def _fast_rotating_tip() -> str:
    tips = [
        "Pro tip: Use 'navig host test' before remote run/db/file operations.",
        "What's new: 'navig help --schema' exposes machine-readable commands.",
        "Pro tip: Use '--plain' for scripts and '--json' for structured output.",
        "What's new: 'navig init --status' reports onboarding readiness.",
    ]
    try:
        import datetime as _dt
        idx = _dt.date.today().toordinal() % len(tips)
    except (ImportError, ValueError):
        idx = 0
    return tips[idx]


def _format_cmd(name: str, desc: str) -> str:
    return f"    {name:<13} {desc}"


def _fast_help_text(version: str) -> str:
    # Keep this ASCII-only so it works on Windows default consoles.
    hr = "-" * 66
    host, profile = _fast_status_context()
    return "\n".join(
        [
            hr,
            f"  NAVIG v{version}  |  host: {host}  |  profile: {profile}",
            hr,
            "  Server management from your terminal. Fast, scriptable, exact.",
            "  CORE",
            _format_cmd("init", "Initialize NAVIG workspace"),
            _format_cmd("config", "View or set configuration values"),
            _format_cmd("profile", "Switch operating profile"),
            _format_cmd("version", "Show version and build info"),
            _format_cmd("upgrade", "Upgrade NAVIG to latest release"),
            _format_cmd("plugin", "Manage plugins and extensions"),
            "  CONNECTIONS",
            _format_cmd("host", "Add, remove, switch, or list hosts"),
            _format_cmd("tunnel", "Open and manage SSH tunnels"),
            _format_cmd("proxy", "Configure HTTP/SOCKS proxy routing"),
            _format_cmd("port", "Scan or forward remote ports"),
            "  APPS & SERVICES",
            _format_cmd("app", "Deploy, start, stop, or scale apps"),
            _format_cmd("service", "Manage NAVIG daemon"),
            _format_cmd("docker", "Build, run, and inspect containers"),
            _format_cmd("web", "Nginx/Caddy/Apache operations"),
            _format_cmd("run", "Execute command on active host"),
            "  INFRASTRUCTURE",
            _format_cmd("backup", "Backup and restore NAVIG state"),
            _format_cmd("db", "Query, dump, and restore databases"),
            _format_cmd("file", "Transfer and edit remote files"),
            _format_cmd("cron", "Schedule recurring remote jobs"),
            _format_cmd("job", "Run and monitor one-off jobs"),
            "  SECURITY",
            _format_cmd("cert", "Issue, renew, or inspect TLS certs"),
            _format_cmd("key", "Manage SSH keys and credentials"),
            _format_cmd("firewall", "View and modify firewall rules"),
            _format_cmd("secret", "Store encrypted environment secrets"),
            "  ENVIRONMENT",
            _format_cmd("env", "View/override environment variables"),
            _format_cmd("dns", "Query or update DNS records"),
            "  MONITORING",
            _format_cmd("status", "Live status of connected hosts"),
            _format_cmd("health", "Run cross-service health checks"),
            _format_cmd("logs", "Stream or search host/service logs"),
            _format_cmd("stats", "CPU, memory, disk, and network metrics"),
            "  DEVELOPER",
            _format_cmd("alias", "Create command shortcuts"),
            _format_cmd("script", "Save and replay command sequences"),
            _format_cmd("mcp", "MCP tool integrations"),
            _format_cmd("help", "Browse in-app help topics"),
            hr,
            "  EXAMPLES",
            "    navig host add                         Add a new remote server",
            "    navig host use staging-01              Switch active host",
            "    navig run 'df -h'                      Check disk on active host",
            "    navig logs api --tail 200              Stream last 200 API lines",
            "    navig cert renew --host prod-01        Renew TLS cert on prod",
            "    navig db dump mydb -o predeploy.sql    Snapshot DB before deploy",
            hr,
            f"  {_fast_rotating_tip()}",
            "  navig <command> --help   |   navig help <topic>",
            hr,
        ]
    )


def _maybe_handle_fast_path(argv: list[str]) -> bool:
    """Handle ultra-fast invocations without importing the full CLI.

    We only intercept cases where Typer would show top-level help/version,
    not subcommand help (e.g. `navig host --help`).

    Returns True if handled.
    """
    args = [a for a in argv[1:] if a]
    from navig.cli.registration import extract_non_global_tokens

    command_tokens = extract_non_global_tokens(args)

    if not args:
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    # Global-only invocations (e.g. `navig --host prod`) or global-flag-prefixed
    # top-level help/version should stay on the ultra-fast path.
    if not command_tokens:
        from navig import __version__

        if any(flag in args for flag in {"--version", "-v"}):
            sys.stdout.write(__version__ + "\n")
            return True

        # Includes explicit help flags and global-only no-command invocations.
        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    if len(args) == 1 and args[0] in {"--help", "-h"}:
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    # `navig help` with no topic → same as `navig --help`
    if len(args) == 1 and args[0] == "help":
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    if len(args) == 1 and args[0] in {"--version", "-v"}:
        from navig import __version__

        sys.stdout.write(__version__ + "\n")
        return True

    # `navig start` — alias for `navig dashboard` (Kraken TUI)
    # Let normal CLI parsing handle help forms.
    if command_tokens and command_tokens[0] == "start":
        if len(command_tokens) > 1 and any(flag in command_tokens[1:] for flag in ("--help", "-h", "help")):
            return False
        return _handle_start_command(command_tokens[1:])

    return False


def _handle_start_command(extra_args: list[str]) -> bool:
    """Launch the NAVIG Kraken Dashboard (Rich TUI).

    `navig start` is a convenient alias for `navig dashboard`.
    Accepts optional flags: --fast (skip boot animation).
    """
    from navig.commands.dashboard import run_dashboard, run_dashboard_simple

    skip_boot = "--fast" in extra_args
    simple = "--simple" in extra_args

    if simple:
        run_dashboard_simple()
    else:
        run_dashboard(skip_boot=skip_boot)
    return True


def _normalize_help_compat_args(argv: list[str]) -> list[str]:
    """Normalize legacy help forms to canonical ``--help``.

    Compatibility rules (best-effort):
    - ``navig <path> help`` -> ``navig <path> --help``
    - ``navig <path> -h``   -> ``navig <path> --help`` (only when ``-h`` is trailing)
    - ``navig help <cmd>``  -> ``navig <cmd> --help``   (leading help rewrite)

    We intentionally do not rewrite top-level ``navig help`` or ``navig -h``.
    """

    if len(argv) <= 2:
        return argv

    from navig.cli.registration import extract_non_global_tokens

    args = argv[1:]
    non_global_tokens = extract_non_global_tokens(args)
    if not non_global_tokens:
        return argv

    value_flags = {"--host", "-h", "--app", "-p"}
    global_flags = {
        "--host",
        "-h",
        "--app",
        "-p",
        "--verbose",
        "--quiet",
        "-q",
        "--dry-run",
        "--yes",
        "-y",
        "--confirm",
        "-c",
        "--raw",
        "--json",
        "--debug-log",
        "--no-cache",
        "--version",
        "-v",
        "--help",
    }

    non_global_positions: list[int] = []
    skip_next = False
    for idx, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if token in value_flags:
            skip_next = True
            continue
        if token in global_flags or token.startswith("--"):
            continue
        non_global_positions.append(idx)

    if non_global_tokens[0] == "help" and len(non_global_tokens) == 1:
        return argv

    # Leading help: `navig help db` → `navig db --help`
    if non_global_tokens[0] == "help" and len(non_global_tokens) >= 2:
        normalized = list(argv)
        help_arg_index = 1 + non_global_positions[0]
        del normalized[help_arg_index]
        normalized.append("--help")
        return normalized

    # Legacy alias: `navig memory list` -> `navig memory sessions`
    if len(non_global_tokens) >= 2 and non_global_tokens[0] == "memory" and non_global_tokens[1] == "list":
        normalized = list(argv)
        list_arg_index = 1 + non_global_positions[1]
        normalized[list_arg_index] = "sessions"
        args = normalized[1:]
        argv = normalized

    if "--help" in args:
        return argv

    normalized = list(argv)

    if non_global_tokens[-1] == "help":
        help_arg_index = 1 + non_global_positions[-1]
        normalized[help_arg_index] = "--help"
        return normalized

    if args[-1] == "-h" and args[0] != "-h":
        normalized[-1] = "--help"
        return normalized

    return argv


def _get_console():
    """Lazy-create a Rich console for error output."""
    try:
        from rich.console import Console

        return Console(stderr=True)
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Failed to initialize rich console: %s", e, exc_info=True
        )
        return None


def _eprint(message: str) -> None:
    """Best-effort stderr output without assuming Rich is available."""
    console = _get_console()
    if console is not None:
        console.print(message)
    else:
        sys.stderr.write(str(message) + "\n")


def _check_first_run() -> None:
    """Trigger onboarding on first run if ~/.navig/onboarding.json is absent.

    Safe to call on every invocation — returns immediately if already done.
    Non-TTY environments (CI, scripts) auto-skip interactive Phase 2 steps
    via _tty_check() guards already built into each Phase 2 step.

    Opt-out: set NAVIG_SKIP_ONBOARDING=1 in the environment.
    """
    try:
        from navig.onboarding.runner import (
            run_engine_onboarding,
            should_auto_run_onboarding,
        )

        if not should_auto_run_onboarding(sys.argv):
            return

        run_engine_onboarding(
            show_banner=True,
            respect_skip_env=True,
            skip_if_configured=True,
        )
    except Exception as exc:  # never crash main on onboarding failure
        _eprint(f"[dim]First-run setup skipped: {exc}[/dim]")


# Track plugin state for status command
_loaded_plugins: list[str] = []
_failed_plugins: list[dict[str, str]] = []

# ---------------------------------------------------------------------------
# Single source of truth for all built-in (plugin-free) command names.
# Rules:
#   • Every command listed here skips plugin loading on startup (~110 ms saving).
#   • _suggest_did_you_mean() adds "plugin" and "help" on top of this set.
# When you add a new built-in command, add it ONCE here.
# ---------------------------------------------------------------------------
_BUILTIN_COMMANDS: frozenset[str] = frozenset({
    "host", "h",
    "app", "a",
    "run", "r",
    "db", "database",
    "file", "f",
    "docker",
    "tunnel", "t",
    "web",
    "backup",
    "config",
    "status",
    "version",
    "log", "l",
    "local",
    "mcp",
    "profile",
    "index",
    "skills", "skill",
    "flow",
    "wiki",
    "scaffold",
    "migrate",
    "server-template",
    "bridge",
    "farmore",
    "copilot",
    "inbox",
    "sync",
    "agent", "agents",
    "service",
    "stack",
    "tray",
    "formation",
    "council",
    "auto",
    "evolve",
    "script",
    "calendar",
    "mode",
    "email",
    "voice",
    "crash",
    "telegram", "tg",
    "matrix", "mx",
    "store",
    "vault",
    "flux", "fx",
    "cortex",
    "desktop",
    "net",
    "server", "s",
    "links",
    "kg", "knowledge",
    "webhook", "webhooks",
    "cron",
    "doctor",
    "prompts",
    "browser",
    "import",
    "dispatch",
    "contacts", "ct",
    "paths",
    "radar",
    "watch",
    "mesh",
    "debug",
    "memory",
    "spaces",
    "telemetry",
    "wut", "eval",
    "webdash",
    "snapshot", "replay",
    "cloud",
    "benchmark",
    "finance",
    "work",
    "origin",
    "user",
    "node",
    "boot",
    "space",
    "blueprint",
    "deck",
    "portable",
    "system",
    "mount",
    "update",
    "proactive",
    "package", "pack", "packs",
})


def load_plugins_into_app(app) -> None:
    """
    Discover and load all available plugins into the Typer app.

    Args:
        app: Main Typer app instance to register plugins into
    """
    global _loaded_plugins, _failed_plugins

    try:
        from navig.plugins import get_plugin_manager

        manager = get_plugin_manager()

        # Discover all plugins
        manager.discover_plugins()

        # Load all enabled plugins
        _loaded_plugins, _failed_plugins = manager.load_all_plugins(silent=False)

        # Register loaded plugins as sub-commands
        for name, plugin_app in manager.get_loaded_apps().items():
            try:
                app.add_typer(plugin_app, name=name)
            except Exception as e:
                _failed_plugins.append(
                    {"name": name, "reason": f"Failed to register: {e}"}
                )

    except Exception as e:
        # Plugin system failure should not break NAVIG
        _eprint(f"[yellow]⚠ Plugin system error: {e}[/yellow]")


def _should_skip_plugin_loading(argv: list[str]) -> bool:
    """Return True when plugin loading should be skipped for fast startup.

    We skip plugin discovery for commands that only need core CLI wiring:
    - no args (shows compact help)
    - --help / --version
    - navig help (in-app help)
    - core built-in commands that never need plugins

    This keeps cold start fast while preserving full functionality for
    real operational commands.
    """
    from navig.cli.registration import extract_non_global_tokens

    raw_args = [a for a in argv[1:] if a]  # strip program name
    args = extract_non_global_tokens(raw_args)

    if not args:
        return True

    if len(args) == 1 and args[0] in {"--help", "--version"}:
        return True

    # In-app help command (with or without topic).
    if args and args[0] == "help":
        return True

    # Core built-in commands that never use plugins.
    # Skipping plugins saves ~110ms on these hot paths.
    if args[0] in _BUILTIN_COMMANDS:
        return True

    if args[0] == "plugin":
        return False

    # Check plugin cache for known plugin commands (best-effort speedup).
    # Never short-circuit unknown commands here: stale cache can otherwise
    # hide valid plugin commands after install/rename and cause false negatives.
    try:
        import json

        # Path is imported at module level; no redundant import needed here.
        cache_file = paths.data_dir() / "plugins_cache.json"
        if cache_file.exists():
            with open(cache_file, encoding="utf-8") as f:
                cached_data = json.load(f)
            plugins = cached_data.get("plugins", {})
            cached_names = set(plugins.keys())
            for plugin_data in plugins.values():
                plugin_name = plugin_data.get("name")
                if isinstance(plugin_name, str) and plugin_name:
                    cached_names.add(plugin_name)

                plugin_path = plugin_data.get("path")
                if isinstance(plugin_path, str) and plugin_path:
                    # Path is imported at module top level; no re-import needed.
                    cached_names.add(Path(plugin_path).name)

            if args[0] in cached_names:
                return False
    except json.JSONDecodeError:
        # P1-6: Corrupted plugin cache — log a warning instead of silently ignoring
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Plugin cache corrupted at %s — skipping cache check",
            cache_file,
        )
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "Plugin check exception: %s", e, exc_info=True
        )

    return False


def add_plugin_commands(app) -> None:
    """
    Add plugin management commands to the main app.

    Commands:
    - navig plugin list: Show all plugins and their status
    - navig plugin enable <name>: Enable a plugin
    - navig plugin disable <name>: Disable a plugin
    - navig plugin info <name>: Show detailed plugin information
    """
    import typer

    from navig import console_helper as ch

    plugin_app = typer.Typer(
        name="plugin",
        help="Manage NAVIG plugins",
        no_args_is_help=True,
    )

    def _plugin_identifiers(info) -> list[str]:
        identifiers: list[str] = []
        for candidate in (info.name, info.path.name):
            if candidate and candidate not in identifiers:
                identifiers.append(candidate)
        return identifiers

    @plugin_app.command("list")
    def plugin_list(
        all_plugins: bool = typer.Option(
            False, "--all", "-a", help="Include disabled plugins"
        ),
    ):
        """List all installed plugins."""
        from rich.table import Table

        from navig.plugins import get_plugin_manager

        manager = get_plugin_manager()
        plugins = manager.list_plugins()

        if not plugins:
            ch.info("No plugins installed")
            ch.dim("Built-in plugins are in navig/plugins/")
            ch.dim("User plugins can be added to ~/.navig/plugins/")
            return

        table = Table(title="NAVIG Plugins")
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="dim")
        table.add_column("Source", style="dim")
        table.add_column("Status", style="bold")
        table.add_column("Description")

        for info in sorted(plugins.values(), key=lambda plugin: plugin.name.lower()):
            if not all_plugins and not info.enabled:
                continue

            if info.loaded:
                status = "[green]+ Loaded[/green]"
            elif not info.enabled:
                status = "[dim]o Disabled[/dim]"
            elif info.error:
                status = (
                    f"[red]x {info.error[:30]}...[/red]"
                    if len(info.error) > 30
                    else f"[red]x {info.error}[/red]"
                )
            else:
                status = "[yellow]? Not loaded[/yellow]"

            source_icons = {"builtin": "builtin", "user": "user", "project": "project"}
            source = f"{source_icons.get(info.source, '?')}"

            table.add_row(
                info.name,
                info.version,
                source,
                status,
                (
                    info.description[:50] + "..."
                    if len(info.description) > 50
                    else info.description
                ),
            )

        ch.console.print(table)

        if _failed_plugins:
            ch.dim("")
            ch.warning(
                f"{len(_failed_plugins)} plugin(s) failed to load. Use 'navig plugin info <name>' for details."
            )

    @plugin_app.command("info")
    def plugin_info(
        name: str = typer.Argument(..., help="Plugin name"),
    ):
        """Show detailed information about a plugin."""
        from navig.plugins import get_plugin_manager

        manager = get_plugin_manager()
        info = manager.get_plugin_info(name)

        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        ch.heading(f"Plugin: {info.name}")
        ch.dim(f"Version: {info.version}")
        ch.dim(f"Source: {info.source} ({info.path})")
        ch.dim(f"Description: {info.description or '(no description)'}")
        ch.dim("")

        if info.loaded:
            ch.success("Status: Loaded and active")
        elif not info.enabled:
            ch.warning("Status: Disabled")
            ch.dim("Enable with: navig plugin enable " + name)
        elif info.error:
            ch.error("Status: Failed to load", info.error)

        if info.missing_deps:
            ch.dim("")
            ch.warning("Missing dependencies:")
            for dep in info.missing_deps:
                ch.dim(f"  • {dep}")
            ch.dim("")
            ch.dim("Install with: pip install " + " ".join(info.missing_deps))

    @plugin_app.command("enable")
    def plugin_enable(
        name: str = typer.Argument(..., help="Plugin name to enable"),
    ):
        """Enable a disabled plugin."""
        from navig.config import get_config_manager
        from navig.plugins import get_plugin_manager

        config = get_config_manager()
        manager = get_plugin_manager()

        info = manager.get_plugin_info(name)
        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        for plugin_name in _plugin_identifiers(info):
            config.enable_plugin(plugin_name)

        ch.success(f"Plugin '{info.name}' enabled")
        ch.dim("Restart NAVIG to load the plugin")

    @plugin_app.command("disable")
    def plugin_disable(
        name: str = typer.Argument(..., help="Plugin name to disable"),
    ):
        """Disable a plugin (without uninstalling)."""
        from navig.config import get_config_manager
        from navig.plugins import get_plugin_manager

        config = get_config_manager()
        manager = get_plugin_manager()

        info = manager.get_plugin_info(name)
        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        for plugin_name in _plugin_identifiers(info):
            config.disable_plugin(plugin_name)

        ch.success(f"Plugin '{info.name}' disabled")
        ch.dim("Restart NAVIG to unload the plugin")

    @plugin_app.command("install")
    def plugin_install(
        path: str = typer.Argument(..., help="Path to plugin directory or Git URL"),
    ):
        """Install a plugin from local path or Git URL."""
        import os
        from pathlib import Path

        from navig.config import get_config_manager

        config = get_config_manager()
        source_path = Path(path).expanduser()

        if source_path.exists() and source_path.is_dir():
            try:
                source_path = source_path.resolve(strict=True)
            except OSError as exc:
                ch.error("Invalid plugin path", str(exc))
                raise typer.Exit(1) from exc

            if source_path.is_symlink():
                ch.error(
                    "Invalid plugin path",
                    "Plugin source directories cannot be symbolic links.",
                )
                raise typer.Exit(1)

            linked_entry = next(
                (entry for entry in source_path.rglob("*") if entry.is_symlink()), None
            )
            if linked_entry is not None:
                ch.error(
                    "Invalid plugin path",
                    f"Plugin source contains a symbolic link: {linked_entry}",
                )
                raise typer.Exit(1)

            # Local directory installation
            plugin_file = source_path / "plugin.py"
            if not plugin_file.exists():
                ch.error("Invalid plugin", "Directory must contain plugin.py")
                raise typer.Exit(1)

            plugin_name = os.path.basename(str(source_path.resolve(strict=False)))

            # P1-9: Validate plugin name — prevent path traversal via crafted names
            import re as _re

            if plugin_name in {"", ".", ".."} or not _re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]*", plugin_name
            ):
                ch.error(
                    f"Invalid plugin name: '{plugin_name}'",
                    "Plugin names must contain only letters, digits, underscores, or hyphens.",
                )
                raise typer.Exit(1)

            dest_root = config.plugins_dir.resolve()
            dest_path = (dest_root / plugin_name).resolve()

            try:
                dest_path.relative_to(dest_root)
            except ValueError:
                ch.error(
                    "Invalid plugin destination",
                    "Resolved plugin path escapes the NAVIG plugins directory.",
                )
                raise typer.Exit(1) from None

            if dest_path.exists():
                ch.error(f"Plugin '{plugin_name}' already exists")
                ch.dim(f"Remove it first: rm -rf {dest_path}")
                raise typer.Exit(1)

            # Copy plugin to user plugins directory
            shutil.copytree(source_path, dest_path)
            ch.success(f"Installed plugin '{plugin_name}' to {dest_path}")
            ch.dim("Restart NAVIG to load the plugin")

        elif path.startswith(("http://", "https://", "git@")):
            # Git URL installation
            ch.error("Git URL installation not yet implemented")
            ch.dim(
                "Clone the repository manually and use: navig plugin install ./path/to/plugin"
            )
            raise typer.Exit(1)

        else:
            ch.error(f"Invalid path: {path}")
            ch.dim("Provide a local directory path or Git URL")
            raise typer.Exit(1)

    @plugin_app.command("uninstall")
    def plugin_uninstall(
        name: str = typer.Argument(..., help="Plugin name to uninstall"),
        force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    ):
        """Uninstall a user-installed plugin."""
        import shutil

        from navig.plugins import get_plugin_manager

        manager = get_plugin_manager()

        info = manager.get_plugin_info(name)
        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        if info.source == "builtin":
            ch.error("Cannot uninstall built-in plugins")
            ch.dim("You can disable it instead: navig plugin disable " + name)
            raise typer.Exit(1)

        if not force:
            confirm = typer.confirm(f"Uninstall plugin '{info.name}'?")
            if not confirm:
                raise typer.Abort()

        shutil.rmtree(info.path)
        ch.success(f"Uninstalled plugin '{info.name}'")

    # Register plugin commands
    app.add_typer(plugin_app, name="plugin")


def main() -> None:
    """
    NAVIG CLI entry point.

    1. Import and use the existing CLI app from navig.cli
    2. Discover and load plugins
    3. Add plugin management commands
    4. Run the CLI
    """
    try:
        from navig.config import reset_config_manager, set_config_cache_bypass
        from navig.core.crash_handler import crash_handler

        # Check for debug flag early to configure handler
        if "--debug" in sys.argv:
            crash_handler.enable_debug()
            # We don't remove it from argv so Typer can still see it if needed,
            # but usually Typer handles its own parsing.

        no_cache_requested = "--no-cache" in sys.argv
        set_config_cache_bypass(no_cache_requested)
        if no_cache_requested:
            reset_config_manager()

        # Compatibility normalization for legacy help syntaxes.
        sys.argv = _normalize_help_compat_args(sys.argv)

        # Fast-path: handle --version / -v / --help / bare invocation without
        # importing the full CLI.  Must run BEFORE first-run onboarding so that
        # these flags always work on a fresh install on every platform.
        if _maybe_handle_fast_path(sys.argv):
            return

        # First-run onboarding — fires when ~/.navig/onboarding.json is absent.
        # Runs after the fast-path so that -v / --version / --help are never
        # blocked by the onboarding wizard (macOS and other platforms included).
        _check_first_run()

        # Import the existing CLI app (maintains all current functionality)
        from navig.cli import _register_external_commands, app

        # Register all external command sub-apps (deferred from module load)
        _register_external_commands()

        skip_plugins = _should_skip_plugin_loading(sys.argv)

        # Load plugins only when necessary (fast-path for help/version)
        if not skip_plugins:
            load_plugins_into_app(app)
            # Add plugin management commands only when plugin system is active.
            # This avoids importing rich/console_helper during `navig --help`.
            add_plugin_commands(app)

            # Auto-load packages listed in ~/.navig/packages_autoload.json
            try:
                from navig.commands.package import autoload_packages

                autoload_packages()
            except Exception as _e:  # noqa: BLE001
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "autoload_packages() failed (non-critical): %s", _e
                )

        # Run the CLI
        app()

    except KeyboardInterrupt:
        _eprint("\n[dim]Interrupted[/dim]")
        sys.exit(130)
    except SystemExit as e:
        # Catch Typer/Click parsing errors that indicate PowerShell mangled the input
        if e.code != 0 and len(sys.argv) >= 2:
            _handle_powershell_parsing_error(sys.argv)
        # "Did you mean?" suggestions for misspelled top-level commands
        if e.code == 2 and len(sys.argv) >= 2:
            from navig.cli.registration import extract_non_global_tokens

            command_tokens = extract_non_global_tokens(sys.argv[1:])
            if command_tokens:
                _suggest_did_you_mean(command_tokens[0])
        raise
    except Exception as e:
        # Use our robust crash handler
        from navig.core.crash_handler import crash_handler

        crash_handler.handle_exception(e)


def _suggest_did_you_mean(unknown: str) -> None:
    """Print 'Did you mean?' suggestions for misspelled top-level commands."""
    # _BUILTIN_COMMANDS is the authoritative set; add non-plugin meta-commands here.
    _KNOWN_COMMANDS = _BUILTIN_COMMANDS | {"plugin", "help"}
    try:
        from navig.cli.recovery import did_you_mean

        suggestions = did_you_mean(unknown, list(_KNOWN_COMMANDS))
        if suggestions:
            _eprint("\n[yellow]Did you mean?[/yellow]")
            for s in suggestions[:3]:
                _eprint(f"  navig {s}")
    except Exception as _e:
        _log.debug("did-you-mean suggestion failed: %s", _e)


def _handle_powershell_parsing_error(argv: list[str]) -> None:
    """Detect if PowerShell mangled the command and provide helpful guidance.

    This catches errors BEFORE navig even parses the command, when PowerShell
    breaks the arguments due to special characters.
    """
    import os

    from navig.cli.registration import extract_non_global_tokens

    # Only help if this looks like a 'navig run' command
    _ps_cmd_tokens = extract_non_global_tokens(argv[1:])
    if not _ps_cmd_tokens or _ps_cmd_tokens[0] not in ["run", "r"]:
        return

    # Detect PowerShell environment
    is_powershell = False
    if sys.platform == "win32":
        is_powershell = True
        if os.environ.get("PROMPT"):
            is_powershell = False
    elif "powershell" in os.environ.get("TERM_PROGRAM", "").lower():
        is_powershell = True

    if not is_powershell:
        return

    # Join the arguments that were passed TO the run command.
    # Use _ps_cmd_tokens[1:] (already global-flag-stripped) rather than the
    # raw argv[2:], which would include global flag *values* (e.g. the hostname
    # from --host prod) and could produce false positives when the hostname
    # itself contains odd quote counts or backslashes.
    attempted_cmd = " ".join(_ps_cmd_tokens[1:])

    # Check for signs PowerShell mangled it (backslash-escaped quotes, broken strings)
    powershell_mangled = any(
        [
            '\\"' in attempted_cmd,
            "\\'" in attempted_cmd,
            attempted_cmd.count('"') % 2 != 0,  # Odd number of quotes
            attempted_cmd.count("'") % 2 != 0,
        ]
    )

    if not powershell_mangled:
        return

    # Show helpful guidance
    sys.stderr.write("\n")
    sys.stderr.write("[!] PowerShell Quoting Error Detected\n")
    sys.stderr.write("-" * 70 + "\n\n")
    sys.stderr.write("PowerShell broke your command before it reached navig.\n")
    sys.stderr.write(
        "Special characters like quotes, parentheses, and braces cause this.\n\n"
    )
    sys.stderr.write("Solution 1: Use stdin (recommended)\n\n")
    sys.stderr.write("  @'\n")
    sys.stderr.write("  your complex command here\n")
    sys.stderr.write("  '@ | navig run --b64 --stdin\n\n")
    sys.stderr.write("Solution 2: Save to file\n\n")
    sys.stderr.write("  @'\n")
    sys.stderr.write("  your complex command here\n")
    sys.stderr.write("  '@ | Out-File cmd.txt\n")
    sys.stderr.write("  navig run --b64 --file cmd.txt\n\n")
    sys.stderr.write("Solution 3: Interactive editor\n\n")
    sys.stderr.write("  navig run -i\n\n")
    sys.stderr.write(
        "Tip: PowerShell here-strings @'...'@ preserve everything exactly.\n"
    )
    sys.stderr.write("-" * 70 + "\n\n")


if __name__ == "__main__":
    main()
