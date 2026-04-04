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
from typing import Any, Dict, List, Optional  # noqa: F401

import typer

from navig import __version__
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
    _get_hacker_quotes,
    _schema_callback,
    help_callback,
    make_subcommand_callback,
    show_compact_help,
    show_subcommand_help,
    version_callback,
)
from navig.cli.help_dictionaries import HELP_REGISTRY  # noqa: E402


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
        pass
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
    ctx.obj["debug_logger"] = None

    # Initialize operation recorder for history tracking
    # void: every command becomes a memory. every memory becomes a lesson.
    try:
        import sys
        import time

        from navig.operation_recorder import OperationType, get_operation_recorder

        recorder = get_operation_recorder()
        command_str = " ".join(sys.argv[1:])  # Exclude 'python -m navig'

        # Determine operation type from command
        op_type = OperationType.LOCAL_COMMAND
        if any(kw in command_str for kw in ["exec ", "ssh ", "tunnel "]):
            op_type = OperationType.REMOTE_COMMAND
        elif any(kw in command_str for kw in ["db ", "database "]):
            op_type = OperationType.DATABASE_QUERY
        elif any(kw in command_str for kw in ["upload ", "download ", "get ", "put "]):
            op_type = (
                OperationType.FILE_UPLOAD
                if "upload" in command_str or "put" in command_str
                else OperationType.FILE_DOWNLOAD
            )
        elif any(kw in command_str for kw in ["docker ", "container "]):
            op_type = OperationType.DOCKER_COMMAND
        elif any(kw in command_str for kw in ["workflow run"]):
            op_type = OperationType.WORKFLOW_RUN
        elif "host use" in command_str or "host switch" in command_str:
            op_type = OperationType.HOST_SWITCH
        elif "service" in command_str:
            op_type = OperationType.SERVICE_RESTART

        # Skip recording for certain meta commands
        skip_record = any(
            kw in command_str
            for kw in [
                "history ",
                "help",
                "--help",
                "-h",
                "--version",
                "-v",
                "insights ",
                "dashboard",
                "suggest",
                "trigger test",
                "trigger history",
            ]
        )

        if not skip_record and command_str.strip():
            record = recorder.start_operation(
                command=f"navig {command_str}",
                operation_type=op_type,
                host=host,
                app=app,
            )
            ctx.obj["_operation_record"] = record
            ctx.obj["_operation_start"] = time.time()
            ctx.obj["_operation_recorder"] = recorder
    except Exception as e:
        # Silently skip recording on failure
        if verbose:
            ch.dim(f"→ Operation recording skipped: {e}")

    # Initialize debug logger if enabled (via flag OR config)
    # void: every action leaves a trace. we just choose which traces to keep.
    # Performance: read debug settings from raw YAML without full config load.
    _debug_raw_cfg = None  # raw YAML dict when read via fast-path
    debug_log_enabled = debug_log
    if not debug_log_enabled:
        try:
            _cm = _get_config_manager()
            # Only load config for this check if it's already loaded (cheap)
            if _cm._global_config_loaded:
                debug_log_enabled = _cm.global_config.get("debug_log", False)
            else:
                # Fast-path: read just the debug_log key from the YAML file
                # without triggering full config load + migrations + validation
                import yaml

                _gc_file = _cm.global_config_dir / "config.yaml"
                if _gc_file.exists():
                    with open(_gc_file, encoding="utf-8") as _f:
                        _debug_raw_cfg = yaml.safe_load(_f) or {}
                    debug_log_enabled = _debug_raw_cfg.get("debug_log", False)
        except Exception:
            debug_log_enabled = False

    if debug_log_enabled:
        try:
            from navig.debug_logger import DebugLogger

            # Reuse raw YAML dict if available; fall back to full config only if needed
            _dgc = _debug_raw_cfg or (
                _get_config_manager().global_config
                if _get_config_manager()._global_config_loaded
                else {}
            )
            log_path = _dgc.get("debug_log_path")
            max_size_mb = _dgc.get("debug_log_max_size_mb", 10)
            max_files = _dgc.get("debug_log_max_files", 5)
            truncate_kb = _dgc.get("debug_log_truncate_output_kb", 10)

            debug_logger = DebugLogger(
                log_path=Path(log_path) if log_path else None,
                max_size_mb=max_size_mb,
                max_files=max_files,
                truncate_output_kb=truncate_kb,
            )
            ctx.obj["debug_logger"] = debug_logger

            # Log command start
            import atexit
            import sys

            command_str = " ".join(sys.argv)
            debug_logger.log_command_start(
                command_str,
                {
                    "host": host,
                    "app": app,
                    "verbose": verbose,
                    "quiet": quiet,
                    "dry_run": dry_run,
                },
            )

            # Register atexit handler to log command end
            def log_command_end_on_exit():
                debug_logger.log_command_end(True)

            atexit.register(log_command_end_on_exit)

            if verbose:
                ch.dim(f"→ Debug logging enabled: {debug_logger.log_path}")
        except Exception as e:
            if verbose:
                ch.warning(f"Failed to initialize debug logger: {e}")

    # Register operation recording completion handler
    # void: the loop closes. the record endures.
    if "_operation_record" in ctx.obj:
        import atexit
        import time

        def record_operation_on_exit():
            def _do_record():
                try:
                    record = ctx.obj.get("_operation_record")
                    recorder = ctx.obj.get("_operation_recorder")
                    start_time = ctx.obj.get("_operation_start", time.time())

                    if record and recorder:
                        duration_ms = (time.time() - start_time) * 1000
                        # Assume success unless we explicitly track failure
                        # (actual exit code handling would require more integration)
                        recorder.complete_operation(
                            record=record,
                            success=True,
                            output="",
                            duration_ms=duration_ms,
                        )
                except Exception:
                    pass  # Silent fail for recording

            import threading

            # daemon=False so the write completes before process exits.
            # join(1.0) caps CLI exit delay to ≤1 second even on slow DB.
            t = threading.Thread(target=_do_record, daemon=False)
            t.start()
            t.join(timeout=1.0)

        atexit.register(record_operation_on_exit)

    # Register fact extraction handler — runs silently after every CLI invocation.
    # void: every command is a signal. we harvest meaning from routine.
    _SKIP_FACT_CMDS = frozenset(["memory", "kg", "index", "history", "version", "help"])
    _invoked_cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if _invoked_cmd not in _SKIP_FACT_CMDS and _invoked_cmd not in (
        "",
        "--help",
        "--version",
    ):

        def _extract_facts_on_exit():
            def _do_extract():
                try:
                    command_str = " ".join(sys.argv[1:])
                    if not command_str.strip():
                        return
                    # Skip meta commands
                    _skip_prefixes = ("--help", "--version", "memory ", "kg ", "index ")
                    if any(command_str.startswith(p) for p in _skip_prefixes):
                        return
                    from navig.memory.manager import get_memory_manager

                    _mgr = get_memory_manager()
                    if hasattr(_mgr, "record_command"):
                        _mgr.record_command(command_str)
                    elif hasattr(_mgr, "fact_extractor") and _mgr.fact_extractor:
                        _result = _mgr.fact_extractor.extract_from_text(
                            f"User ran: {command_str}",
                            source="cli",
                        )
                        if _result and hasattr(_mgr, "store_facts"):
                            _mgr.store_facts(_result)
                except Exception:
                    pass  # Never surface memory errors to the user

            import threading

            threading.Thread(target=_do_extract, daemon=True).start()

        import atexit as _atexit2

        _atexit2.register(_extract_facts_on_exit)

    # Initialize proactive assistant if enabled
    # void: we built an AI to watch our systems. now who watches the AI?
    # Performance: skip for non-interactive / scripting commands (--plain, --json, -q).
    #
    # QUANTUM VELOCITY K3: Non-blocking async preload.
    # The assistant is initialized in a background daemon thread so it never
    # blocks the critical CLI path (saves ~181ms on every invocation).
    # Commands that need the assistant call ctx.obj['get_assistant']() which
    # returns the instance (waiting up to 500ms if still loading).
    _skip_assistant = quiet or any(a in sys.argv for a in ("--plain", "--raw", "--json"))
    if not _skip_assistant:
        import threading as _threading

        _assistant_holder: dict = {"instance": None, "error": None}
        _assistant_ready = _threading.Event()

        def _load_assistant_bg() -> None:
            try:
                from navig.config import get_config_manager as _gcm
                from navig.proactive_assistant import ProactiveAssistant as _PA

                _cfg = _gcm()
                _inst = _PA(_cfg)
                _assistant_holder["instance"] = _inst
            except Exception as _e:
                _assistant_holder["error"] = _e
            finally:
                _assistant_ready.set()

        _threading.Thread(target=_load_assistant_bg, daemon=True).start()

        def _get_assistant(timeout: float = 0.5):
            """Retrieve the ProactiveAssistant, waiting up to `timeout` seconds."""
            _assistant_ready.wait(timeout=timeout)
            return _assistant_holder.get("instance")

        ctx.obj["get_assistant"] = _get_assistant
        ctx.obj["assistant_enabled"] = True  # optimistic — set False if disabled
    else:
        ctx.obj["get_assistant"] = lambda timeout=0.5: None
        ctx.obj["assistant_enabled"] = False

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
            _run_ai_chat(query, single_query=True)
        else:
            # No args - show help
            show_compact_help()


def _run_ai_chat(initial_query: str = None, single_query: bool = False):
    """Run interactive AI chat or process single query."""
    from rich.console import Console

    console = Console()

    try:
        from navig.ai import AIAssistant  # noqa: PLC0415

        _cfg = _get_config_manager()
        ai = AIAssistant(_cfg)

        if single_query and initial_query:
            # Single query mode - run and exit
            import asyncio

            response = asyncio.run(ai.chat(initial_query, []))
            console.print(response)
            return

        # Interactive mode
        console.print("\n🤖 [bold cyan]NAVIG AI Chat[/bold cyan]")
        console.print("   Type your question or command. Type 'exit' or 'quit' to leave.\n")

        conversation = []

        # Process initial query if provided
        if initial_query:
            import asyncio

            console.print(f"[dim]You:[/dim] {initial_query}")
            response = asyncio.run(ai.chat(initial_query, conversation))
            console.print(f"\n{response}\n")
            conversation.append({"role": "user", "content": initial_query})
            conversation.append({"role": "assistant", "content": response})

        # Interactive loop
        import asyncio

        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "q", "bye"):
                    console.print("\n👋 Goodbye!")
                    break

                response = asyncio.run(ai.chat(user_input, conversation))
                console.print(f"\n{response}\n")

                conversation.append({"role": "user", "content": user_input})
                conversation.append({"role": "assistant", "content": response})

                # Keep conversation manageable
                if len(conversation) > 20:
                    conversation = conversation[-20:]

            except KeyboardInterrupt:
                console.print("\n👋 Goodbye!")
                break
            except EOFError:
                break

    except ImportError as e:
        ch.error(f"AI module not available: {e}")
        ch.info("Ensure NAVIG is installed correctly: pip install -e .")
    except Exception as e:
        ch.error(f"AI chat error: {e}")


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


@app.command("chat", hidden=True)
def chat_command(
    query: str | None = typer.Argument(None, help="Optional initial query"),
):
    """Start interactive AI chat (alias for running 'navig' with a query)."""
    _run_ai_chat(query, single_query=False)


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


# ============================================================================
# FILE OPERATIONS (Canonical 'file' group)
# ============================================================================

# ── task_app: extracted to navig/commands/workflow.py ───────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── context_app: extracted to navig/commands/context.py ─────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py
# Aliases: "context" (canonical), "ctx" (hidden)


# ── index_app: extracted to navig/commands/index.py ────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── history_app: extracted to navig/commands/history.py ─────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py
# Aliases: "history" (canonical), "hist" (hidden)


# ============================================================================
# App MANAGEMENT COMMANDS
# ============================================================================

# ============================================================================
# TUNNEL — extracted to navig.commands.tunnel :: tunnel_app
# ============================================================================


# ============================================================================
# MONITOR / SECURITY — removed (deprecated)
# ============================================================================
# Inline monitor_app (~190 lines) and security_app (~265 lines) removed.
# Canonical commands: `navig host monitor` and `navig host security`
# (sub-apps of host_app in navig.commands.host, via _EXTERNAL_CMD_MAP).
# Legacy flat aliases (monitor-resources, monitor-disk, monitor-services,
# monitor-network, health-check, monitoring-report, firewall-status,
# firewall-add, firewall-remove, fail2ban-status, security-scan) also removed.


# ============================================================================
# SYSTEM MAINTENANCE — deferred to _register_external_commands
# ============================================================================
# Inline system_app removed (~175 lines). Canonical source: navig.commands.system_cmd
# (registered via _EXTERNAL_CMD_MAP). Legacy flat aliases also removed:
#   update-packages, clean-packages, rotate-logs, cleanup-temp,
#   check-filesystem, system-maintenance


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


# ── trigger_app: extracted to navig/commands/triggers.py ────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── insights_app: extracted to navig/commands/insights.py ───────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# INSTALL — Community asset installer (brain/<type>/)
# ============================================================================

# ── install_app: extracted to navig/commands/install.py ─────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# QUICK ACTIONS - Shortcuts for frequent operations
# ============================================================================

# ── quick_app: extracted to navig/commands/suggest.py ──────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py
# Aliases: "quick" (canonical), "q" (hidden)


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


# ============================================================================
# LOCAL MACHINE MANAGEMENT (Canonical 'hosts', 'software', 'local' groups)
# ============================================================================

# Hosts file management
# ── hosts_app: extracted to navig/commands/local.py ────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── software_app: extracted to navig/commands/local.py ─────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── local_app: extracted to navig/commands/local.py ────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# ── web_app: extracted to navig/commands/webserver.py ──────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py
# Includes nested `navig web hestia ...` subcommands


# Deprecated hidden webserver aliases removed.
# Canonical commands: `navig web vhosts`, `navig web test`, `navig web reload`.



# ============================================================================
# AI ASSISTANT — extracted to navig/commands/ai.py (P1-14)
# ============================================================================
# ai_app + ai_memory_app (929 lines, 18 commands) → navig.commands.ai :: ai_app
# Registered via _EXTERNAL_CMD_MAP in registration.py.


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
):
    """Ask AI about server/configuration."""
    from navig.commands.ai import ask_ai

    ask_ai(question, model, ctx.obj)


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



# ============================================================================
# HESTIA / TEMPLATE / ADDON — removed (deprecated)
# ============================================================================
# Inline hestia_app (~140 lines), template_app (~100 lines), addon_app (~85 lines)
# removed. Canonical commands: `navig web hestia` (web_hestia_app sub-app),
# `navig flow template`. These deprecated wrappers called deprecation_warning()
# and delegated to navig.commands.hestia / navig.commands.template.


# ── server_template_app: extracted to navig/commands/server_template.py ─────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# mcp_app inline block removed — canonical: navig.commands.mcp_cmd via _EXTERNAL_CMD_MAP


# ============================================================================
# App INITIALIZATION
# ============================================================================


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


# ── config_app + schema_app: extracted to navig/commands/config.py ─────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# CONFIGURATION BACKUP & EXPORT COMMANDS
# ============================================================================

# ============================================================================
# BACKUP — extracted to navig.commands.backup :: backup_app
# ============================================================================


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

# ============================================================================
# WORKFLOW COMMANDS
# ============================================================================

# ── flow_app: extracted to navig/commands/flow.py ──────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── skills_app: extracted to navig/commands/skills.py ──────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py
# Aliases: "skills" (canonical), "skill" (hidden)


# ── scaffold_app: extracted to navig/commands/scaffold.py ──────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── flow_template_app: extracted to navig/commands/flow.py ─────────────────
# Registered as nested `navig flow template ...` inside flow_app



# ============================================================================
# WORKFLOW — removed (deprecated)
# ============================================================================
# Inline workflow_app (~120 lines) removed. Canonical command: `navig flow`
# (flow_app, registered via _EXTERNAL_CMD_MAP → navig.commands.flow).


# ── wiki_app: extracted to navig/commands/wiki.py ──────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# DISPATCH / CONTACTS — deferred to _register_external_commands
# ============================================================================
# Inline lazy-loading stubs removed — dispatch and contacts are registered via
# _EXTERNAL_CMD_MAP → navig.commands.dispatch (dispatch_app, contacts_app).


# ============================================================================
# BRIDGE / FARMORE / COPILOT — deferred to _register_external_commands
# ============================================================================
# QUANTUM VELOCITY K4: These were imported eagerly at module level, paying the
# full import cost (~30-60ms) even for unrelated commands like `navig host list`.
# They are now registered lazily via _EXTERNAL_CMD_MAP in _register_external_commands
# and only imported when the user actually invokes `navig bridge|farmore|copilot`.
# (entries added to _EXTERNAL_CMD_MAP below)


# ── Shared gateway helpers: moved to navig/commands/gateway.py ─────────────

# ── bot_app: extracted to navig/commands/gateway.py ────────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


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


# ── heartbeat_app: extracted to navig/commands/gateway.py ──────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# CRON — deferred to _register_external_commands
# ============================================================================
# Inline cron_app removed (~215 lines). Full implementation lives in
# navig.commands.cron (cron_app) and is registered via _EXTERNAL_CMD_MAP.


# ── approve_app: extracted to navig/commands/gateway.py ────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ── queue_app: extracted to navig/commands/gateway.py ──────────────────────
# Registration via _EXTERNAL_CMD_MAP in registration.py


# ============================================================================
# MEMORY MANAGEMENT — extracted to navig/commands/memory.py (P1-14)
# ============================================================================


# ── config legacy extension block removed ───────────────────────────────────
# Additional config subcommands are now defined in `navig.commands.config`.



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
    _EXTERNAL_CMD_MAP,
    _HIDDEN_COMMANDS,
    _register_external_commands,
    get_external_commands,
    is_external_command,
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
