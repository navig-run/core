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

import sys
from typing import Dict, List


def _fast_help_text(version: str) -> str:
    # Keep this ASCII-only so it works on Windows default consoles.
    return "\n".join(
        [
            f"NAVIG v{version}  Server Management CLI",
            "",
            "Usage:",
            "  navig <command> [options]",
            "",
            "Common commands:",
            "  init        Initialize NAVIG configuration",
            "  menu        Interactive mode",
            "  host        Manage remote server connections",
            "  app         Manage applications",
            "  run         Run a remote command",
            "  file        File transfer and remote editing",
            "  db          Database operations",
            "  backup      Backup and restore NAVIG configuration",
            "  tunnel      Manage SSH tunnels",
            "  docker      Docker containers",
            "  web         Web server operations",
            "  service     Manage NAVIG daemon (persistent background service)",
            "  profile     Operating profile (node | builder | operator | architect)",
            "  mcp         MCP tools and integrations",
            "  help        In-app help topics",
            "",
            "Examples:",
            "  navig init",
            "  navig host add",
            "  navig host use",
            "  navig run 'uname -a'",
            "  navig help db",
            "",
            "Tip:",
            "  Use 'navig <command> --help' for command details.",
        ]
    )


def _maybe_handle_fast_path(argv: List[str]) -> bool:
    """Handle ultra-fast invocations without importing the full CLI.

    We only intercept cases where Typer would show top-level help/version,
    not subcommand help (e.g. `navig host --help`).

    Returns True if handled.
    """
    args = [a for a in argv[1:] if a]

    if not args:
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    if len(args) == 1 and args[0] in {"--help"}:
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    # `navig help` with no topic → same as `navig --help`
    if len(args) == 1 and args[0] == "help":
        from navig import __version__

        sys.stdout.write(_fast_help_text(__version__) + "\n")
        return True

    if len(args) == 1 and args[0] in {"--version"}:
        from navig import __version__

        sys.stdout.write(__version__ + "\n")
        return True

    # `navig start` — alias for `navig dashboard` (Kraken TUI)
    if args and args[0] == "start":
        return _handle_start_command(args[1:])

    return False


def _handle_start_command(extra_args: List[str]) -> bool:
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
    import os
    from pathlib import Path

    # Explicit opt-out for CI and scripted environments
    if os.getenv("NAVIG_SKIP_ONBOARDING") == "1":
        return

    # Shell-completion probes — must never block or print
    if any(
        v in os.environ for v in ("_NAVIG_COMPLETE", "COMP_WORDS", "_TYPER_COMPLETE")
    ):
        return

    navig_dir = Path.home() / ".navig"

    # Primary completion signal: the engine writes this artifact when done
    if (navig_dir / "onboarding.json").exists():
        return

    # Avoid double-run when the user explicitly invokes onboarding sub-commands
    _SKIP_CMDS = {"onboard", "quickstart", "service", "update", "version"}
    if any(cmd in sys.argv[1:2] for cmd in _SKIP_CMDS):
        return

    try:
        import socket

        from navig.onboarding import EngineConfig, OnboardingEngine
        from navig.onboarding.genesis import load_or_create
        from navig.onboarding.steps import build_step_registry

        cfg = EngineConfig(
            navig_dir=navig_dir,
            node_name=socket.gethostname(),
        )
        genesis = load_or_create(navig_dir, name=socket.gethostname())
        steps = build_step_registry(cfg, genesis)
        engine = OnboardingEngine(cfg, steps)

        sys.stdout.write("\n  Welcome to NAVIG — running first-time setup.\n")
        sys.stdout.write("  Set NAVIG_SKIP_ONBOARDING=1 to skip.\n\n")
        sys.stdout.flush()

        engine.run()

        sys.stdout.write("\n  Setup complete. Run 'navig --help' to get started.\n\n")
        sys.stdout.flush()
    except Exception as exc:  # never crash main on onboarding failure
        _eprint(f"[dim]First-run setup skipped: {exc}[/dim]")


# Track plugin state for status command
_loaded_plugins: List[str] = []
_failed_plugins: List[Dict[str, str]] = []


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


def _should_skip_plugin_loading(argv: List[str]) -> bool:
    """Return True when plugin loading should be skipped for fast startup.

    We skip plugin discovery for commands that only need core CLI wiring:
    - no args (shows compact help)
    - --help / --version
    - navig help (in-app help)
    - core built-in commands that never need plugins

    This keeps cold start fast while preserving full functionality for
    real operational commands.
    """
    args = [a for a in argv[1:] if a]  # strip program name

    if not args:
        return True

    if any(a in {"--help", "--version"} for a in args):
        return True

    # In-app help command.
    if args and args[0] == "help":
        return True

    # Core built-in commands that never use plugins.
    # Skipping plugins saves ~110ms on these hot paths.
    _PLUGIN_FREE = {
        "host",
        "h",
        "app",
        "a",
        "run",
        "r",
        "db",
        "database",
        "file",
        "f",
        "docker",
        "tunnel",
        "t",
        "web",
        "backup",
        "config",
        "status",
        "version",
        "log",
        "local",
        "mcp",
        "profile",
        "security",
        "monitor",
        "index",
        "skills",
        "skill",
        "flow",
        "workflow",
        "wiki",
        "scaffold",
        "migrate",
        "server-template",
        "template",
        "hestia",
    }
    if args[0] in _PLUGIN_FREE:
        return True

    if args[0] == "plugin":
        return False

    # Check the plugin cache to short-circuit invalid commands or fast paths
    try:
        import json
        from pathlib import Path

        # We can't easily import Config here without overhead, so we construct the default path
        cache_file = Path.home() / ".navig" / "data" / "plugins_cache.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            plugins = cached_data.get("plugins", {})
            if args[0] not in plugins:
                return True
    except json.JSONDecodeError:
        # P1-6: Corrupted plugin cache — log a warning instead of silently ignoring
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Plugin cache corrupted at %s — skipping cache check",
            Path.home() / ".navig" / "data" / "plugins_cache.json",
        )
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "Plugin check exception: %s", e, exc_info=True
        )
        pass

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

        for name, info in plugins.items():
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
                name,
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
        from navig.core import Config
        from navig.plugins import get_plugin_manager

        config = Config()
        manager = get_plugin_manager()

        info = manager.get_plugin_info(name)
        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        config.enable_plugin(name)
        ch.success(f"Plugin '{name}' enabled")
        ch.dim("Restart NAVIG to load the plugin")

    @plugin_app.command("disable")
    def plugin_disable(
        name: str = typer.Argument(..., help="Plugin name to disable"),
    ):
        """Disable a plugin (without uninstalling)."""
        from navig.core import Config
        from navig.plugins import get_plugin_manager

        config = Config()
        manager = get_plugin_manager()

        info = manager.get_plugin_info(name)
        if not info:
            ch.error(f"Plugin '{name}' not found")
            raise typer.Exit(1)

        config.disable_plugin(name)
        ch.success(f"Plugin '{name}' disabled")
        ch.dim("Restart NAVIG to unload the plugin")

    @plugin_app.command("install")
    def plugin_install(
        path: str = typer.Argument(..., help="Path to plugin directory or Git URL"),
    ):
        """Install a plugin from local path or Git URL."""
        import shutil
        from pathlib import Path

        from navig.core import Config

        config = Config()
        source_path = Path(path)

        if source_path.exists() and source_path.is_dir():
            # Local directory installation
            plugin_file = source_path / "plugin.py"
            if not plugin_file.exists():
                ch.error("Invalid plugin", "Directory must contain plugin.py")
                raise typer.Exit(1)

            plugin_name = source_path.name

            # P1-9: Validate plugin name — prevent path traversal via crafted names
            import re as _re

            if not _re.match(r"^[a-zA-Z0-9_-]+$", plugin_name):
                ch.error(
                    f"Invalid plugin name: '{plugin_name}'",
                    "Plugin names must contain only letters, digits, underscores, or hyphens.",
                )
                raise typer.Exit(1)

            dest_path = config.plugins_dir / plugin_name

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

        from navig.core import Config
        from navig.plugins import get_plugin_manager

        config = Config()
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
            confirm = typer.confirm(f"Uninstall plugin '{name}'?")
            if not confirm:
                raise typer.Abort()

        shutil.rmtree(info.path)
        ch.success(f"Uninstalled plugin '{name}'")

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
        from navig.core.crash_handler import crash_handler

        # Check for debug flag early to configure handler
        if "--debug" in sys.argv:
            crash_handler.enable_debug()
            # We don't remove it from argv so Typer can still see it if needed,
            # but usually Typer handles its own parsing.

        # First-run onboarding — fires when ~/.navig/onboarding.json is absent.
        # Placed before the fast-path so bare `navig` on a fresh install shows
        # the welcome wizard rather than just help text.
        _check_first_run()

        if _maybe_handle_fast_path(sys.argv):
            return

        # Import the existing CLI app (maintains all current functionality)
        from navig.cli import _register_external_commands, app

        # Register all external command sub-apps (deferred from module load)
        _register_external_commands()

        # Register operating profile command (node/builder/operator/architect)
        try:
            from navig.commands.profile import profile_app

            app.add_typer(profile_app, name="profile")
        except Exception as e:  # never break startup
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "Failed to load profile sub-app: %s", e, exc_info=True
            )
            pass

        skip_plugins = _should_skip_plugin_loading(sys.argv)

        # Load plugins only when necessary (fast-path for help/version)
        if not skip_plugins:
            load_plugins_into_app(app)
            # Add plugin management commands only when plugin system is active.
            # This avoids importing rich/console_helper during `navig --help`.
            add_plugin_commands(app)

        # Run the CLI
        app()

    except KeyboardInterrupt:
        _eprint("\n[dim]Interrupted[/dim]")
        sys.exit(130)
    except SystemExit as e:
        # Catch Typer/Click parsing errors that indicate PowerShell mangled the input
        if e.code != 0 and len(sys.argv) >= 2:
            _handle_powershell_parsing_error(sys.argv)
        raise
    except Exception as e:
        # Use our robust crash handler
        from navig.core.crash_handler import crash_handler

        crash_handler.handle_exception(e)


def _handle_powershell_parsing_error(argv: List[str]) -> None:
    """Detect if PowerShell mangled the command and provide helpful guidance.

    This catches errors BEFORE navig even parses the command, when PowerShell
    breaks the arguments due to special characters.
    """
    import os

    # Only help if this looks like a 'navig run' command
    if len(argv) < 2 or argv[1] not in ["run", "r"]:
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

    # Join all args to see the original attempted command
    attempted_cmd = " ".join(argv[2:]) if len(argv) > 2 else ""

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
