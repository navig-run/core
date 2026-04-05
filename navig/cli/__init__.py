"""
NAVIG CLI - No Admin Visible In Graveyard

Keep your servers alive. Forever.

Performance note: Heavy imports (TunnelManager, RemoteOperations, AIAssistant)
are deferred until actually needed to improve CLI startup time.
"""

import logging
import os
import sys
from pathlib import Path
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

import threading as _threading  # P1-10: thread safety for singleton lazy-init

_config_manager = None
_NO_CACHE = False
_config_manager_lock = _threading.Lock()


def _get_config_manager():
    """Lazy-load and cache the ConfigManager (thread-safe — P1-10)."""
    global _config_manager
    if _config_manager is None:
        with _config_manager_lock:
            if _config_manager is None:  # double-checked locking
                from navig.config import get_config_manager

                _config_manager = get_config_manager(force_new=_NO_CACHE)
    return _config_manager


# Heavy dependencies - loaded lazily on first use
# This reduces startup time by ~200ms for commands that don't need them
_TunnelManager = None
_RemoteOperations = None
_AIAssistant = None
_lazy_lock = _threading.Lock()  # shared lock for lazy class references (cheap)
_log = logging.getLogger(__name__)


def _get_tunnel_manager():
    """Lazy load TunnelManager (thread-safe — P1-10)."""
    global _TunnelManager
    if _TunnelManager is None:
        with _lazy_lock:
            if _TunnelManager is None:
                from navig.tunnel import TunnelManager

                _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations():
    """Lazy load RemoteOperations (thread-safe — P1-10)."""
    global _RemoteOperations
    if _RemoteOperations is None:
        with _lazy_lock:
            if _RemoteOperations is None:
                from navig.remote import RemoteOperations

                _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant():
    """Lazy load AIAssistant (thread-safe — P1-10)."""
    global _AIAssistant
    if _AIAssistant is None:
        with _lazy_lock:
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
    _schema_callback,
    help_callback,
    make_subcommand_callback as make_subcommand_callback,
    show_compact_help,
    show_subcommand_help as show_subcommand_help,
    version_callback,
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
        help="Enable debug logging to file (.navig/debug.log)",
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

    global _NO_CACHE, _config_manager
    _NO_CACHE = bool(no_cache)
    try:
        from navig.config import reset_config_manager, set_config_cache_bypass

        set_config_cache_bypass(_NO_CACHE)
        if _NO_CACHE:
            reset_config_manager()
    except Exception:
        pass  # best-effort reset; failure does not affect CLI startup
    if _NO_CACHE:
        # Ensure subsequent calls create a fresh ConfigManager.
        _config_manager = None

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
            active_host = _get_config_manager().get_active_host()
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
    ctx.obj["dry_run"] = dry_run
    ctx.obj["yes"] = yes
    ctx.obj["confirm"] = confirm
    ctx.obj["raw"] = raw
    ctx.obj["json"] = json
    ctx.obj["debug_log"] = debug_log

    # Initialize operation recorder for history tracking
    # void: every command becomes a memory. every memory becomes a lesson.
    from navig.cli.middleware import init_operation_recorder
    init_operation_recorder(ctx, host=host, app=app, verbose=verbose)

    # Initialize debug logger if enabled (via flag OR config)
    # void: every action leaves a trace. we just choose which traces to keep.
    from navig.cli.middleware import init_debug_logger
    init_debug_logger(ctx, debug_log=debug_log, host=host, app=app, verbose=verbose, quiet=quiet, dry_run=dry_run)


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

        remaining_args = sys.argv[1:]

        # Filter out global flags
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
        non_flag_args = [
            arg for arg in remaining_args if arg not in global_flags and not arg.startswith("--")
        ]

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


@app.command("chat")
def chat_command(
    query: str | None = typer.Argument(None, help="Optional initial query"),
):
    """[DEPRECATED: Use 'navig ask'] Start interactive AI chat."""
    deprecation_warning("navig chat", "navig ask")
    from navig.commands.chat import run_ai_chat
    run_ai_chat(query, single_query=False)


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

    run_fetch(ctx, url=url, mode=mode, max_chars=max_chars, timeout=timeout, plain=plain, json_output=json_output)


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
        help="Search provider: auto, brave, duckduckgo, perplexity, gemini, grok, kimi",
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

    run_search(ctx, query=query, limit=limit, provider=provider, plain=plain, json_output=json_output)


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
        help="Run installer profile without wizard: quickstart, node, operator, architect, system_standard, system_deep",
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
) -> None:
    """
    State-aware NAVIG setup gateway.

    First run  → interactive setup wizard (TUI or Rich fallback)
    Return     → configuration status dashboard

    Use --profile to run the non-interactive installer pipeline instead:

        navig init --profile operator          # silent install (default profile)
        navig init --profile node --dry-run    # preview minimal setup
        navig init --profile system_standard   # + service daemon

    Deep-links (interactive wizard only):
        navig init --provider    jump to AI-provider configuration
        navig init --settings    open settings/status overview
        navig init --reconfigure force the wizard to re-run

    Examples:
        navig init               # auto: wizard on first run, dashboard after
        navig init --reconfigure # always run wizard
        navig init --provider    # configure AI provider
    """
    from navig.commands.init import run_init_command
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
            from navig.platform.paths import navig_config_dir

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
    refresh: int = typer.Option(5, "--refresh", "-r", help="Refresh interval in seconds"),
):
    """
    Real-time operations dashboard with host status, Docker, and history.

    The dashboard shows:
    - Host connectivity status with latency
    - Docker container overview
    - Recent operations from history
    - System resource overview

    Examples:
        navig dashboard           # Full live dashboard
        navig dashboard --no-live # Single snapshot
        navig dashboard -r 10     # Refresh every 10 seconds

    Press Q to quit, R to force refresh.
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


@app.command("install")
def install_package(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Package or command to install"),
):
    """Auto-detect package manager and install."""
    from navig.commands.remote import install_remote_package

    install_remote_package(package, ctx.obj)


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
):
    """Ask AI about server/configuration."""
    from navig.commands.ai import ask_ai
    from navig.routing.detect import detect_mode

    mode, confidence, reasons = detect_mode(question)
    opts = dict(ctx.obj or {})
    opts["ask_detected_mode"] = mode
    opts["ask_detected_confidence"] = confidence
    opts["ask_detected_reasons"] = reasons

    if plan:
        ch.dim(
            f"Intent route: {mode} (confidence={confidence:.2f}; reasons={', '.join(reasons)})"
        )
        if not execute:
            ch.warning(
                "Plan preview only — command not executed.",
                "Re-run with --plan --execute to continue.",
            )
            return

    ask_ai(question, model, opts)


# Legacy flat command for backward compatibility
@app.command("ai", hidden=True)
def ai_legacy(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(None, "--model", "-m", help="Override default AI model"),
):
    """[DEPRECATED: Use 'navig ai ask'] Ask AI about server."""
    deprecation_warning("navig ai <question>", "navig ai ask <question>")
    from navig.commands.ai import ask_ai

    ask_ai(question, model, ctx.obj)



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


# ── bridge/farmore/copilot, bot_app → navig.commands.gateway via _EXTERNAL_CMD_MAP


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
# (one that is defined in this file), we skip all external imports
# entirely — saving ~200 ms on hot paths like ``navig host list``.
#
# P1-14: Registration logic extracted to navig/cli/registration.py
# ============================================================================

from navig.cli.registration import (
    _EXTERNAL_CMD_MAP as _EXTERNAL_CMD_MAP,
    _HIDDEN_COMMANDS as _HIDDEN_COMMANDS,
    _register_external_commands,
    get_external_commands as get_external_commands,
    is_external_command as is_external_command,
)

# Re-export for backward compatibility (tests, tooling import from cli.__init__)
__all_registration__ = [
    "_EXTERNAL_CMD_MAP",
    "_HIDDEN_COMMANDS",
    "_register_external_commands",
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
