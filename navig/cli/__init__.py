"""
NAVIG CLI - No Admin Visible In Graveyard

Keep your servers alive. Forever.

Performance note: Heavy imports (TunnelManager, RemoteOperations, AIAssistant)
are deferred until actually needed to improve CLI startup time.
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import typer

from navig import __version__ as __version__
from navig.deprecation import deprecation_warning
from navig.lazy_loader import lazy_import

# Lazy-load console helper (imports rich.*). This keeps `navig --help` fast.
ch = lazy_import("navig.console_helper")


def _force_utf8_stdio() -> None:
    """Force UTF-8 stdio on Windows to prevent UnicodeEncodeError.

    NAVIG prints Unicode (emoji, checkmarks) via rich/console helper. On some
    Windows setups, stdout/stderr default to a legacy code page (e.g. cp1252),
    which crashes on characters like '✓'.
    """

    if os.name != "nt":
        return

    # Ensure Python prefers UTF-8 for stdio.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    # Reconfigure stdio streams when supported (Python 3.7+ TextIOBase).
    for stream_name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, stream_name, None)
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Never fail CLI startup due to console encoding adjustments.
            pass


_force_utf8_stdio()

import threading as _threading

# ---------------------------------------------------------------------------
# Config manager
# ---------------------------------------------------------------------------
# Delegate to navig.config.get_config_manager() — the single source of truth.
# Removing a duplicate module-level cache means navig.config.reset_config_manager()
# correctly clears the instance for tests and --no-cache without needing a
# second flush of cli.__init__._config_manager.
# ---------------------------------------------------------------------------
_NO_CACHE = False
_log = logging.getLogger(__name__)


def _get_config_manager():
    """Return the process-wide ConfigManager from navig.config."""
    from navig.config import get_config_manager

    return get_config_manager()


# ---------------------------------------------------------------------------
# Heavy-class lazy references
# ---------------------------------------------------------------------------
# Each class gets its own lock so a slow import of one cannot block the others.
# These store the *class* (not an instance) for deferred construction.
# ---------------------------------------------------------------------------
_TunnelManager = None
_RemoteOperations = None
_AIAssistant = None
_tunnel_lock = _threading.Lock()
_remote_lock = _threading.Lock()
_ai_lock = _threading.Lock()


def _get_tunnel_manager():
    """Lazy-load TunnelManager class (thread-safe)."""
    global _TunnelManager
    if _TunnelManager is None:
        with _tunnel_lock:
            if _TunnelManager is None:
                from navig.tunnel import TunnelManager

                _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations():
    """Lazy-load RemoteOperations class (thread-safe)."""
    global _RemoteOperations
    if _RemoteOperations is None:
        with _remote_lock:
            if _RemoteOperations is None:
                from navig.remote import RemoteOperations

                _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant():
    """Lazy-load AIAssistant class (thread-safe)."""
    global _AIAssistant
    if _AIAssistant is None:
        with _ai_lock:
            if _AIAssistant is None:
                from navig.ai import AIAssistant

                _AIAssistant = AIAssistant
    return _AIAssistant


# ============================================================================
# HELP & CALLBACK SYSTEM
# ============================================================================
# Callback implementations live in navig/cli/_callbacks.py — single source of truth.
# Re-exported here so ``from navig.cli import show_subcommand_help`` keeps working.
from navig.cli._callbacks import (  # noqa: E402
    _get_hacker_quotes as _get_hacker_quotes,
)
from navig.cli._callbacks import (
    _schema_callback,
    help_callback,
    show_compact_help,
    version_callback,
)
from navig.cli._callbacks import (
    make_subcommand_callback as make_subcommand_callback,
)
from navig.cli._callbacks import (
    show_subcommand_help as show_subcommand_help,
)
from navig.cli.help_dictionaries import HELP_REGISTRY as HELP_REGISTRY  # noqa: E402

# Initialize CLI app
app = typer.Typer(
    name="navig",
    help="NAVIG - Server Management CLI",
    add_completion=True,
    rich_markup_mode="rich",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    schema: bool = typer.Option(
        None,
        "--schema",
        callback=_schema_callback,
        is_eager=True,
        expose_value=False,
        help="Output machine-readable command schema as JSON and exit.",
    ),
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
    show_help: bool = typer.Option(
        None,
        "--help",
        callback=help_callback,
        is_eager=True,
        help="Show help and exit",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        "-h",
        help="Override active host for this command",
    ),
    app: str | None = typer.Option(
        None,
        "--app",
        "-p",
        help="Override active app for this command (auto-detects host if not specified)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Detailed logging output",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be done without executing",  # void: always dry-run in production
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-confirm all prompts",  # void: danger lives here
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        "-c",
        help="Force confirmation prompts even if auto mode is configured",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Output raw data (no formatting, for scripting)",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output data in JSON format for automation/scripting",
    ),
    debug_log: bool = typer.Option(
        False,
        "--debug-log",
        help="Enable debug logging to a file (run `navig paths` for its location — under the OS log dir, not ~/.navig)",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Disable local caches for this run (slower but always fresh)",
    ),
):
    """
    NAVIG - Cross-platform SSH tunnel & remote server management CLI.

    Encrypted channels. Surgical precision. No traces.
    """
    # Store global options in context for subcommands to access
    ctx.ensure_object(dict)

    global _NO_CACHE
    _NO_CACHE = bool(no_cache)
    if _NO_CACHE:
        # Invalidate navig.config's cached instance so _get_config_manager()
        # returns a fresh ConfigManager on the next call.  Restore bypass=False
        # immediately so the fresh instance gets cached (not re-created on every
        # subsequent call in this process).
        try:
            from navig.config import reset_config_manager, set_config_cache_bypass

            set_config_cache_bypass(True)   # mark: create fresh on next call
            reset_config_manager()           # drop the stale cached instance
            set_config_cache_bypass(False)  # allow the fresh instance to be cached
        except Exception as _cache_exc:  # noqa: BLE001
            _log.debug("no-cache reset failed (non-fatal): %s", _cache_exc)

    # Auto-detect host if --app is specified without --host
    # void: the system finds what you need before you know you need it
    if app and not host:
        hosts_with_app = _get_config_manager().find_hosts_with_app(app)

        if not hosts_with_app:
            ch.error(
                f"App '{app}' not found on any host",
                "Use 'navig app list --all' to see all available apps.",
            )
            raise typer.Exit(1)
        elif len(hosts_with_app) == 1:
            # Auto-select the only host with this app
            host = hosts_with_app[0]
            if not quiet:
                ch.dim(f"→ Auto-detected host: {host}")
        else:
            # Multiple hosts have this app
            # get_active_host may return a (host, source) tuple — unpack safely.
            active_host_raw = _get_config_manager().get_active_host(return_source=False)
            active_host: str | None = (
                active_host_raw[0] if isinstance(active_host_raw, tuple) else active_host_raw
            )
            default_host = _get_config_manager().global_config.get("default_host")

            # Try to use active host first, then default host
            if active_host in hosts_with_app:
                host = active_host
                if not quiet:
                    ch.dim(
                        f"→ Using active host: {host} (app '{app}' found on {len(hosts_with_app)} hosts)"
                    )
            elif default_host in hosts_with_app:
                host = default_host
                if not quiet:
                    ch.dim(
                        f"→ Using default host: {host} (app '{app}' found on {len(hosts_with_app)} hosts)"
                    )
            else:
                # Prompt user to choose
                ch.warning(
                    f"App '{app}' found on multiple hosts: {', '.join(hosts_with_app)}",
                    "Please specify which host to use with --host flag.",
                )
                raise typer.Exit(1)

    ctx.obj["host"] = host
    ctx.obj["app"] = app
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet

    # An interactive command shows WARNING and up on stderr; INFO/DEBUG stay in navig.log
    # unless --verbose. Without this the console handler defaulted to INFO for every
    # process, so loading the installed plugins printed ~25 log lines above the output of
    # `navig doctor`, `navig store status`, `navig skill list`, … on every run. The daemon
    # never passes through this callback (`-m navig.daemon.*`), and `gateway start` sets
    # its own console level after the boot story — this is the CLI's policy only.
    try:
        import logging as _logging

        from navig.core.logging import set_console_level

        set_console_level(_logging.INFO if verbose else _logging.WARNING)
    except Exception as _log_exc:  # noqa: BLE001 — a logging policy must never block a command
        _log.debug("console log level not applied: %s", _log_exc)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["yes"] = yes
    ctx.obj["confirm"] = confirm
    ctx.obj["raw"] = raw
    ctx.obj["plain"] = raw  # --raw is the global alias for --plain; initialise so subcommands inherit it
    ctx.obj["json"] = json
    ctx.obj["debug_log"] = debug_log

    # Initialize operation recorder for history tracking
    # void: every command becomes a memory. every memory becomes a lesson.
    from navig.cli.middleware import init_operation_recorder

    init_operation_recorder(ctx, host=host, app=app, verbose=verbose)

    # Initialize debug logger if enabled (via flag OR config)
    # void: every action leaves a trace. we just choose which traces to keep.
    from navig.cli.middleware import init_debug_logger

    init_debug_logger(
        ctx, debug_log=debug_log, host=host, app=app, verbose=verbose, quiet=quiet, dry_run=dry_run
    )

    # Register fact extraction handler — runs silently after every CLI invocation.
    # void: every command is a signal. we harvest meaning from routine.
    from navig.cli.middleware import register_fact_extraction

    register_fact_extraction()

    # Initialize proactive assistant if enabled
    # void: we built an AI to watch our systems. now who watches the AI?
    from navig.cli.middleware import init_proactive_assistant

    init_proactive_assistant(ctx, quiet=quiet)

    # Show compact help if no subcommand is invoked
    if ctx.invoked_subcommand is None:
        # Check if user passed a natural language query as argument
        # (navig "check disk space" should work like AI chat)
        import sys
        from pathlib import Path

        executable = Path(sys.argv[0]).name.lower() if sys.argv else ""
        if "navig" in executable:
            remaining_args = sys.argv[1:]
        else:
            # In embedded/in-process calls (e.g., pytest invoking `app([...])`),
            # host process argv is unrelated to NAVIG and must not drive NL routing.
            remaining_args = []

        from navig.cli.registration import extract_non_global_tokens

        non_flag_args = extract_non_global_tokens(remaining_args)

        if non_flag_args and not non_flag_args[0].startswith("-"):
            # User passed something like: navig "check disk space"
            # Treat as natural language query → start AI chat
            query = " ".join(non_flag_args)
            from navig.commands.chat import run_ai_chat

            run_ai_chat(query, single_query=True)
        else:
            # No args - show help
            show_compact_help()


def _run_ai_chat(initial_query: str | None = None, single_query: bool = False) -> None:
    """Run interactive AI chat — delegates to navig.commands.chat."""
    from navig.commands.chat import run_ai_chat

    run_ai_chat(initial_query, single_query=single_query)


@app.command("version")
def version_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output version in JSON format",
    ),
):
    """
    Show NAVIG version and system info.

    Examples:
        navig version
        navig version --json
    """
    from navig.commands.upgrade import run_version

    run_version(json_output=json_output)


@app.command("upgrade")
def upgrade_command(
    ctx: typer.Context,
    check: bool = typer.Option(False, "--check", "-c", help="Only check, don't install"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force reinstall even if already up-to-date"
    ),
):
    """
    Upgrade NAVIG to the latest version.

    For dev (git) installs: pulls latest commits and reinstalls.
    For regular pip installs: upgrades via uv or pip.

    Examples:
        navig upgrade            # Upgrade to latest
        navig upgrade --check    # Check if an upgrade is available
    """
    from navig.commands.upgrade import run_upgrade

    run_upgrade(check=check, force=force)


@app.command("update", hidden=True)
def update_alias(ctx: typer.Context):
    """Alias for 'navig upgrade'."""
    upgrade_command(ctx=ctx, check=False, force=False)


@app.command("help")
def help_command(
    ctx: typer.Context,
    topic: str | None = typer.Argument(
        None,
        help="Help topic (e.g., host, db, file, backup). Omit to list topics.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no rich formatting).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output help in JSON format (useful for automation).",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Output raw/plain text (no rich formatting).",
    ),
    schema_out: bool = typer.Option(
        False,
        "--schema",
        help="Output full command schema as JSON (for automation/tooling).",
    ),
):
    """In-app help system for predictable, AI-friendly help output."""
    from navig.commands.help_cmd import run_help

    run_help(ctx, topic=topic, plain=plain, json_output=json_output, raw=raw, schema_out=schema_out)


@app.command("docs")
def docs_command(
    ctx: typer.Context,
    query: str | None = typer.Argument(
        None,
        help="Search query for documentation (e.g., 'database connection', 'ssh tunnel').",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results to return.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no rich formatting).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results in JSON format.",
    ),
):
    """
    Search NAVIG documentation for relevant information.

    Searches through markdown files in the docs/ directory to find
    content relevant to your query. Useful for finding how to use
    specific features or troubleshooting issues.

    Examples:
        navig docs                      # List all documentation topics
        navig docs "ssh tunnel"         # Search for SSH tunnel info
        navig docs "database backup"    # Search for backup instructions
        navig docs --json "config"      # JSON output for automation
    """
    from navig.commands.docs_cmd import run_docs

    run_docs(ctx, query=query, limit=limit, plain=plain, json_output=json_output)


@app.command("fetch")
def fetch_command(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="URL to fetch content from"),
    mode: str = typer.Option(
        "markdown",
        "--mode",
        "-m",
        help="Extraction mode: markdown (default), text, or raw",
    ),
    max_chars: int = typer.Option(
        50000,
        "--max-chars",
        "-c",
        help="Maximum characters to extract",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        "-t",
        help="Request timeout in seconds",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no formatting)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format",
    ),
):
    """
    Fetch and extract content from a URL.

    Downloads a web page and extracts the main content, converting
    HTML to clean markdown or plain text.

    Examples:
        navig fetch https://example.com
        navig fetch https://news.ycombinator.com --mode text
        navig fetch https://docs.python.org/3/ --json
        navig fetch https://github.com/user/repo --max-chars 10000
    """
    from navig.commands.docs_cmd import run_fetch

    run_fetch(
        ctx,
        url=url,
        mode=mode,
        max_chars=max_chars,
        timeout=timeout,
        plain=plain,
        json_output=json_output,
    )


@app.command("search")
def search_command(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results",
    ),
    provider: str = typer.Option(
        "auto",
        "--provider",
        "-p",
        help="Search provider: auto, mojeek, brave, tavily, serpapi, duckduckgo, firecrawl, perplexity",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no formatting)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format",
    ),
):
    """
    Search the web for information.

    Uses Brave Search API (requires API key) or DuckDuckGo as fallback.

    Examples:
        navig search "Python best practices"
        navig search "Docker tutorial" --limit 5
        navig search "kubernetes deployment" --json
        navig search "nginx configuration" --provider duckduckgo

    Setup Brave Search:
        1. Get API key from https://brave.com/search/api/
        2. Set in config: navig config set web.search.api_key=YOUR_KEY
    """
    from navig.commands.docs_cmd import run_search

    run_search(
        ctx, query=query, limit=limit, provider=provider, plain=plain, json_output=json_output
    )


# ============================================================================
# ONBOARDING & WORKSPACE (Agent-style setup)
# ============================================================================


@app.command("onboard", hidden=True)
def onboard_command(
    ctx: typer.Context,
    flow: str = typer.Option(
        "auto",
        "--flow",
        "-f",
        help="Onboarding flow: auto, quickstart, or manual",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        "-n",
        help="Skip prompts and use defaults (for automation)",
    ),
    skip: bool = typer.Option(
        False,
        "--skip",
        "-s",
        help="Show banner then exit immediately (no setup)",
    ),
):
    """
    Interactive setup wizard for NAVIG (inspired by agentic best practices).

    Creates configuration, workspace templates, and sets up AI providers.

    Flows:
      - auto: Choose between quickstart and manual
      - quickstart: Minimal prompts, sensible defaults
      - manual: Full configuration with all options

    Examples:
        navig onboard                    # Interactive mode (choose flow)
        navig onboard --flow quickstart  # Quick setup
        navig onboard --flow manual      # Full setup
        navig onboard -n                 # Non-interactive with defaults
    """
    from navig.commands.onboard import run_onboard

    run_onboard(flow=flow, non_interactive=non_interactive, skip=skip)


@app.command("onboarding", hidden=True)
def onboarding_alias(
    ctx: typer.Context,
    flow: str = typer.Option("auto", "--flow", "-f"),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n"),
):
    """Alias for 'navig onboard' — use that instead."""
    ctx.obj = getattr(ctx, "obj", None)
    from rich.console import Console as _C

    _C().print("[yellow]Tip:[/yellow] use [bold]navig onboard[/bold] (this alias works too)")
    from navig.commands.onboard import run_onboard

    run_onboard(flow=flow, non_interactive=non_interactive)


def _init_tui_capable() -> bool:
    """Return whether current terminal supports interactive TUI onboarding."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


@app.command("init")
def init_command(
    ctx: typer.Context,
    reconfigure: bool = typer.Option(
        False,
        "--reconfigure",
        "-r",
        help="Force re-run of the setup wizard even if already configured",
    ),
    provider: bool = typer.Option(
        False,
        "--provider",
        help="Deep-link to AI Provider settings.",
    ),
    settings: bool = typer.Option(
        False,
        "--settings",
        "-s",
        help="Open the configuration status dashboard instead of the wizard",
    ),
    status: bool = typer.Option(
        False,
        "--status",
        help="Show init setup status summary",
    ),
    profile: str = typer.Option(
        "",
        "--profile",
        "-p",
        help=(
            "Run installer profile without wizard. Installation profiles: "
            "cli, minimal, server, full — legacy: quickstart, node, operator, "
            "architect, system_standard, system_deep"
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview installer actions without making any changes (use with --profile)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress installer output (use with --profile)",
    ),
    tui: bool = typer.Option(
        False,
        "--tui",
        help="Use full-screen TUI onboarding (opt-in). Falls back to CLI when unsupported.",
    ),
    messaging: bool = typer.Option(
        False,
        "--messaging",
        help="Configure messaging adapters (SMS, WhatsApp, Discord) via interactive vault wizard.",
    ),
) -> None:
    """
    State-aware NAVIG setup gateway.

    First run  → interactive setup wizard (TUI or Rich fallback)
    Return     → configuration status dashboard

    Use --profile to run the non-interactive installer pipeline instead:

        navig init --profile operator          # silent install (default profile)
        navig init --profile node --dry-run    # preview minimal setup
        navig init --profile system_standard   # + service daemon

    Installation profiles (the four `install.sh -p/--profile` names) — each one
    prints an effective-profile report under --dry-run and, when applied,
    records itself in the config key `install.profile`:

        navig init --profile cli              # CLI only, no service
        navig init --profile minimal          # CLI + shell PATH, no service
        navig init --profile server           # headless: gateway + scheduler daemon
        navig init --profile full             # everything the default install runs
        navig init --profile server --dry-run # preview the whole plan, change nothing

    Deep-links (interactive wizard only):
        navig init --provider      jump to AI-provider configuration
        navig init --settings      open settings/status overview
        navig init --reconfigure   force the wizard to re-run
        navig init --messaging     configure SMS / WhatsApp / Discord adapters

    Examples:
        navig init                # auto: wizard on first run, dashboard after
        navig init --reconfigure  # always run wizard
        navig init --provider     # configure AI provider
        navig init --messaging    # set up outbound messaging adapters
    """
    from navig.commands.init import run_init_command

    if messaging:
        from navig.commands.messaging_wizard import run_messaging_wizard

        run_messaging_wizard()
        return

    run_init_command(
        ctx,
        reconfigure=reconfigure,
        provider=provider,
        settings=settings,
        status=status,
        profile=profile,
        dry_run=dry_run,
        quiet=quiet,
        tui=tui,
        tui_capable=_init_tui_capable(),
    )


@app.command("init-rollback")
def init_rollback_command(
    last: bool = typer.Option(
        True,
        "--last/--no-last",
        help="Roll back the most recent installer run (default: True)",
    ),
    profile: str = typer.Option(
        "",
        "--profile",
        "-p",
        help="Roll back the most recent run of a specific profile",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be rolled back without making any changes",
    ),
) -> None:
    """
    Roll back the most recent installer run.

    Reloads the JSONL manifest saved by ``navig init --profile`` and
    reverses every reversible APPLIED action in reverse order.

    Examples:
        navig init-rollback                    # undo last run
        navig init-rollback --profile operator # undo last operator run
        navig init-rollback --dry-run          # preview rollback
    """
    from navig.commands.init import run_init_rollback

    run_init_rollback(last=last, profile=profile, dry_run=dry_run)


@app.command("whoami")
def whoami_command(
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Show the identity file path before rendering the sigil card",
    ),
) -> None:
    """Show your NAVIG node identity sigil card."""
    from navig.commands.whoami import run_whoami

    if debug:
        try:
            from navig.platform.paths import config_dir as navig_config_dir

            state_file = navig_config_dir() / "state" / "entity.json"
            ch.dim(f"  entity path: {state_file}")
        except Exception:  # noqa: BLE001
            pass
    run_whoami()


@app.command("settings")
def settings_command(
    key: str | None = typer.Argument(None, help="Setting key, e.g. navig.ai.provider"),
    value: str | None = typer.Argument(None, help="New value to write (triggers write mode)"),
    layer: str = typer.Option(
        "global",
        "--layer",
        "-l",
        help="Target layer: global, project, or local",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Remove key override and restore default",
    ),
    show_sources: bool = typer.Option(
        False,
        "--show-sources",
        help="Show layer file paths in the header",
    ),
) -> None:
    """
    View or edit layered NAVIG settings (VSCode-style, 5 layers).

    Examples:
        navig settings                              # show all settings
        navig settings navig.ai.provider            # inspect one key
        navig settings navig.ai.provider openai     # write to global layer
        navig settings navig.ai.model --reset       # remove override
        navig settings --layer project --show-sources
    """
    from navig.commands.settings_cmd import run_settings

    run_settings(
        key=key,
        value=value,
        layer=layer,
        reset=reset,
        show_sources=show_sources,
    )


# TELEGRAM BOT MANAGEMENT, MATRIX MESSAGING, STORE MANAGEMENT
# All registered lazily via _EXTERNAL_CMD_MAP / _register_external_commands()


# ── Extracted sub-apps (all via _EXTERNAL_CMD_MAP in registration.py):
# task/workflow, context/ctx, index, history/hist, tunnel, system_cmd
# monitor/security → navig.commands.host (sub-apps of host_app)

# ============================================================================
# REMOTE COMMAND EXECUTION
# ============================================================================


@app.command("run")
def run_command(
    ctx: typer.Context,
    command: str | None = typer.Argument(
        None, help="Command to execute, @- for stdin, @file for file"
    ),
    stdin: bool = typer.Option(
        False, "--stdin", "-s", help="Read command from stdin (bypasses escaping)"
    ),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read command from file"),
    b64: bool = typer.Option(
        False,
        "--b64",
        "-b",
        help="Encode command as Base64 (escape-proof for JSON/special chars)",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Open editor for multi-line input"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-confirm prompts (same as global --yes)"
    ),
    confirm: bool = typer.Option(False, "--confirm", "-c", help="Force confirmation prompt"),
    json: bool = typer.Option(False, "--json", help="Output JSON (captures stdout/stderr)"),
):
    """Execute arbitrary shell command on remote server.

    \b
    ⚠️  PowerShell Users: For commands with (), {}, $, or other special chars,
    use --stdin or --file to avoid quoting issues:

      echo 'complex command' | navig run --b64 --stdin
      navig run --b64 --file script.txt
      navig run -i     # Opens editor

    \b
    Examples:
      navig run "ls -la"                              # Simple command
      navig run --b64 "curl -d '{\"k\":\"v\"}' api"   # JSON (use stdin on PowerShell!)
      navig run @script.sh                            # Read from file
      cat script.sh | navig run @-                    # Read from stdin
      navig run -i                                    # Open editor

    \b
    Use --b64 for commands with:
      • JSON payloads: '{"key":"value"}'
      • Special characters: $ ! ( ) [ ] { }
      • Nested quotes: "outer 'inner' text"
    """
    from navig.commands.remote import run_remote_command

    # Merge command-level options with global options
    options = ctx.obj.copy()
    if yes:
        options["yes"] = True
    if confirm:
        options["confirm"] = True
    if b64:
        options["b64"] = True
    if json:
        options["json"] = True
    run_remote_command(command, options, stdin=stdin, file=file, interactive=interactive)


@app.command("procs")
def procs_command(
    target: str = typer.Argument("node", help="Group (node/python/chrome/...) or process-name substring"),
    flat: bool = typer.Option(False, "--flat", help="One row per process instead of grouping by project"),
    top: int = typer.Option(0, "--top", "-t", help="Show only the N biggest rows (0 = all)"),
    idle: bool = typer.Option(
        False, "--idle", help="Classify Claude Code sessions as idle / active / unknown (ignores TARGET)"
    ),
    idle_hours: float = typer.Option(24.0, "--idle-hours", help="With --idle: hours without activity that count as idle"),
    by: str = typer.Option("project", "--by", help="Group by 'project' (default) or 'role' (CI gate, TypeScript, dev server, ...)"),
    watch: float = typer.Option(0, "--watch", "-w", help="Redraw every N seconds until Ctrl-C (0 = once)"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Show which project each process belongs to, with memory and owner (read-only).

    \b
    The project is the git repo of the process's working directory; a linked
    worktree reads as repo:worktree. The owner is the nearest agent session or
    editor window up the parent chain. "detached" means the launcher is gone:
    either deliberately backgrounded or a leftover.

    \b
    --idle lists every Claude Code session with a verdict. "idle" means no tool
    is running, it has been open longer than --idle-hours and its transcript has
    not been written for that long; "unknown" is never reaped. Reap the idle
    ones with `navig kill claude-idle` (the conversations stay resumable).

    \b
    Examples:
      navig procs                      # node processes grouped by project
      navig procs python --flat        # every python process, one row each
      navig procs claude --top 10      # the 10 biggest agent sessions
      navig procs --idle               # which Claude sessions are leftovers
      navig procs --by role            # node processes by job: CI gate, TypeScript, dev server
      navig procs --watch 5            # live view, redrawn every 5 s
      navig procs node --json

    \b
    The header shows CPU, free RAM, memory commit and the biggest process
    FAMILIES machine-wide, so a heavy family the TARGET filter hides (agent
    sessions, editors, a leaking desktop app) is still visible.
    """
    import json as _json

    from navig.commands import processes

    if idle:
        sessions = processes.describe_processes(processes.find_processes_by_name(["claude"]))
        sessions = [r for r in sessions if (r.get("name") or "").lower().removesuffix(".exe") == "claude"]
        if not sessions:
            typer.echo("No Claude Code sessions running.")
            raise typer.Exit(0)
        order = {"idle": 0, "unknown": 1, "young": 2, "active": 3, "self": 4}
        rows = sorted(
            processes.classify_claude_sessions(sessions, idle_hours),
            key=lambda r: (order.get(r["verdict"], 9), -(r.get("idle_h") or 0), -r["mem_mb"]),
        )
        if json_output:
            typer.echo(_json.dumps(rows, indent=2, default=str))
            raise typer.Exit(0)
        from navig import console_helper as ch
        from navig.console_helper import Table

        tone = {"idle": "yellow", "unknown": "dim", "young": "cyan", "active": "green", "self": "magenta"}
        t = Table(box=None, show_header=True, padding=(0, 2))
        for col, just in (("PID", "right"), ("Verdict", "left"), ("RAM", "right"), ("Open", "right"),
                          ("Last write", "right"), ("Project", "left"), ("Why", "left")):
            t.add_column(col, justify=just, no_wrap=col != "Why")
        for r in rows[:top] if top else rows:
            where = r["project"] or ("<unreadable>" if not r["readable"] else "")
            last = f"{r['idle_h']:g}h ago" if r.get("idle_h") is not None else "—"
            t.add_row(str(r["pid"]), f"[{tone.get(r['verdict'], 'dim')}]{r['verdict']}[/]",
                      f"{r['mem_mb'] / 1024:.2f} GB", f"{r['age_s'] / 3600:.1f}h", last, where, f"[dim]{r['reason']}[/dim]")
        ch.console.print(t)
        reap = [r for r in rows if r["verdict"] == "idle"]
        counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in order}
        ch.console.print(
            f"\n{len(rows)} session(s): [yellow]{counts['idle']} idle[/yellow] "
            f"(~{sum(r['mem_mb'] for r in reap) / 1024:.1f} GB) · "
            f"[green]{counts['active'] + counts['self']} active[/green] · "
            f"[cyan]{counts['young']} young[/cyan] (open under {idle_hours:g}h, not judged) · "
            f"{counts['unknown']} unknown"
        )
        if reap:
            ch.console.print(f"[dim]Reap the idle ones: navig kill claude-idle --idle-hours {idle_hours:g}[/dim]")
        elif counts["young"]:
            ch.console.print(f"[dim]Judge younger sessions with a shorter threshold: navig procs --idle --idle-hours 4[/dim]")
        raise typer.Exit(0)

    if by not in ("project", "role"):
        typer.echo("--by must be 'project' or 'role'.", err=True)
        raise typer.Exit(2)

    def _snapshot() -> tuple[list, Any]:
        rows = processes.describe_processes(processes.find_processes_by_group(target))
        if flat:
            rows.sort(key=lambda r: -r["mem_mb"])
            return rows, rows[:top] if top else rows
        groups = processes.summarize_by_role(rows) if by == "role" else processes.summarize_by_project(rows)
        return rows, groups[:top] if top else groups

    if json_output:
        if watch:
            typer.echo("--watch cannot be combined with --json.", err=True)
            raise typer.Exit(2)
        _, shown = _snapshot()
        typer.echo(_json.dumps(shown, indent=2, default=str))
        raise typer.Exit(0)

    if not watch:
        _render_procs(processes, target, flat, by, _snapshot)
        raise typer.Exit(0)

    from navig import console_helper as ch

    try:
        while True:
            ch.console.clear()
            _render_procs(processes, target, flat, by, _snapshot)
            ch.console.print(f"[dim]refreshing every {watch:g}s - Ctrl-C to stop[/dim]")
            time.sleep(max(0.5, watch))
    except KeyboardInterrupt:
        raise typer.Exit(0)


def _gb(mb: float) -> str:
    return f"{mb / 1024:.2f} GB"


def _render_procs(processes: Any, target: str, flat: bool, by: str, snapshot: Any) -> bool:
    """Draw the machine header + the process table. Returns False when nothing matched."""
    from navig import console_helper as ch
    from navig.console_helper import Table

    m = processes.machine_summary()
    cpu = m.get("cpu_percent")
    free, total = m.get("ram_free_gb"), m.get("ram_total_gb")
    used, ctotal = m.get("commit_used_gb"), m.get("commit_total_gb")

    def _tone(pct, warn, bad):
        if pct is None:
            return "dim"
        return "red" if pct >= bad else "yellow" if pct >= warn else "green"

    head = Table(box=None, show_header=False, padding=(0, 2))
    for _ in range(3):
        head.add_column(no_wrap=True)
    ram_pct = None if not (free is not None and total) else 100 * (1 - free / total)
    head.add_row(
        f"[{_tone(cpu, 70, 90)}]CPU {cpu:.0f}%[/]" if cpu is not None else "[dim]CPU ?[/dim]",
        f"[{_tone(ram_pct, 80, 92)}]RAM {free:.1f} GB free of {total:.0f} GB[/]" if ram_pct is not None else "[dim]RAM ?[/dim]",
        f"commit {used:.0f} / {ctotal:.0f} GB - {m.get('processes', 0)} processes" if used is not None else "",
    )
    ch.console.print(head)
    if m.get("families"):
        ch.console.print("[dim]  biggest process families, by private memory:[/dim]")
        fam = Table(box=None, show_header=True, padding=(0, 2))
        fam.add_column("Family", no_wrap=True)
        fam.add_column("Procs", justify="right", no_wrap=True)
        fam.add_column("Private", justify="right", no_wrap=True)
        for f in m["families"]:
            fam.add_row(f["name"], str(f["count"]), f"{f['private_gb']:.1f} GB")
        ch.console.print(fam)
    ch.console.print()

    rows, shown = snapshot()
    if not rows:
        ch.console.print(f"No matching processes for '{target}'.")
        return False
    t = Table(box=None, show_header=True, padding=(0, 2))
    if flat:
        cols = (("PID", "right"), ("RAM", "right"), ("Age", "right"), ("Project", "left"), ("Kind", "left"), ("Owner", "left"))
        for col, just in cols:
            t.add_column(col, justify=just, no_wrap=col != "Owner")
        for r in shown:
            where = r["project"] or ("<unreadable>" if not r["readable"] else "")
            owner = r["owner"] or ("[yellow]detached[/yellow]" if r["detached"] else "")
            t.add_row(str(r["pid"]), _gb(r["mem_mb"]), f"{r['age_s'] / 3600:.1f}h", where, r["kind"], owner)
    else:
        first = "Role" if by == "role" else "Project"
        who_col = "Projects" if by == "role" else "Owner"
        cols = (("RAM", "right"), ("Procs", "right"), (first, "left"), (who_col, "left"), ("What", "left"))
        for col, just in cols:
            t.add_column(col, justify=just, no_wrap=col in ("RAM", "Procs", first))
        for g in shown:
            kinds = ", ".join(f"{k} x{n}" if n > 1 else k for k, n in list(g["kinds"].items())[:5])
            names = g["projects"] if by == "role" else g["owners"]
            who = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
            if g["detached"]:
                who = (who + "; " if who else "") + f"[yellow]{g['detached']} detached[/yellow]"
            t.add_row(_gb(g["mem_mb"]), str(g["count"]), g.get("role") or g.get("project"), who, f"[dim]{kinds}[/dim]")
    ch.console.print(t)

    total_mb = sum(r["mem_mb"] for r in rows)
    unreadable = sum(1 for r in rows if not r["readable"])
    detached = sum(1 for r in rows if r.get("detached"))
    nudge = f"{len(rows)} {target} process(es) - {total_mb / 1024:.2f} GB"
    if detached:
        nudge += f" - {detached} detached"
    if by == "project" and not flat:
        nudge += " - by job: navig procs --by role"
    nudge += " - idle agents: navig procs --idle"
    ch.console.print(f"[dim]{nudge}[/dim]")
    if unreadable:
        hint = "an elevated shell" if sys.platform == "win32" else "root"
        ch.console.print(
            f"[dim]{unreadable} unreadable (owned by an elevated parent) - rerun from {hint} to see their project[/dim]"
        )
    return True


@app.command("kill")
def kill_command(
    target: str = typer.Argument(..., help="Group (node/python/chrome-tabs/...) or process-name substring"),
    force: bool = typer.Option(False, "--force", "-f", help="Use SIGKILL instead of graceful terminate"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be killed without killing"),
    include_self: bool = typer.Option(
        False, "--include-self", help="Don't protect this process's PID + parent PID (dangerous)"
    ),
    idle_hours: float = typer.Option(
        24.0, "--idle-hours", help="claude-idle only: hours without activity that count as idle"
    ),
):
    """Kill processes by group or name (cross-platform).

    \b
    Built-in groups:
      node, python, powershell, pwsh, conhost, windows-terminal,
      esbuild, git, chrome, chrome-tabs, chrome-all-renderers,
      claude-idle (Claude Code sessions `navig procs --idle` calls idle)

    \b
    Examples:
      navig kill node                  # graceful terminate all node procs
      navig kill chrome-tabs           # kill Chrome tab renderers (keeps browser + extensions)
      navig kill esbuild --force       # SIGKILL all esbuild
      navig kill node --dry-run        # list what would be killed
      navig kill claude-idle -n        # preview leftover Claude sessions, then drop -n
    """
    from navig.commands import processes

    if target.strip().lower() == "claude-idle":
        cands = processes.find_idle_claude_sessions(idle_hours)
        if not cands:
            typer.echo(f"No Claude Code session has been idle for {idle_hours:g}h.")
            raise typer.Exit(0)
        total = sum(r["mem_mb"] for r in cands)
        verb = "Would reap" if dry_run else "Reaping"
        typer.echo(f"{verb} {len(cands)} idle Claude session(s) (~{total / 1024:.1f} GB); conversations stay resumable:")
        for r in sorted(cands, key=lambda r: -(r["idle_h"] or 0)):
            where = r["project"] or "<unreadable>"
            typer.echo(f"  pid={r['pid']:>6}  {r['mem_mb']:>5.0f} MB  {where:<28} {r['reason']}")
        if dry_run:
            raise typer.Exit(0)
        report = processes.kill_processes(cands, force=True, exclude_self=not include_self)
        k, f = len(report["killed"]), len(report["failed"])
        typer.echo(f"Reaped {k}, failed {f}.")
        if f:
            if any("access denied" in str(x.get("reason", "")) for x in report["failed"]):
                hint = "an elevated shell" if sys.platform == "win32" else "root"
                typer.echo(f"  Some sessions belong to an elevated editor - rerun from {hint}.")
            raise typer.Exit(1)
        raise typer.Exit(0)

    if dry_run:
        report = processes.kill_group(target, dry_run=True)
        cands = report["candidates"]
        if not cands:
            typer.echo(f"No matching processes for '{target}'.")
            raise typer.Exit(0)
        rows = sorted(processes.describe_processes(cands), key=lambda r: -r["mem_mb"])
        total = sum(r["mem_mb"] for r in rows)
        typer.echo(f"Would kill {len(cands)} process(es) matching '{target}' (~{total / 1024:.2f} GB):")
        for r in rows[:50]:
            where = r["project"] or ("<unreadable>" if not r["readable"] else "")
            typer.echo(f"  pid={r['pid']:>6}  {r['mem_mb']:>8.0f} MB  {where:<28} {r['kind']}")
        if len(cands) > 50:
            typer.echo(f"  ... +{len(cands) - 50} more")
        raise typer.Exit(0)

    report = processes.kill_group(
        target, force=force, exclude_self=not include_self, dry_run=False
    )
    k, s, f = len(report["killed"]), len(report["skipped"]), len(report["failed"])
    typer.echo(f"Group '{target}': killed={k} skipped={s} failed={f}")
    if s:
        for sk in report["skipped"]:
            typer.echo(f"  skip pid={sk.get('pid')} reason={sk.get('reason')}")
    if f:
        for fl in report["failed"]:
            typer.echo(f"  fail pid={fl.get('pid')} reason={fl.get('reason')}")
        raise typer.Exit(1)
    raise typer.Exit(0)


@app.command("r", hidden=True)
def run_command_alias(
    ctx: typer.Context,
    command: str | None = typer.Argument(None, help="Alias for: navig run"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read command from stdin"),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read command from file"),
    b64: bool = typer.Option(False, "--b64", "-b", help="Base64 encode the command"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Open editor for multi-line input"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm prompts"),
    confirm: bool = typer.Option(False, "--confirm", "-c", help="Force confirmation prompt"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Alias for: navig run."""
    run_command(
        ctx,
        command=command,
        stdin=stdin,
        file=file,
        b64=b64,
        interactive=interactive,
        yes=yes,
        confirm=confirm,
        json=json,
    )


@app.command("status")
def status_command(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line status summary"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
    all_: bool = typer.Option(False, "--all", "-a", help="Show extended status"),
):
    """Show current NAVIG status (active host/app, tunnel, gateway)."""
    from navig.commands.status import show_status

    ctx.obj["plain"] = plain
    ctx.obj["all"] = all_
    if json:
        ctx.obj["json"] = True
    show_status(ctx.obj)


@app.command("dashboard")
def dashboard_command(
    ctx: typer.Context,
    live: bool = typer.Option(True, "--live/--no-live", help="Live auto-refresh mode"),
    refresh: int = typer.Option(5, "--refresh", "-r", help="Seconds between service re-reads"),
):
    """
    Live operator console: identity, services, safety, hosts and activity.

    The dashboard shows:
    - Your install's sigil, active space and plan progress
    - Daemon, gateway, bot, cron, reach and default AI
    - Ledger chain, pending approvals, host locks, in-flight operations
    - SSH reachability of every host, and the latest operations

    Examples:
        navig dashboard           # Live view
        navig dashboard --no-live # Single snapshot (also used when piped)
        navig dashboard -r 10     # Re-read services every 10 seconds

    Keys: q quit · r refresh · d doctor · g open deck · ? help.
    """
    from navig.commands.dashboard import run_dashboard, run_dashboard_simple

    if live:
        run_dashboard(refresh_interval=refresh)
    else:
        run_dashboard_simple()


@app.command("suggest")
def suggest_command(
    ctx: typer.Context,
    context: str | None = typer.Option(
        None,
        "--context",
        "-c",
        help="Filter by context (docker, database, deployment, monitoring)",
    ),
    run_idx: int | None = typer.Option(None, "--run", "-r", help="Run suggestion by number"),
    limit: int = typer.Option(8, "--limit", "-l", help="Number of suggestions"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show command without executing"),
):
    """
    Intelligent command suggestions based on history and context.

    Analyzes your command history, current project context, and time patterns
    to suggest relevant commands.

    Examples:
        navig suggest                    # Show suggestions
        navig suggest --context docker   # Docker-related suggestions
        navig suggest --run 1            # Run first suggestion
        navig suggest --run 2 --dry-run  # Preview second suggestion

    Suggestion sources:
        H = History (frequently used)
        S = Sequence (what usually follows)
        T = Time (typical for this time of day)
        C = Context (project type detected)
    """
    from navig.commands.suggest import run_suggestion, show_suggestions

    if run_idx is not None:
        run_suggestion(run_idx, dry_run=dry_run)
    else:
        show_suggestions(
            context=context,
            limit=limit,
            plain=plain,
            json_out=json_out,
            opts=ctx.obj,
        )


# ── triggers, insights, install_app, quick_app → via _EXTERNAL_CMD_MAP


@app.command("quickstart")
def quickstart_command(ctx: typer.Context):
    """Minimal onboarding to get NAVIG usable in under 5 minutes."""
    from navig.commands.quickstart import quickstart

    quickstart(ctx.obj)


# `navig install` is the community-asset group (navig.commands.install). An inline
# `install <package>` command used to be declared here too, but the group registered
# under the same name always won, so it was unreachable; installing a package on a
# remote host is `navig host maintenance install <package>`.


# ── hosts/software/local → navig.commands.local, web_app → navig.commands.webserver
# ── ai_app + ai_memory_app → navig.commands.ai  (all via _EXTERNAL_CMD_MAP)


@app.command("ask")
def ask_compat(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override default AI model",
    ),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Preview inferred intent/route before execution (safe mode)",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="With --plan, execute after preview; without --plan, no behavior change",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        "-e",
        help="Reasoning depth: low, medium, high, max, ultra  [default: auto]",
    ),
):
    """Ask AI about server/configuration."""
    from navig.agent.effort import resolve_effort
    from navig.commands.ai import ask_ai
    from navig.llm.routing.detect import detect_mode

    if effort is not None:
        try:
            resolve_effort(effort)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(2) from exc

    mode, confidence, reasons = detect_mode(question)
    opts = dict(ctx.obj or {})
    opts["ask_detected_mode"] = mode
    opts["ask_detected_confidence"] = confidence
    opts["ask_detected_reasons"] = reasons
    if effort is not None:
        opts["effort"] = effort

    if plan:
        ch.dim(f"Intent route: {mode} (confidence={confidence:.2f}; reasons={', '.join(reasons)})")
        if not execute:
            ch.warning(
                "Plan preview only — command not executed.",
                "Re-run with --plan --execute to continue.",
            )
            return

    ask_ai(question, model, opts)


@app.command("chat", hidden=True)
def chat_compat(
    ctx: typer.Context,
    question: str | None = typer.Argument(None, help="Question to ask"),
) -> None:
    """[DEPRECATED] Use 'navig ask' instead."""
    deprecation_warning("navig chat", "navig ask")
    from navig.commands.chat import run_ai_chat

    run_ai_chat(question, single_query=question is not None)


# ── hestia/template/addon removed; server_template_app, mcp_app → via _EXTERNAL_CMD_MAP

# ── App INITIALIZATION


@app.command("init-local")
def init_local_command(
    ctx: typer.Context,
    copy_global: bool = typer.Option(
        False,
        "--copy-global",
        help="Copy (not move) global configs from ~/.navig/ to app .navig/",
    ),
):
    """
    Initialize app-specific .navig/ directory (renamed from 'init').

    Creates a hierarchical configuration structure in the current directory,
    allowing app-specific host and configuration management that takes
    precedence over global ~/.navig/ configs.

    Similar to 'git init', this makes the current directory a NAVIG app root.

    The --copy-global option COPIES (not moves) configurations from ~/.navig/
    to the app .navig/, leaving the originals intact. This allows the same
    host configs to be used across multiple apps.
    """
    from navig.commands.init import init_app

    ctx.obj["copy_global"] = copy_global
    init_app(ctx.obj)


# ── config_app/schema_app, backup_app, flow_app, skills_app, scaffold_app,
# ── wiki_app, dispatch/contacts → all via _EXTERNAL_CMD_MAP in registration.py


# ── bridge/copilot, bot_app → navig.commands.gateway via _EXTERNAL_CMD_MAP


# ============================================================================
# START - QUICK LAUNCHER (ALIAS)
# ============================================================================


@app.command("start")
def quick_start(
    bot: bool = typer.Option(True, "--bot/--no-bot", "-b/-B", help="Start Telegram bot"),
    gateway: bool = typer.Option(True, "--gateway/--no-gateway", "-g/-G", help="Start gateway"),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Gateway port (default: gateway.port from config, fallback 8789)",
    ),
    background: bool = typer.Option(
        True, "--background/--foreground", "-d/-f", help="Run in background"
    ),
):
    """
    Quick launcher - start NAVIG services with sensible defaults.

    By default starts both gateway and bot in background.

    Examples:
        navig start                  # Start gateway + bot (background)
        navig start --foreground     # Start in foreground (see logs)
        navig start --no-gateway     # Bot only (standalone)
        navig start --no-bot         # Gateway only
    """
    from navig.commands.start import run_quick_start

    run_quick_start(bot=bot, gateway=gateway, port=port, background=background)


# ── heartbeat_app, cron_app, approve_app, queue_app → navig.commands.gateway / _EXTERNAL_CMD_MAP
# ── memory_app → navig.commands.memory via _EXTERNAL_CMD_MAP


# ============================================================================
# DEFERRED COMMAND MODULE REGISTRATION
# ============================================================================
# External command modules are NOT imported at module level.
# They are registered lazily by _register_external_commands() which is
# called from main.py just before app() runs (after fast-path check).
# This avoids importing heavy transitive deps (rich, cryptography, asyncio)
# for every CLI invocation.
#
# Optimisation: if the first CLI argument matches an *inline* command
# (one that is defined in this file), we skip all external imports entirely —
# no `navig.commands.*` module is imported on hot paths like ``navig host list``.
# The saving is real but UNMEASURED: this line asserted "~200 ms" with no
# benchmark behind it, in the same file family that claimed sub-50 ms startup
# while measuring 80-90 ms. Quote a number here only once it has been measured.
#
# P1-14: Registration logic extracted to navig/cli/registration.py
# ============================================================================

from navig.cli.registration import (
    _EXTERNAL_CMD_MAP as _EXTERNAL_CMD_MAP,
)
from navig.cli.registration import (
    _HIDDEN_COMMANDS as _HIDDEN_COMMANDS,
)
from navig.cli.registration import (
    _clear_registration_cache as _clear_registration_cache,
)
from navig.cli.registration import (
    _register_external_commands,
)
from navig.cli.registration import (
    get_external_commands as get_external_commands,
)
from navig.cli.registration import (
    is_external_command as is_external_command,
)

# Re-export for backward compatibility (tests, tooling import from cli.__init__)
__all_registration__ = [
    "_EXTERNAL_CMD_MAP",
    "_HIDDEN_COMMANDS",
    "_register_external_commands",
    "_clear_registration_cache",
    "get_external_commands",
    "is_external_command",
]

# [P1-14] Original inline definitions moved to navig/cli/registration.py
# The following block has been extracted:
# - _EXTERNAL_CMD_MAP dict (~150 entries)
# - _HIDDEN_COMMANDS frozenset
# - _register_external_commands() function
# - get_external_commands() helper
# - is_external_command() helper

# --- END OF EXTRACTED BLOCK (was lines 10008-10243) ---
# The remaining inline code below this point stays in __init__.py

_REGISTRATION_EXTRACTED = True  # marker for tooling


# ============================================================================
# MONITORING & HEALTH CHECKS (Unified 'monitor' group)
# ============================================================================

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    _register_external_commands()
    app()
