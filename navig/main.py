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
    try:
        from navig.commands.dashboard import run_dashboard, run_dashboard_simple
    except Exception as exc:
        _log.debug("Dashboard unavailable: %s — falling back to full CLI", exc)
        return False

    skip_boot = "--fast" in extra_args
    simple = "--simple" in extra_args

    if simple:
        run_dashboard_simple()
    else:
        run_dashboard(skip_boot=skip_boot)
    return True


def _has_help_page(topic: str) -> bool:
    """Return True when a rich in-app help page (``navig/help/<topic>.md``) exists.

    Used by :func:`_normalize_help_compat_args` to keep ``navig help <topic>``
    on the in-app help command (which renders the markdown guide) instead of
    rewriting it to ``navig <topic> --help``.

    ``index`` is excluded on purpose: ``index.md`` is the bare-``navig help``
    landing page, not a guide for the ``navig index`` command — the rewrite to
    ``navig index --help`` must keep winning there.
    """
    name = topic.strip().lower()
    if not name or name in {"index", "readme"}:
        return False
    # Only plain topic tokens may touch the filesystem (no path separators).
    if not all(ch.isalnum() or ch in "-_" for ch in name):
        return False
    return (Path(__file__).resolve().parent / "help" / f"{name}.md").is_file()


def _normalize_help_compat_args(argv: list[str]) -> list[str]:
    """Normalize legacy help forms to canonical ``--help``.

    Compatibility rules (best-effort):
    - ``navig <path> help`` -> ``navig <path> --help``
    - ``navig <path> -h``   -> ``navig <path> --help`` (only when ``-h`` is trailing)
    - ``navig help <cmd>``  -> ``navig <cmd> --help``   (leading help rewrite,
      ONLY when no ``navig/help/<cmd>.md`` guide exists — see
      :func:`_has_help_page`; topics with a guide stay on ``navig help``)

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
    # …EXCEPT when a rich in-app guide exists (navig/help/<topic>.md): those
    # route to the `help` command so the markdown page renders instead of
    # Typer's flag dump. Topics without a page keep the legacy rewrite, so
    # `navig <cmd> --help` behaviour is unchanged for every other command.
    if non_global_tokens[0] == "help" and len(non_global_tokens) >= 2:
        if len(non_global_tokens) == 2 and _has_help_page(non_global_tokens[1]):
            return argv
        normalized = list(argv)
        help_arg_index = 1 + non_global_positions[0]
        del normalized[help_arg_index]
        normalized.append("--help")
        return normalized

    # Legacy alias: `navig memory list` -> `navig memory sessions`
    if len(non_global_tokens) >= 2 and non_global_tokens[0] == "memory" and non_global_tokens[1] == "list":
        rewritten = list(argv)
        list_arg_index = 1 + non_global_positions[1]
        rewritten[list_arg_index] = "sessions"
        args = rewritten[1:]
        argv = rewritten

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


def _note_terminal_exit_code(code: object | None) -> None:
    """Report the process's terminal exit code to the operation ledger.

    The atexit operation-completion handler cannot see the exit code itself
    (``sys.exc_info()`` is empty during interpreter shutdown), so main() — the
    single point every invocation exits through — hands it over here first.
    Wrapped so a recording hiccup can NEVER change the real process exit code.
    """
    try:
        from navig.cli.middleware import note_exit_code

        note_exit_code(code)
    except Exception as exc:  # noqa: BLE001 — recording must never mask the exit
        _log.debug("note_exit_code failed: %s", exc)


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


def _run_startup_migrations() -> None:
    """Run one-time workspace migrations and guard against stale registrations.

    Safe to call on every invocation — both helpers are idempotent.
    Failures are logged at DEBUG level and never crash the CLI.
    """
    try:
        from pathlib import Path as _Path

        from navig.migrations.workspace_to_spaces import (
            ensure_no_stale_spaces_registration,
            migrate_workspace_to_spaces,
        )

        migrate_workspace_to_spaces(_Path.home() / ".navig", notify=lambda _m: None)
        ensure_no_stale_spaces_registration()
    except Exception as exc:  # never crash main on migration failure
        import logging as _logging

        _logging.getLogger(__name__).debug("Startup migration skipped: %s", exc)


def _check_first_run() -> None:
    """Trigger onboarding on first run if ~/.navig/onboarding.json is absent.

    Safe to call on every invocation — returns immediately if already done.
    Non-TTY environments (CI, scripts) auto-skip interactive Phase 2 steps
    via _tty_check() guards already built into each Phase 2 step.

    Output contract: stdout belongs to the OUTPUT of the command about to
    dispatch; the wizard is pre-command narration, so everything it prints is
    routed to stderr. Terminals still show it; pipes and ``--json`` consumers
    stay clean. (``--json`` invocations additionally skip onboarding entirely
    — see should_auto_run_onboarding.)

    Opt-out: set NAVIG_SKIP_ONBOARDING=1 in the environment.
    """
    try:
        import contextlib

        from navig.onboarding.runner import (
            run_engine_onboarding,
            should_auto_run_onboarding,
        )

        if not should_auto_run_onboarding(sys.argv):
            return

        # One seam covers every write site: Rich consoles (console_helper's
        # global one included) resolve sys.stdout at print time, so the
        # banner, per-step progress, step output, and the verification
        # dashboard all land on stderr. stdin is untouched — interactive
        # prompts still work. Explicit `navig onboard` / `navig init` are NOT
        # wrapped: there the wizard IS the command's output.
        with contextlib.redirect_stdout(sys.stderr):
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
    "log", "l", "logs",
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
    "repo",
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
    "plugin", "plugins",
    "hub", "connections",
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
    # (`plugin` management is itself built-in now — commands/plugin.py.)
    if args[0] in _BUILTIN_COMMANDS:
        return True

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



def main() -> None:
    """
    NAVIG CLI entry point.

    1. Import and use the existing CLI app from navig.cli
    2. Discover and load plugins
    3. Add plugin management commands
    4. Run the CLI
    """
    # On Windows the console may use a legacy code page (e.g. cp1251) that
    # cannot encode emoji used in NAVIG's output.  Reconfigure stdout/stderr
    # to UTF-8 so rich output and gateway banners render correctly.
    if sys.platform == "win32":
        for _stream in (sys.stdout, sys.stderr):
            if hasattr(_stream, "reconfigure"):
                try:
                    _stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    try:
        # Compatibility normalization for legacy help syntaxes.
        sys.argv = _normalize_help_compat_args(sys.argv)

        # Fast-path: handle --version / -v / --help / bare invocation WITHOUT
        # importing config (pyyaml + the core.* managers) or the full CLI. This
        # keeps `navig help` / `navig --version` on the sub-50ms lightweight-core
        # path. Must run BEFORE first-run onboarding so these flags always work
        # on a fresh install on every platform.
        if _maybe_handle_fast_path(sys.argv):
            return

        # Past the fast-path this is a real command, so now pull in config +
        # crash handling. Deferred on purpose: everything below is the heaviest
        # thing on the help/version path, and the fast-path above needs none of
        # it — importing it eagerly would tax every `navig help` invocation.
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

        # First-run onboarding — fires when ~/.navig/onboarding.json is absent.
        # Runs after the fast-path so that -v / --version / --help are never
        # blocked by the onboarding wizard (macOS and other platforms included).
        _check_first_run()

        # One-time workspace→spaces migration and stale-registration guard.
        # Both helpers are idempotent; failures are swallowed at DEBUG level.
        _run_startup_migrations()

        # Bind the process cwd to the active workshop (CLI only): the active
        # space *is* the working directory, so an agent's relative file/shell
        # ops land inside the space — not wherever the CLI was launched. The
        # daemon serves many spaces concurrently and must NEVER chdir (its file
        # tools resolve per-request via the session ContextVar instead).
        try:
            import os as _os  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            if not any(tok in ("gateway", "daemon") for tok in sys.argv[1:3]):
                from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

                # Remember where the user actually invoked the CLI (before we chdir
                # to the active space) so directory-aware commands like `navig menu`
                # can operate on the folder the user is standing in, not the space.
                _os.environ["NAVIG_INVOCATION_CWD"] = str(_Path.cwd())

                _wd = get_active_working_dir()
                if _wd and _wd.is_dir() and _wd.resolve() != _Path.cwd().resolve():
                    _os.chdir(_wd)
        except Exception:  # noqa: BLE001
            pass  # best-effort; never block the CLI

        # Import the existing CLI app (maintains all current functionality)
        from navig.cli import _register_external_commands, app

        # Register all external command sub-apps (deferred from module load)
        _register_external_commands()

        skip_plugins = _should_skip_plugin_loading(sys.argv)

        # Load plugins only when necessary (fast-path for help/version).
        # Plugin MANAGEMENT (`navig plugin …`) is a normal external command now
        # (commands/plugin.py via _EXTERNAL_CMD_MAP) — no discovery needed for it.
        if not skip_plugins:
            load_plugins_into_app(app)

        # Run the CLI
        app()

    except KeyboardInterrupt:
        # Record the cancel for the operation ledger BEFORE the process exits —
        # atexit can't observe the exit code itself (see middleware.note_exit_code).
        _note_terminal_exit_code(130)
        _eprint("\n[dim]Interrupted[/dim]")
        sys.exit(130)
    except SystemExit as e:
        # The one chokepoint every real command leaves through — success is
        # SystemExit(0), typer.Exit(n)/usage errors are SystemExit(n). Report it
        # to the ledger before re-raising so the atexit completer records the
        # truthful status (a non-zero exit is NOT a success).
        _note_terminal_exit_code(e.code)
        # Catch Typer/Click parsing errors that indicate PowerShell mangled the input
        if e.code != 0 and len(sys.argv) >= 2:
            _handle_powershell_parsing_error(sys.argv)
        # Unknown command: (1) a plugin may provide it → suggest + one-key
        # activate; (2) otherwise "did you mean" for misspellings.
        # Click exits 2 for EVERY usage error — including a bad flag on a real,
        # registered command — so only suggest when the command itself is
        # unknown (never registered on the app), else `navig github --badflag`
        # would masquerade as a missing-plugin hint.
        if e.code == 2 and len(sys.argv) >= 2:
            from navig.cli.registration import extract_non_global_tokens

            command_tokens = extract_non_global_tokens(sys.argv[1:])
            # `app` is imported inside the try above — unbound if we exited
            # before that import, hence locals() rather than a bare name.
            cli_app = locals().get("app")
            if command_tokens and not _is_registered_command(cli_app, command_tokens[0]):
                handled = False
                try:
                    from navig.cli.providers import suggest_provider

                    handled = suggest_provider(command_tokens[0], sys.argv)
                except SystemExit:
                    raise  # re-run of the activated command — propagate its code
                except Exception:  # noqa: BLE001 — suggestion must never mask exit
                    handled = False
                if not handled:
                    _suggest_did_you_mean(command_tokens[0])
        raise
    except Exception as e:
        # A missing optional plugin is a CONFIGURATION state, not a bug — routing it
        # to the crash handler filed a crash report (and dumped a traceback) for what
        # only needs one install line. Resolved HERE rather than as an `except
        # PluginRequired` clause on purpose: that would need a module-level import of
        # navig.plugins, whose package __init__ pulls in console_helper → rich, on
        # every single `navig` invocation. This path only runs once we're already
        # crashing, so the import cost is free.
        from navig.plugins.require import PluginRequired

        if isinstance(e, PluginRequired):
            _note_terminal_exit_code(2)
            _eprint(f"[red]✗[/red] {e}")
            _eprint(f"  [dim]Install it:[/dim]  {e.hint}")
            sys.exit(2)

        # Use our robust crash handler (it calls sys.exit(1)).
        _note_terminal_exit_code(1)
        from navig.core.crash_handler import crash_handler

        crash_handler.handle_exception(e)


def _is_registered_command(app, name: str) -> bool:
    """True if *name* is a command/group actually registered on the typer app.

    Used to tell "unknown command" (suggest a provider) apart from "usage error
    inside a known command" (click exits 2 for both).
    """
    if app is None:
        return False
    try:
        for group in app.registered_groups:
            if group.name == name:
                return True
        for cmd in app.registered_commands:
            cmd_name = cmd.name or (
                cmd.callback.__name__.replace("_", "-") if cmd.callback else ""
            )
            if cmd_name == name:
                return True
    except Exception:  # noqa: BLE001 — never break exit handling over a lookup
        pass
    return False


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
