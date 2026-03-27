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
# CENTRALIZED HELP SYSTEM
# ============================================================================
# Single source of truth for all CLI help text.
# Format: "command": {"desc": "description", "commands": {"cmd": "desc", ...}}
#
# Standardization rules:
# - Descriptions: Start with verb (Manage, Execute, Control)
# - Commands: lowercase verb phrase, no period
# - Consistent verbs: list/add/remove/show/edit/test/run/use

from navig.cli.help_dictionaries import HELP_REGISTRY


def show_subcommand_help(name: str, ctx: typer.Context = None):
    """Display compact help for a subcommand using the help registry."""
    from rich.console import Console
    from rich.table import Table

    # Use legacy_windows=True to avoid Unicode encoding issues on some Windows consoles
    console = Console(legacy_windows=True)

    if name not in HELP_REGISTRY:
        # Fallback to default Typer help if not in registry
        return False

    info = HELP_REGISTRY[name]

    console.print()
    console.print(
        f"[bold cyan]navig {name}[/bold cyan] [dim]-[/dim] [white]{info['desc']}[/white]"
    )
    console.print("[dim]" + "=" * 75 + "[/dim]")

    # Commands table
    cmd_table = Table(
        box=None, show_header=False, padding=(0, 2), collapse_padding=True
    )
    cmd_table.add_column("Command", style="cyan", min_width=12)
    cmd_table.add_column("Description", style="dim")

    for cmd, desc in info["commands"].items():
        cmd_table.add_row(cmd, desc)

    console.print(cmd_table)

    console.print("[dim]" + "=" * 75 + "[/dim]")
    console.print(
        f"[yellow]navig {name} <cmd> --help[/yellow] [dim]for command details[/dim]"
    )
    console.print()

    return True


def make_subcommand_callback(name: str):
    """Create a callback function for a subcommand that shows custom help."""

    def callback(ctx: typer.Context):
        if ctx.invoked_subcommand is None:
            if show_subcommand_help(name, ctx):
                raise typer.Exit()

    return callback


def show_compact_help():
    """Render navig/help/index.md with Rich Markdown, or fall back to bare text."""
    from pathlib import Path as _Path

    _help_index = _Path(__file__).resolve().parent.parent / "help" / "index.md"
    if _help_index.exists():
        try:
            from rich.console import Console as _Console
            from rich.markdown import Markdown as _MD

            _Console(legacy_windows=True).print(
                _MD(_help_index.read_text(encoding="utf-8"))
            )
            raise typer.Exit()
        except typer.Exit:
            raise
        except Exception:
            pass
    # Fallback: minimal text so --help never crashes
    from navig import __version__ as _v  # noqa: PLC0415

    typer.echo(f"NAVIG v{_v}")
    typer.echo("  navig <command> [options]")
    typer.echo("  navig help <cmd>  for details")
    raise typer.Exit()


def help_callback(ctx: typer.Context, value: bool):
    """Callback for --help flag."""
    if value:
        show_compact_help()


# Initialize CLI app
app = typer.Typer(
    name="navig",
    help="NAVIG - Server Management CLI",
    add_completion=True,
    rich_markup_mode="rich",
    invoke_without_command=True,
    no_args_is_help=False,
)

# Global state (lazy via _get_config_manager())


# ============================================================================
# HACKER CULTURE & TECHNOLOGY QUOTES
# ============================================================================
# Quote list lives in navig/cli/_quotes.py and is imported lazily inside
# version_callback() and the 'version' command body.  This avoids parsing
# the 90-line list on every CLI import where quotes are never shown.

_HACKER_QUOTES: list | None = None  # populated on first use below


def _get_hacker_quotes() -> list:
    global _HACKER_QUOTES
    if _HACKER_QUOTES is None:
        from navig.cli._quotes import HACKER_QUOTES as _q

        _HACKER_QUOTES = _q
    return _HACKER_QUOTES


# ============================================================================
# GLOBAL FLAGS (applied via context)
# ============================================================================


def _schema_callback(value: bool):
    """Output machine-readable command schema as JSON and exit."""
    if value:
        import json as _json

        from navig.cli.registry import get_schema

        _schema = get_schema()
        typer.echo(_json.dumps(_schema, indent=2))
        raise typer.Exit()


def version_callback(value: bool):
    """Show version and exit."""
    if value:
        ch.info(f"NAVIG v{__version__}")
        # Select and display a random quote
        import random

        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f"💬 {quote} - {author}")
        raise typer.Exit()


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
    _skip_assistant = quiet or any(
        a in sys.argv for a in ("--plain", "--raw", "--json")
    )
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
            arg
            for arg in remaining_args
            if arg not in global_flags and not arg.startswith("--")
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
        console.print(
            "   Type your question or command. Type 'exit' or 'quit' to leave.\n"
        )

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
    import platform
    import sys

    if json_output:
        import json

        info = {
            "navig_version": __version__,
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
            "platform_release": platform.release(),
            "machine": platform.machine(),
        }
        print(json.dumps(info, indent=2))
    else:
        ch.info(f"NAVIG v{__version__}")
        ch.dim(
            f"Python {sys.version.split()[0]} on {platform.system()} {platform.release()}"
        )
        # Show a random quote
        import random

        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f"💬 {quote} - {author}")


@app.command("upgrade")
def upgrade_command(
    ctx: typer.Context,
    check: bool = typer.Option(
        False, "--check", "-c", help="Only check, don't install"
    ),
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
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from rich.console import Console as _RC

    _con = _RC()
    src_dir = (
        Path(__file__).resolve().parent.parent.parent
    )  # navig/cli/__init__.py → navig-core/
    is_git = (src_dir / ".git").exists()

    # ------------------------------------------------------------------ check
    if check:
        if is_git:
            try:
                # Show current commit without any network call
                log = subprocess.run(
                    ["git", "-C", str(src_dir), "log", "--oneline", "-1"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                commit = log.stdout.strip()
                _con.print(
                    f"[green]✓[/green] NAVIG v{__version__}  [dim]{commit}[/dim]"
                )
                _con.print(
                    "[dim]Run [bold]navig upgrade[/bold] to pull latest commits.[/dim]"
                )
            except Exception as exc:
                _con.print(f"[dim]Could not read git info: {exc}[/dim]")
        else:
            _con.print(f"[green]✓[/green] NAVIG v{__version__}")
            _con.print(
                "[dim]Run [bold]navig upgrade[/bold] to upgrade to the latest release.[/dim]"
            )
        return

    # ---------------------------------------------------------------- upgrade
    old_version = __version__
    success = False

    if is_git:
        _con.print(f"[cyan]▶[/cyan] Pulling latest from git… [dim]({src_dir})[/dim]")
        _git_env = {**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            pull = subprocess.run(
                [
                    "git",
                    "-C",
                    str(src_dir),
                    "-c",
                    "http.connectTimeout=10",
                    "-c",
                    "http.lowSpeedLimit=0",
                    "-c",
                    "http.lowSpeedTime=20",
                    "pull",
                    "--ff-only",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env,
            )
            if pull.returncode != 0:
                err = pull.stderr.strip()
                _con.print(f"[red]✗[/red] git pull failed:\n[dim]{err[:300]}[/dim]")
                raise SystemExit(1)
            if "Already up to date" in pull.stdout and not force:
                _con.print(f"[green]✓[/green] Already up-to-date (v{old_version})")
                return
            _con.print(f"[dim]{pull.stdout.strip()}[/dim]")
        except FileNotFoundError as _exc:
            _con.print("[red]✗[/red] git not found — install git and retry")
            raise SystemExit(1) from _exc
        except subprocess.TimeoutExpired:
            _con.print(
                "[yellow]⚠[/yellow] git pull timed out (slow network) — reinstalling from local source"
            )

        # Re-install editable so any new entry points or deps are picked up
        _con.print("[cyan]▶[/cyan] Reinstalling package…")
        uv = shutil.which("uv")
        if uv:
            cmd = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "-e",
                str(src_dir),
                "-q",
            ]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-e", str(src_dir), "-q"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            _con.print(
                f"[yellow]⚠[/yellow] Reinstall warning:\n[dim]{r.stderr.strip()[:300]}[/dim]"
            )
        success = True

    else:
        # PyPI install — use uv or pip
        uv = shutil.which("uv")
        if uv:
            _con.print("[cyan]▶[/cyan] Upgrading via [bold]uv[/bold]…")
            cmd = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--upgrade",
                "navig",
            ]
            if force:
                cmd.append("--reinstall")
        else:
            _con.print("[cyan]▶[/cyan] Upgrading via [bold]pip[/bold]…")
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "navig",
                "--disable-pip-version-check",
                "-q",
            ]
            if force:
                cmd.append("--force-reinstall")

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                _con.print(
                    f"[red]✗[/red] Upgrade failed:\n[dim]{r.stderr.strip()[:400]}[/dim]"
                )
                raise SystemExit(1)
            success = True
        except subprocess.TimeoutExpired as _exc:
            _con.print("[red]✗[/red] Upgrade timed out — check your network connection")
            raise SystemExit(1) from _exc

    if success:
        # Re-import to get new version string
        try:
            import importlib

            import navig as _nav

            importlib.reload(_nav)
            new_version = _nav.__version__
        except Exception:
            new_version = "?"
        if new_version != old_version:
            _con.print(
                f"[bold green]✓[/bold green] Upgraded [cyan]v{old_version}[/cyan] → [bold cyan]v{new_version}[/bold cyan]"
            )
        else:
            _con.print(f"[bold green]✓[/bold green] NAVIG v{new_version} is ready")

        # Sync the PATH entry point if it differs from the venv script
        # (handles cases where an old navig.exe lives in ~/.local/bin)
        try:
            _venv_exe = (
                Path(__file__).resolve().parent.parent.parent
                / ".venv"
                / "Scripts"
                / "navig.exe"
            )
            _path_navig = shutil.which("navig")
            _path_exe = Path(_path_navig) if _path_navig else None
            if (
                _venv_exe.exists()
                and _path_exe
                and _path_exe.exists()
                and _venv_exe != _path_exe
            ):
                shutil.copy2(str(_venv_exe), str(_path_exe))
                _con.print(f"[dim]↳ PATH entry point updated: {_path_exe}[/dim]")
        except Exception:
            pass  # Never fail the upgrade over a PATH sync issue


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
    import json as jsonlib
    from pathlib import Path

    from rich.console import Console

    # --schema: emit the canonical command registry and exit
    if schema_out:
        from navig.cli.registry import get_schema as _get_schema

        typer.echo(jsonlib.dumps(_get_schema(), indent=2))
        raise typer.Exit()

    console = Console()
    # Help markdown files live at navig/help/, one level above navig/cli/
    help_dir = Path(__file__).resolve().parent.parent / "help"

    md_topics = []
    if help_dir.exists():
        md_topics = sorted(
            {
                p.stem
                for p in help_dir.glob("*.md")
                if p.is_file() and p.stem.lower() not in {"readme"}
            }
        )

    registry_topics = sorted(HELP_REGISTRY.keys())
    all_topics = sorted(set(md_topics) | set(registry_topics))

    want_json = bool(json_output or ctx.obj.get("json"))
    want_raw = bool(raw or ctx.obj.get("raw"))
    want_plain = plain or want_raw

    if not topic:
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {
                        "topics": all_topics,
                        "sources": {
                            "markdown": md_topics,
                            "registry": registry_topics,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        # If an index file exists, show it first.
        index_md = help_dir / "index.md"
        if index_md.exists():
            content = index_md.read_text(encoding="utf-8")
            if want_plain:
                console.print(content)
            else:
                from rich.markdown import Markdown

                console.print(Markdown(content))
        else:
            console.print("[bold cyan]NAVIG Help[/bold cyan]")
            console.print(
                "Use [yellow]navig help <topic>[/yellow] or [yellow]navig <cmd> --help[/yellow]."
            )

        if all_topics:
            console.print("\n[bold white]Topics[/bold white]")
            for name in all_topics:
                console.print(f"  - {name}")
        raise typer.Exit()

    normalized = topic.strip().lower()

    # Prefer markdown topic files if present.
    md_path = help_dir / f"{normalized}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {"topic": normalized, "content": content, "source": "markdown"},
                    indent=2,
                    sort_keys=True,
                )
            )
        elif want_plain:
            console.print(content)
        else:
            from rich.markdown import Markdown

            console.print(Markdown(content))
        raise typer.Exit()

    # Fall back to the centralized help registry.
    if normalized in HELP_REGISTRY:
        if want_json:
            typer.echo(
                jsonlib.dumps(
                    {
                        "topic": normalized,
                        "desc": HELP_REGISTRY[normalized].get("desc"),
                        "commands": HELP_REGISTRY[normalized].get("commands"),
                        "source": "registry",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        show_subcommand_help(normalized, ctx)
        raise typer.Exit()

    ch.error(
        f"Unknown help topic: {topic}",
        "Run 'navig help' to list topics or 'navig <cmd> --help' for command help.",
    )
    raise typer.Exit(1)


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
    import json as jsonlib
    from pathlib import Path

    from rich.console import Console

    # Force UTF-8 encoding for console to handle emoji on Windows
    console = Console(force_terminal=True)

    # Find docs directory (project root or installed package)
    project_docs = Path(__file__).resolve().parent.parent / "docs"
    pkg_docs = Path(__file__).resolve().parent / "docs"

    if project_docs.exists():
        docs_dir = project_docs
    elif pkg_docs.exists():
        docs_dir = pkg_docs
    else:
        ch.error(
            "Documentation directory not found.",
            "Make sure NAVIG is installed correctly with docs/ available.",
        )
        raise typer.Exit(1)

    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")

    # List all docs if no query
    if not query:
        md_files = sorted(docs_dir.glob("**/*.md"))
        topics = []
        for f in md_files:
            rel_path = f.relative_to(docs_dir)
            # Get first heading as title
            try:
                content = f.read_text(encoding="utf-8")
                lines = content.split("\n")
                title = None
                for line in lines:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
                topics.append(
                    {
                        "file": str(rel_path),
                        "title": title or f.stem,
                    }
                )
            except Exception:
                topics.append({"file": str(rel_path), "title": f.stem})

        if want_json:
            console.print(jsonlib.dumps({"topics": topics}, indent=2))
        else:
            console.print("[bold cyan]NAVIG Documentation[/bold cyan]")
            console.print(f"Found {len(topics)} documentation files.\n")
            for item in topics:
                # Use safe ASCII output - strip emoji that can't be encoded
                title = item["title"]
                try:
                    # Test if title can be encoded in console encoding
                    title.encode(console.encoding or "utf-8")
                except (UnicodeEncodeError, LookupError):
                    # Strip non-ASCII characters
                    title = "".join(c for c in title if ord(c) < 128)
                console.print(
                    f"  [cyan]*[/cyan] [yellow]{item['file']}[/yellow]: {title.strip()}"
                )
            console.print(
                "\n[dim]Use 'navig docs <query>' to search documentation.[/dim]"
            )
        raise typer.Exit()

    # Search docs
    try:
        from navig.tools.web import search_docs

        results = search_docs(query=query, docs_path=docs_dir, max_results=limit)

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "query": query,
                        "results": [
                            {
                                "file": r.get("file"),
                                "title": r.get("title"),
                                "excerpt": r.get("excerpt"),
                                "score": r.get("score"),
                            }
                            for r in results
                        ],
                    },
                    indent=2,
                )
            )
        else:
            if not results:
                console.print(f"[yellow]No results found for '{query}'.[/yellow]")
                console.print(
                    "[dim]Try different keywords or check 'navig docs' for all topics.[/dim]"
                )
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(results, 1):
                    console.print(
                        f"[bold white]{i}. {r.get('title', 'Untitled')}[/bold white]"
                    )
                    console.print(f"   [dim]{r.get('file')}[/dim]")
                    if r.get("excerpt"):
                        excerpt = (
                            r["excerpt"][:300] + "..."
                            if len(r.get("excerpt", "")) > 300
                            else r.get("excerpt", "")
                        )
                        console.print(f"   {excerpt}")
                    console.print()

    except ImportError as e:
        ch.error(f"Search tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Documentation search failed: {e}")
        raise typer.Exit(1) from e


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
    import json as jsonlib

    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")

    try:
        from navig.tools.web import web_fetch

        console.print(f"[dim]Fetching {url}...[/dim]") if not want_json else None

        result = web_fetch(
            url=url,
            extract_mode=mode,
            max_chars=max_chars,
            timeout_seconds=timeout,
        )

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "success": result.success,
                        "url": url,
                        "final_url": result.final_url,
                        "title": result.title,
                        "content": result.text[:max_chars] if result.text else None,
                        "truncated": result.truncated,
                        "error": result.error if not result.success else None,
                    },
                    indent=2,
                )
            )
        elif result.success:
            if want_plain:
                if result.title:
                    console.print(f"Title: {result.title}")
                console.print(f"URL: {result.final_url or url}\n")
                console.print(result.text)
            else:
                console.print(f"[bold cyan]{result.title or 'Untitled'}[/bold cyan]")
                console.print(f"[dim]{result.final_url or url}[/dim]\n")
                console.print(Markdown(result.text[:20000]))
                if result.truncated:
                    console.print(
                        "\n[yellow]Content truncated. Use --max-chars to increase limit.[/yellow]"
                    )
        else:
            ch.error(f"Failed to fetch URL: {result.error}")
            raise typer.Exit(1)

    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Fetch failed: {e}")
        raise typer.Exit(1) from e


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
        help="Search provider: auto, brave, duckduckgo",
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
    import json as jsonlib

    from rich.console import Console

    console = Console()
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")

    try:
        from navig.tools.web import web_search

        (
            console.print(f"[dim]Searching for '{query}'...[/dim]")
            if not want_json
            else None
        )

        result = web_search(
            query=query,
            count=limit,
        )

        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "success": result.success,
                        "query": query,
                        "results": (
                            [
                                {
                                    "title": r.title,
                                    "url": r.url,
                                    "snippet": r.snippet,
                                }
                                for r in result.results
                            ]
                            if result.results
                            else []
                        ),
                        "error": result.error if not result.success else None,
                    },
                    indent=2,
                )
            )
        elif result.success and result.results:
            if want_plain:
                for i, r in enumerate(result.results, 1):
                    console.print(f"{i}. {r.title}")
                    console.print(f"   {r.url}")
                    if r.snippet:
                        console.print(f"   {r.snippet[:200]}")
                    console.print()
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(result.results, 1):
                    console.print(f"[bold white]{i}. {r.title}[/bold white]")
                    console.print(f"   [blue underline]{r.url}[/blue underline]")
                    if r.snippet:
                        console.print(f"   [dim]{r.snippet[:200]}[/dim]")
                    console.print()
        elif result.success:
            console.print("[yellow]No results found.[/yellow]")
        else:
            ch.error(f"Search failed: {result.error}")
            console.print(
                "\n[dim]Tip: Set up Brave Search API for better results:[/dim]"
            )
            console.print("[dim]  1. Get key from https://brave.com/search/api/[/dim]")
            console.print(
                "[dim]  2. navig config set web.search.api_key=YOUR_KEY[/dim]"
            )
            raise typer.Exit(1)

    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"Search failed: {e}")
        raise typer.Exit(1) from e


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

    _C().print(
        "[yellow]Tip:[/yellow] use [bold]navig onboard[/bold] (this alias works too)"
    )
    from navig.commands.onboard import run_onboard

    run_onboard(flow=flow, non_interactive=non_interactive)


@app.command("init")
def init_command(
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
    profile: str = typer.Option(
        "",
        "--profile",
        "-p",
        help="Run installer profile without wizard: node, operator, architect, system_standard, system_deep",
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
    # ── Installer pipeline (non-interactive) ─────────────────────────────────
    if profile:
        from navig.installer import run_install
        from navig.installer.profiles import VALID_PROFILES

        if profile not in VALID_PROFILES:
            import typer as _t

            _t.echo(
                f"Unknown profile '{profile}'. " f"Valid: {', '.join(VALID_PROFILES)}",
                err=True,
            )
            raise SystemExit(1)

        run_install(profile=profile, dry_run=dry_run, quiet=quiet)
        return

    # ── Interactive wizard ────────────────────────────────────────────────────
    from navig.commands.init import _maybe_send_first_run_ping
    from navig.commands.onboard import run_onboard

    if reconfigure:
        run_onboard(flow="manual")
        return

    if settings:
        run_onboard(flow="manual")
        return

    run_onboard(flow="auto")

    try:
        _maybe_send_first_run_ping()
    except Exception:  # noqa: BLE001
        pass


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
    from navig.installer.contracts import InstallerContext, ModuleState
    from navig.installer.runner import rollback as run_rollback
    from navig.installer.state import load_last

    try:
        from navig.platform.paths import navig_config_dir

        config_dir = navig_config_dir()
    except Exception:
        import pathlib

        config_dir = pathlib.Path.home() / ".navig"

    records = load_last(config_dir=config_dir, profile=profile or None)
    if not records:
        ch.warning("No installer history found — nothing to roll back.")
        return

    from navig.installer.contracts import Action, Result

    # Reconstruct minimal Action / Result pairs from the manifest
    actions: list[Action] = []
    results: list[Result] = []
    for rec in records:
        a = Action(
            id=rec.get("action_id", "?"),
            description=rec.get("description", ""),
            module=rec.get("module", "?"),
            reversible=rec.get("reversible", False),
        )
        r = Result(
            action_id=a.id,
            state=ModuleState(rec.get("state", "skipped")),
            message=rec.get("message", ""),
            undo_data=rec.get("undo_data") or {},
        )
        actions.append(a)
        results.append(r)

    reversible = [
        (a, r)
        for a, r in zip(actions, results)
        if a.reversible and r.state == ModuleState.APPLIED
    ]
    if not reversible:
        ch.info("No reversible applied actions found in the last run.")
        return

    ch.info(f"Rolling back {len(reversible)} action(s):")
    for a, _ in reversible:
        ch.dim(f"  ↩  {a.description}")

    if dry_run:
        ch.warning("[dry-run] No changes made.")
        return

    ctx = InstallerContext(config_dir=config_dir, profile=profile or "?")
    run_rollback(actions=actions, results=results, ctx=ctx)
    ch.success("Rollback complete.")


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
    value: str | None = typer.Argument(
        None, help="New value to write (triggers write mode)"
    ),
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

task_app = typer.Typer(
    help="Task/workflow management (reusable command sequences)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(task_app, name="task")


@task_app.callback()
def task_callback(ctx: typer.Context):
    """Task management - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        from navig.commands.workflow import list_workflows

        list_workflows()


@task_app.command("list")
def task_list():
    """List all available tasks/workflows."""
    from navig.commands.workflow import list_workflows

    list_workflows()


@task_app.command("show")
def task_show(name: str = typer.Argument(..., help="Task name")):
    """Display task definition and steps."""
    from navig.commands.workflow import show_workflow

    show_workflow(name)


@task_app.command("run")
def task_run(
    name: str = typer.Argument(..., help="Task name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without executing"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Detailed output"),
    var: list[str] | None = typer.Option(
        None, "--var", "-V", help="Variable (name=value)"
    ),
):
    """Execute a task/workflow."""
    from navig.commands.workflow import run_workflow

    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@task_app.command("test")
def task_test(name: str = typer.Argument(..., help="Task name")):
    """Validate task syntax and structure."""
    from navig.commands.workflow import validate_workflow

    validate_workflow(name)


@task_app.command("add")
def task_add(
    name: str = typer.Argument(..., help="New task name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create globally"),
):
    """Create a new task from template."""
    from navig.commands.workflow import create_workflow

    create_workflow(name, global_scope=global_scope)


@task_app.command("remove")
def task_remove(
    name: str = typer.Argument(..., help="Task name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a task."""
    from navig.commands.workflow import delete_workflow

    delete_workflow(name, force=force)


@task_app.command("edit")
def task_edit(name: str = typer.Argument(..., help="Task name")):
    """Open task in default editor."""
    from navig.commands.workflow import edit_workflow

    edit_workflow(name)


@task_app.command("complete")
def task_complete(
    task_title: str = typer.Argument(..., help="Human-readable task title"),
    task_slug: str = typer.Argument(..., help="kebab-case unique slug"),
    summary: str = typer.Argument(..., help="One-sentence completion summary"),
    phase_name: str = typer.Argument(..., help="Phase name (e.g. phase-1)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Validate; skip all writes"
    ),
    now_date: str | None = typer.Option(
        None, "--date", "-d", help="Override date YYYY-MM-DD"
    ),
) -> None:
    """Record a completed task — runs complete-task.sh (Unix) or complete-task.ps1 (Windows)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Locate complete-task script by walking up from cwd
    cwd = Path.cwd()
    project_root: Path | None = None
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            project_root = parent
            break

    if project_root is None:
        typer.secho(
            "ERROR: could not find .navig/ directory within cwd ancestry",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    scripts_dir = project_root / ".navig" / "scripts"
    is_windows = sys.platform == "win32"
    script = scripts_dir / ("complete-task.ps1" if is_windows else "complete-task.sh")

    if not script.exists():
        typer.secho(f"ERROR: script not found at {script}", fg=typer.colors.RED)
        raise typer.Exit(1)

    env = os.environ.copy()
    if dry_run:
        env["NAVIG_DRY_RUN"] = "1"
    if now_date:
        env["NAVIG_NOW"] = now_date

    if is_windows:
        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            task_title,
            task_slug,
            summary,
            phase_name,
        ]
    else:
        cmd = ["bash", str(script), task_title, task_slug, summary, phase_name]

    result = subprocess.run(cmd, env=env, cwd=str(cwd))
    raise typer.Exit(result.returncode)


context_app = typer.Typer(
    help="Manage host/app context for current project",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(context_app, name="context")
app.add_typer(context_app, name="ctx", hidden=True)


@context_app.callback()
def context_callback(ctx: typer.Context):
    """Context management - shows current context if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.context import show_context

        show_context(ctx.obj)
        raise typer.Exit()


@context_app.command("show")
def context_show(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line output for scripting"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show current context resolution.

    Displays which host/app is active and where the context is resolved from
    (environment variable, project config, user cache, or default).

    Examples:
        navig context show
        navig context show --json
        navig context --plain
    """
    from navig.commands.context import show_context

    ctx.obj["plain"] = plain
    if json_out:
        ctx.obj["json"] = True
    show_context(ctx.obj)


@context_app.command("set")
def context_set(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", "-h", help="Host to set as project default"
    ),
    app_name: str | None = typer.Option(
        None, "--app", "-a", help="App to set as project default"
    ),
):
    """
    Set project-local context in .navig/config.yaml.

    This creates a project-specific context that takes precedence over
    the global user context (set with 'navig host use').

    Examples:
        navig context set --host production
        navig context set --host staging --app myapp
        navig context set --app backend
    """
    from navig.commands.context import set_context

    set_context(host=host, app=app_name, opts=ctx.obj)


@context_app.command("clear")
def context_clear(ctx: typer.Context):
    """
    Clear project-local context.

    Removes active_host and active_app from .navig/config.yaml.
    After clearing, context will resolve from global user settings.

    Examples:
        navig context clear
    """
    from navig.commands.context import clear_context

    clear_context(ctx.obj)


@context_app.command("init")
def context_init(ctx: typer.Context):
    """
    Initialize .navig directory in current project.

    Creates .navig/config.yaml with the current active host.
    If a legacy .navig file exists, it will be migrated.
    Also adds .navig/ to .gitignore if in a git repository.

    Examples:
        navig context init
    """
    from navig.commands.context import init_context

    init_context(ctx.obj)


# ============================================================================
# PROJECT INDEX COMMANDS
# ============================================================================

index_app = typer.Typer(
    help="Project source code indexer (BM25 search over workspace files)",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(index_app, name="index")


@index_app.command("scan")
def index_scan(
    ctx: typer.Context,
    root: str | None = typer.Argument(
        None, help="Project root directory (default: current directory)"
    ),
    incremental: bool = typer.Option(
        True,
        "--incremental/--full",
        help="Incremental scan (only changed files) or full rescan",
    ),
):
    """
    Scan and index project source code for BM25 search.

    Creates or updates a SQLite FTS5 index of all project files,
    chunked by function boundaries for code and paragraph boundaries for docs.

    Examples:
        navig index scan
        navig index scan /path/to/project --full
    """
    import time as _time  # noqa: F401
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()

    project_root = Path(root) if root else Path.cwd()
    if not project_root.is_dir():
        console.print(f"[red]Not a directory: {project_root}[/]")
        raise typer.Exit(1)

    with ProjectIndexer(project_root) as indexer:
        if incremental and indexer._file_hashes:
            console.print(f"[dim]Incremental scan of[/] [bold]{project_root}[/]")
            stats = indexer.update_incremental()
        else:
            console.print(f"[dim]Full scan of[/] [bold]{project_root}[/]")
            stats = indexer.scan()
        console.print(f"[green]✓[/] Indexed: {stats}")


@index_app.command("search")
def index_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    root: str | None = typer.Option(
        None, "--root", "-r", help="Project root directory"
    ),
    top_k: int = typer.Option(10, "--top", "-k", help="Max results to return"),
):
    """
    Search the project index using BM25 ranking.

    Returns the most relevant code/doc chunks matching the query.

    Examples:
        navig index search "authentication middleware"
        navig index search "database connection" --top 5
    """
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        if not indexer._file_hashes:
            console.print("[yellow]No index found. Run 'navig index scan' first.[/]")
            raise typer.Exit(1)

        results = indexer.search(query, top_k=top_k)
        if not results:
            console.print("[dim]No results found.[/]")
            raise typer.Exit(0)

        for r in results:
            score_str = f"[dim]({r.score:.2f})[/]"
            console.print(
                f"\n{score_str} [bold]{r.file_path}[/] L{r.start_line}-{r.end_line} [dim]({r.content_type})[/]"
            )
            # Show first 5 lines of each result
            lines = r.content.split("\n")[:5]
            for line in lines:
                console.print(f"  [dim]{line}[/]")
            if len(r.content.split("\n")) > 5:
                console.print(
                    f"  [dim]... ({len(r.content.split(chr(10)))} lines total)[/]"
                )


@index_app.command("stats")
def index_stats(
    ctx: typer.Context,
    root: str | None = typer.Option(
        None, "--root", "-r", help="Project root directory"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show project index statistics.

    Examples:
        navig index stats
        navig index stats --json
    """
    import json
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        stats = indexer.stats()
        if json_out:
            console.print(json.dumps(stats, indent=2))
        else:
            console.print(f"[bold]Project Index Stats[/] — {project_root}")
            for k, v in stats.items():
                console.print(f"  {k}: [cyan]{v}[/]")


@index_app.command("drop")
def index_drop(
    ctx: typer.Context,
    root: str | None = typer.Option(
        None, "--root", "-r", help="Project root directory"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Drop the project index (removes SQLite database).

    Examples:
        navig index drop
        navig index drop --yes
    """
    from pathlib import Path

    from rich.console import Console as _Con

    from navig.memory.project_indexer import ProjectIndexer

    console = _Con()

    project_root = Path(root) if root else Path.cwd()

    if not yes:
        import typer as _typer

        confirmed = _typer.confirm(f"Drop index for {project_root}?")
        if not confirmed:
            raise typer.Exit(0)

    with ProjectIndexer(project_root) as indexer:
        indexer.drop_index()
        console.print("[green]✓[/] Index dropped")


# ============================================================================
# HISTORY & REPLAY COMMANDS
# ============================================================================

history_app = typer.Typer(
    help="Command history, replay, and audit trail",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(history_app, name="history")
app.add_typer(history_app, name="hist", hidden=True)


@history_app.callback()
def history_callback(ctx: typer.Context):
    """History management - shows recent history if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.history import show_history

        show_history(limit=20, opts=ctx.obj)
        raise typer.Exit()


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-l", help="Number of entries to show"),
    host: str | None = typer.Option(None, "--host", "-h", help="Filter by host"),
    type_filter: str | None = typer.Option(
        None, "--type", "-t", help="Filter by operation type"
    ),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (success/failed)"
    ),
    search: str | None = typer.Option(
        None, "--search", "-q", help="Search in command text"
    ),
    since: str | None = typer.Option(
        None, "--since", help="Time filter (e.g., 1h, 24h, 7d)"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    List command history with filtering.

    Examples:
        navig history list
        navig history list --limit 50
        navig history list --host production
        navig history list --status failed --since 24h
        navig history list --search "docker" --json
    """
    from navig.commands.history import show_history

    ctx.obj["plain"] = plain
    if json_out:
        ctx.obj["json"] = True
    show_history(
        limit=limit,
        host=host,
        operation_type=type_filter,
        status=status,
        search=search,
        since=since,
        opts=ctx.obj,
    )


@history_app.command("show")
def history_show(
    ctx: typer.Context,
    op_id: str = typer.Argument(
        ..., help="Operation ID or index (1=last, 2=second-last)"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show detailed information about an operation.

    Examples:
        navig history show 1              # Show last operation
        navig history show op-20260208... # Show by ID
        navig history show 1 --json       # JSON output
    """
    from navig.commands.history import show_operation_details

    if json_out:
        ctx.obj["json"] = True
    show_operation_details(op_id, opts=ctx.obj)


@history_app.command("replay")
def history_replay(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to replay"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done"
    ),
    modify: str | None = typer.Option(
        None, "--modify", "-m", help="Modify command before replay"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Replay a previous operation.

    Examples:
        navig history replay 1                    # Replay last command
        navig history replay 1 --dry-run          # Preview only
        navig history replay 1 --modify "--host staging"
    """
    from navig.commands.history import replay_operation

    ctx.obj["yes"] = yes
    replay_operation(op_id, dry_run=dry_run, modify=modify, opts=ctx.obj)


@history_app.command("undo")
def history_undo(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to undo"),
):
    """
    Undo a reversible operation.

    Only works for operations that were marked as reversible
    and have undo data stored.

    Examples:
        navig history undo 1
    """
    from navig.commands.history import undo_operation

    undo_operation(op_id, opts=ctx.obj)


@history_app.command("export")
def history_export(
    ctx: typer.Context,
    output: str = typer.Argument(..., help="Output file path"),
    format: str = typer.Option(
        "json", "--format", "-f", help="Export format (json, csv)"
    ),
    limit: int = typer.Option(1000, "--limit", "-l", help="Max entries to export"),
):
    """
    Export operation history to file.

    Examples:
        navig history export audit.json
        navig history export audit.csv --format csv
        navig history export all.json --limit 10000
    """
    from navig.commands.history import export_history

    export_history(output, format=format, limit=limit, opts=ctx.obj)


@history_app.command("clear")
def history_clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Clear all operation history.

    Examples:
        navig history clear
        navig history clear --yes
    """
    from navig.commands.history import clear_history

    ctx.obj["yes"] = yes
    clear_history(opts=ctx.obj)


@history_app.command("stats")
def history_stats_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show history statistics.

    Examples:
        navig history stats
        navig history stats --json
    """
    from navig.commands.history import history_stats

    if json_out:
        ctx.obj["json"] = True
    history_stats(opts=ctx.obj)


# ============================================================================
# App MANAGEMENT COMMANDS
# ============================================================================

tunnel_app = typer.Typer(
    help="Manage SSH tunnels",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(tunnel_app, name="tunnel")
app.add_typer(tunnel_app, name="t", hidden=True)


@tunnel_app.callback()
def tunnel_callback(ctx: typer.Context):
    """Tunnel management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("tunnel", ctx)
        raise typer.Exit()


@tunnel_app.command("run")
def tunnel_run(ctx: typer.Context):
    """Start SSH tunnel for active server (canonical command)."""
    from navig.commands.tunnel import start_tunnel

    start_tunnel(ctx.obj)


@tunnel_app.command("start", hidden=True)
def tunnel_start(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel run'] Start SSH tunnel."""
    deprecation_warning("navig tunnel start", "navig tunnel run")
    from navig.commands.tunnel import start_tunnel

    start_tunnel(ctx.obj)


@tunnel_app.command("remove")
def tunnel_remove(ctx: typer.Context):
    """Stop and remove SSH tunnel (canonical command)."""
    from navig.commands.tunnel import stop_tunnel

    stop_tunnel(ctx.obj)


@tunnel_app.command("stop", hidden=True)
def tunnel_stop(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel remove'] Stop SSH tunnel."""
    deprecation_warning("navig tunnel stop", "navig tunnel remove")
    from navig.commands.tunnel import stop_tunnel

    stop_tunnel(ctx.obj)


@tunnel_app.command("update")
def tunnel_update(ctx: typer.Context):
    """Restart tunnel (canonical command)."""
    from navig.commands.tunnel import restart_tunnel

    restart_tunnel(ctx.obj)


@tunnel_app.command("restart", hidden=True)
def tunnel_restart(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel update'] Restart tunnel."""
    deprecation_warning("navig tunnel restart", "navig tunnel update")
    from navig.commands.tunnel import restart_tunnel

    restart_tunnel(ctx.obj)


@tunnel_app.command("show")
def tunnel_show(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text for scripting"
    ),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show tunnel status (canonical command)."""
    from navig.commands.tunnel import show_tunnel_status

    ctx.obj["plain"] = plain
    if json:
        ctx.obj["json"] = True
    show_tunnel_status(ctx.obj)


@tunnel_app.command("status", hidden=True)
def tunnel_status(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (running/stopped) for scripting"
    ),
):
    """[DEPRECATED: Use 'navig tunnel show'] Show tunnel status."""
    deprecation_warning("navig tunnel status", "navig tunnel show")
    from navig.commands.tunnel import show_tunnel_status

    ctx.obj["plain"] = plain
    show_tunnel_status(ctx.obj)


@tunnel_app.command("auto")
def tunnel_auto(ctx: typer.Context):
    """Auto-start tunnel if needed, auto-stop when done."""
    from navig.commands.tunnel import auto_tunnel

    auto_tunnel(ctx.obj)


# ============================================================================
# DATABASE COMMANDS (Unified 'db' group)
# ============================================================================

monitor_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host monitor'] Server monitoring",
    invoke_without_command=True,
    no_args_is_help=False,
    deprecated=True,
)
app.add_typer(monitor_app, name="monitor", hidden=True)


@monitor_app.callback()
def monitor_callback(ctx: typer.Context):
    """[DEPRECATED] Use 'navig host monitor' instead."""
    deprecation_warning("navig monitor", "navig host monitor")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_monitoring_menu

        launch_monitoring_menu()
        raise typer.Exit()


@monitor_app.command("show")
def monitor_show(
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
    """Show monitoring information (canonical command)."""
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
        # Default to health overview
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)


@monitor_app.command("run")
def monitor_run(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", help="Generate and save report"),
):
    """Run monitoring checks (canonical command)."""
    if report:
        from navig.commands.monitoring import generate_report

        generate_report(ctx.obj)
    else:
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)


@monitor_app.command("resources", hidden=True)
def monitor_resources_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --resources'] Monitor resources."""
    deprecation_warning("navig monitor resources", "navig monitor show --resources")
    from navig.commands.monitoring import monitor_resources

    monitor_resources(ctx.obj)


@monitor_app.command("disk", hidden=True)
def monitor_disk_new(
    ctx: typer.Context,
    threshold: int = typer.Option(
        80, "--threshold", "-t", help="Alert threshold percentage"
    ),
):
    """[DEPRECATED: Use 'navig monitor show --disk'] Monitor disk space."""
    deprecation_warning("navig monitor disk", "navig monitor show --disk")
    from navig.commands.monitoring import monitor_disk

    monitor_disk(threshold, ctx.obj)


@monitor_app.command("services", hidden=True)
def monitor_services_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --services'] Check service status."""
    deprecation_warning("navig monitor services", "navig monitor show --services")
    from navig.commands.monitoring import monitor_services

    monitor_services(ctx.obj)


@monitor_app.command("network", hidden=True)
def monitor_network_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --network'] Monitor network."""
    deprecation_warning("navig monitor network", "navig monitor show --network")
    from navig.commands.monitoring import monitor_network

    monitor_network(ctx.obj)


@monitor_app.command("health", hidden=True)
def monitor_health_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show'] Health check."""
    deprecation_warning("navig monitor health", "navig monitor show")
    from navig.commands.monitoring import health_check

    health_check(ctx.obj)


@monitor_app.command("report")
def monitor_report_new(ctx: typer.Context):
    """Generate comprehensive monitoring report and save to file."""
    from navig.commands.monitoring import generate_report

    generate_report(ctx.obj)


# Legacy aliases for backward compatibility (hidden)
@app.command("monitor-resources", hidden=True)
def monitor_resources_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor resources']"""
    ch.warning(
        "'navig monitor-resources' is deprecated. Use 'navig monitor resources' instead."
    )
    from navig.commands.monitoring import monitor_resources

    monitor_resources(ctx.obj)


@app.command("monitor-disk", hidden=True)
def monitor_disk_cmd(
    ctx: typer.Context,
    threshold: int = typer.Option(
        80, "--threshold", "-t", help="Alert threshold percentage"
    ),
):
    """[DEPRECATED: Use 'navig monitor disk']"""
    ch.warning("'navig monitor-disk' is deprecated. Use 'navig monitor disk' instead.")
    from navig.commands.monitoring import monitor_disk

    monitor_disk(threshold, ctx.obj)


@app.command("monitor-services", hidden=True)
def monitor_services_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor services']"""
    ch.warning(
        "'navig monitor-services' is deprecated. Use 'navig monitor services' instead."
    )
    from navig.commands.monitoring import monitor_services

    monitor_services(ctx.obj)


@app.command("monitor-network", hidden=True)
def monitor_network_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor network']"""
    ch.warning(
        "'navig monitor-network' is deprecated. Use 'navig monitor network' instead."
    )
    from navig.commands.monitoring import monitor_network

    monitor_network(ctx.obj)


@app.command("health-check", hidden=True)
def health_check_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor health']"""
    ch.warning(
        "'navig health-check' is deprecated. Use 'navig monitor health' instead."
    )
    from navig.commands.monitoring import health_check

    health_check(ctx.obj)


@app.command("monitoring-report", hidden=True)
def monitoring_report_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor report']"""
    ch.warning(
        "'navig monitoring-report' is deprecated. Use 'navig monitor report' instead."
    )
    from navig.commands.monitoring import generate_report

    generate_report(ctx.obj)


# ============================================================================
# SECURITY MANAGEMENT (Unified 'security' group)
# ============================================================================

security_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host security'] Security management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(security_app, name="security", hidden=True)  # Deprecated


@security_app.callback()
def security_callback(ctx: typer.Context):
    """Security management - DEPRECATED, use 'navig host security'."""
    deprecation_warning("navig security", "navig host security")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_security_menu

        launch_security_menu()
        raise typer.Exit()


@security_app.command("show")
def security_show(
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
    """Show security information (canonical command)."""
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
        # Default to security scan overview
        from navig.commands.security import security_scan

        security_scan(ctx.obj)


@security_app.command("run")
def security_run(ctx: typer.Context):
    """Run comprehensive security scan (canonical command)."""
    from navig.commands.security import security_scan

    security_scan(ctx.obj)


@security_app.command("firewall", hidden=True)
def security_firewall_new(ctx: typer.Context):
    """Display UFW firewall status and rules."""
    from navig.commands.security import firewall_status

    firewall_status(ctx.obj)


@security_app.command("firewall-add")
def security_firewall_add_new(
    ctx: typer.Context,
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option(
        "any", "--from", help="IP address or subnet (default: any)"
    ),
):
    """Add UFW firewall rule."""
    from navig.commands.security import firewall_add_rule

    firewall_add_rule(port, protocol, allow_from, ctx.obj)


@security_app.command("edit")
def security_edit(
    ctx: typer.Context,
    firewall: bool = typer.Option(
        False, "--firewall", "-f", help="Edit firewall rules"
    ),
    port: int | None = typer.Option(None, "--port", "-p", help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet"),
    add: bool = typer.Option(False, "--add", help="Add a rule"),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove a rule"),
    enable: bool = typer.Option(False, "--enable", help="Enable firewall"),
    disable: bool = typer.Option(False, "--disable", help="Disable firewall"),
    unban: str | None = typer.Option(
        None, "--unban", help="Unban IP address from fail2ban"
    ),
    jail: str | None = typer.Option(
        None, "--jail", "-j", help="Jail name for fail2ban"
    ),
):
    """Edit security settings (canonical command)."""
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
        from navig.console_helper import ch

        ch.error("Specify what to edit: --firewall or --unban")


@security_app.command("firewall-remove", hidden=True)
def security_firewall_remove_new(
    ctx: typer.Context,
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
):
    """Remove UFW firewall rule."""
    from navig.commands.security import firewall_remove_rule

    firewall_remove_rule(port, protocol, ctx.obj)


@security_app.command("firewall-enable")
def security_firewall_enable_new(ctx: typer.Context):
    """Enable UFW firewall."""
    from navig.commands.security import firewall_enable

    firewall_enable(ctx.obj)


@security_app.command("firewall-disable")
def security_firewall_disable_new(ctx: typer.Context):
    """Disable UFW firewall."""
    from navig.commands.security import firewall_disable

    firewall_disable(ctx.obj)


@security_app.command("fail2ban", hidden=True)
def security_fail2ban_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security show --fail2ban'] Show Fail2Ban status."""
    deprecation_warning("navig security fail2ban", "navig security show --fail2ban")
    from navig.commands.security import fail2ban_status

    fail2ban_status(ctx.obj)


@security_app.command("unban", hidden=True)
def security_unban_new(
    ctx: typer.Context,
    ip_address: str = typer.Argument(..., help="IP address to unban"),
    jail: str = typer.Option(
        None, "--jail", "-j", help="Jail name (default: all jails)"
    ),
):
    """[DEPRECATED: Use 'navig security edit --unban <ip>'] Unban IP."""
    deprecation_warning("navig security unban", "navig security edit --unban <ip>")
    from navig.commands.security import fail2ban_unban

    fail2ban_unban(ip_address, jail, ctx.obj)


@security_app.command("ssh", hidden=True)
def security_ssh_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security show --ssh'] SSH audit."""
    deprecation_warning("navig security ssh", "navig security show --ssh")
    from navig.commands.security import ssh_audit

    ssh_audit(ctx.obj)


@security_app.command("updates")
def security_updates_new(ctx: typer.Context):
    """Check for available security updates."""
    from navig.commands.security import check_security_updates

    check_security_updates(ctx.obj)


@security_app.command("connections")
def security_connections_new(ctx: typer.Context):
    """Audit active network connections."""
    from navig.commands.security import audit_connections

    audit_connections(ctx.obj)


@security_app.command("scan")
def security_scan_new(ctx: typer.Context):
    """Run comprehensive security scan."""
    from navig.commands.security import security_scan

    security_scan(ctx.obj)


# Legacy aliases for backward compatibility (hidden)
@app.command("firewall-status", hidden=True)
def firewall_status_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security firewall']"""
    ch.warning(
        "'navig firewall-status' is deprecated. Use 'navig security firewall' instead."
    )
    from navig.commands.security import firewall_status

    firewall_status(ctx.obj)


@app.command("firewall-add", hidden=True)
def firewall_add_cmd(
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option(
        "any", "--from", help="IP address or subnet (default: any)"
    ),
    ctx: typer.Context = typer.Context,
):
    """[DEPRECATED: Use 'navig security firewall-add']"""
    ch.warning(
        "'navig firewall-add' is deprecated. Use 'navig security firewall-add' instead."
    )
    from navig.commands.security import firewall_add_rule

    firewall_add_rule(port, protocol, allow_from, ctx.obj)


@app.command("firewall-remove", hidden=True)
def firewall_remove_cmd(
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    ctx: typer.Context = typer.Context,
):
    """[DEPRECATED: Use 'navig security firewall-remove']"""
    ch.warning(
        "'navig firewall-remove' is deprecated. Use 'navig security firewall-remove' instead."
    )
    from navig.commands.security import firewall_remove_rule

    firewall_remove_rule(port, protocol, ctx.obj)


@app.command("fail2ban-status", hidden=True)
def fail2ban_status_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security fail2ban']"""
    ch.warning(
        "'navig fail2ban-status' is deprecated. Use 'navig security fail2ban' instead."
    )
    from navig.commands.security import fail2ban_status

    fail2ban_status(ctx.obj)


@app.command("security-scan", hidden=True)
def security_scan_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security scan']"""
    ch.warning(
        "'navig security-scan' is deprecated. Use 'navig security scan' instead."
    )
    from navig.commands.security import security_scan

    security_scan(ctx.obj)


# ============================================================================
# SYSTEM MAINTENANCE (Unified 'system' group - Pillar 7)
# ============================================================================

system_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host maintenance'] System maintenance",
    invoke_without_command=True,
    no_args_is_help=False,
)
# Registration removed — navig.commands.system_cmd is the canonical source
# (registered below via `from navig.commands.system_cmd import system_app`)


@system_app.callback()
def system_callback(ctx: typer.Context):
    """System maintenance - DEPRECATED, use 'navig host maintenance'."""
    deprecation_warning("navig system", "navig host maintenance")
    if ctx.invoked_subcommand is None:
        from navig.commands.maintenance import system_info

        system_info(ctx.obj)


@system_app.command("show")
def system_show(
    ctx: typer.Context,
    info: bool = typer.Option(False, "--info", "-i", help="Show system information"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk usage"),
    memory: bool = typer.Option(False, "--memory", "-m", help="Show memory usage"),
    processes: bool = typer.Option(
        False, "--processes", "-p", help="Show running processes"
    ),
):
    """Show system information (canonical command)."""
    if disk:
        from navig.commands.monitoring import monitor_disk

        monitor_disk(80, ctx.obj)
    elif memory:
        from navig.commands.monitoring import monitor_resources

        monitor_resources(ctx.obj)
    elif processes:
        from navig.commands.remote import run_remote_command

        run_remote_command("ps aux --sort=-%mem | head -20", ctx.obj)
    else:
        from navig.commands.maintenance import system_info

        system_info(ctx.obj)


@system_app.command("run")
def system_run(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", "-u", help="Update system packages"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Clean package cache"),
    rotate_logs: bool = typer.Option(
        False, "--rotate-logs", "-r", help="Rotate log files"
    ),
    cleanup_temp: bool = typer.Option(
        False, "--cleanup-temp", "-t", help="Clean temp files"
    ),
    maintenance: bool = typer.Option(
        False, "--maintenance", "-m", help="Full maintenance"
    ),
    reboot: bool = typer.Option(False, "--reboot", help="Reboot server"),
):
    """Run system maintenance operations (canonical command)."""
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
    elif maintenance:
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
            "Specify an action: --update, --clean, --rotate-logs, --cleanup-temp, --maintenance, --reboot"
        )


@system_app.command("update")
def system_update(ctx: typer.Context):
    """Update system packages (alias for 'navig system run --update')."""
    from navig.commands.maintenance import update_packages

    update_packages(ctx.obj)


@system_app.command("clean")
def system_clean(ctx: typer.Context):
    """Clean package cache and orphans (alias for 'navig system run --clean')."""
    from navig.commands.maintenance import clean_packages

    clean_packages(ctx.obj)


@system_app.command("info")
def system_info_cmd(ctx: typer.Context):
    """Display comprehensive system information."""
    from navig.commands.maintenance import system_info

    system_info(ctx.obj)


@system_app.command("reboot")
def system_reboot(ctx: typer.Context):
    """Reboot the server (requires confirmation)."""
    from navig.commands.remote import run_remote_command

    if ctx.obj.get("yes") or typer.confirm(
        "Are you sure you want to reboot the server?"
    ):
        run_remote_command("sudo reboot", ctx.obj)


# Legacy flat commands for backward compatibility (hidden)
@app.command("update-packages", hidden=True)
def update_packages_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system update'] Update packages."""
    deprecation_warning("navig update-packages", "navig system update")
    from navig.commands.maintenance import update_packages

    update_packages(ctx.obj)


@app.command("clean-packages", hidden=True)
def clean_packages_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system clean'] Clean packages."""
    deprecation_warning("navig clean-packages", "navig system clean")
    from navig.commands.maintenance import clean_packages

    clean_packages(ctx.obj)


@app.command("rotate-logs", hidden=True)
def rotate_logs_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --rotate-logs'] Rotate logs."""
    deprecation_warning("navig rotate-logs", "navig system run --rotate-logs")
    from navig.commands.maintenance import rotate_logs

    rotate_logs(ctx.obj)


@app.command("cleanup-temp", hidden=True)
def cleanup_temp_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --cleanup-temp'] Cleanup temp."""
    deprecation_warning("navig cleanup-temp", "navig system run --cleanup-temp")
    from navig.commands.maintenance import cleanup_temp

    cleanup_temp(ctx.obj)


@app.command("check-filesystem", hidden=True)
def check_filesystem_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system show --disk'] Check filesystem."""
    deprecation_warning("navig check-filesystem", "navig system show --disk")
    from navig.commands.maintenance import check_filesystem

    check_filesystem(ctx.obj)


@app.command("system-maintenance", hidden=True)
def system_maintenance_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --maintenance'] Run maintenance."""
    deprecation_warning("navig system-maintenance", "navig system run --maintenance")
    from navig.commands.maintenance import system_maintenance

    system_maintenance(ctx.obj)


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
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Read command from file"
    ),
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
    confirm: bool = typer.Option(
        False, "--confirm", "-c", help="Force confirmation prompt"
    ),
    json: bool = typer.Option(
        False, "--json", help="Output JSON (captures stdout/stderr)"
    ),
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

    # Suggest using --b64 for risky/complex shell strings.
    if command and not b64 and not stdin and not file and not interactive:
        # Only warn about ACTUAL quoting/escaping problems
        # Safe: semicolons (;), pipes (|), redirects (>, <) - these work fine in quoted strings
        # Risky: nested quotes, JSON braces, dollar signs (variable expansion), backticks
        risky_markers = [
            '"\'"',  # nested quotes: "...'..."
            "'\"",  # nested quotes: '..."...'
            '{"',  # JSON object start
            '"}',  # JSON object end
            '["',  # JSON array with string
            '"]',  # JSON array with string
            "$(",  # command substitution
            "`",  # backticks (command substitution)
            "\\n",  # literal newlines in command string
            "\\t",  # literal tabs
        ]
        if any(m in command for m in risky_markers):
            ch.warning("This command looks complex; consider --b64 for safer quoting.")
            ch.dim('Example: navig run --b64 "curl -d \'{\\"k\\":\\"v\\"}\' ..."')
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
    run_remote_command(
        command, options, stdin=stdin, file=file, interactive=interactive
    )


@app.command("r", hidden=True)
def run_command_alias(
    ctx: typer.Context,
    command: str | None = typer.Argument(None, help="Alias for: navig run"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read command from stdin"),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Read command from file"
    ),
    b64: bool = typer.Option(False, "--b64", "-b", help="Base64 encode the command"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Open editor for multi-line input"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm prompts"),
    confirm: bool = typer.Option(
        False, "--confirm", "-c", help="Force confirmation prompt"
    ),
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
    refresh: int = typer.Option(
        5, "--refresh", "-r", help="Refresh interval in seconds"
    ),
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
    run_idx: int | None = typer.Option(
        None, "--run", "-r", help="Run suggestion by number"
    ),
    limit: int = typer.Option(8, "--limit", "-l", help="Number of suggestions"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show command without executing"
    ),
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


# ============================================================================
# EVENT-DRIVEN AUTOMATION (TRIGGERS)
# ============================================================================

trigger_app = typer.Typer(
    help="Event-driven automation triggers",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(trigger_app, name="trigger")


@trigger_app.callback()
def trigger_callback(ctx: typer.Context):
    """Event-driven automation triggers - run without subcommand for list."""
    if ctx.invoked_subcommand is None:
        from navig.commands.triggers import list_triggers

        list_triggers()
        raise typer.Exit()


@trigger_app.command("list")
def trigger_list(
    ctx: typer.Context,
    type_filter: str | None = typer.Option(
        None, "--type", "-t", help="Filter by trigger type"
    ),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (enabled/disabled)"
    ),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all configured triggers."""
    from navig.commands.triggers import list_triggers

    list_triggers(
        type_filter=type_filter,
        status_filter=status,
        tag=tag,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("show")
def trigger_show(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show detailed trigger information."""
    from navig.commands.triggers import show_trigger

    show_trigger(trigger_id, plain=plain, json_out=json_out)


@trigger_app.command("add")
def trigger_add(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Trigger name"),
    action: str | None = typer.Option(None, "--action", "-a", help="Action to execute"),
    trigger_type: str = typer.Option(
        "manual",
        "--type",
        "-t",
        help="Trigger type (health, schedule, threshold, manual)",
    ),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    schedule: str = typer.Option(
        "", "--schedule", help="Schedule expression (for schedule triggers)"
    ),
    host: str = typer.Option("", "--host", help="Target host (for threshold triggers)"),
    condition: str = typer.Option(
        "", "--condition", "-c", help="Condition (format: 'target op value')"
    ),
):
    """
    Create a new trigger.

    Interactive mode (no args):
        navig trigger add

    Quick mode:
        navig trigger add "Disk Alert" --action "notify:telegram" --type threshold --host prod --condition "disk gte 80"
        navig trigger add "Daily Backup" --action "workflow:backup" --type schedule --schedule "0 2 * * *"
        navig trigger add "Health Check" --action "host test" --type health

    Action formats:
        - navig command: "host list", "db dump", etc.
        - workflow: "workflow:deploy", "workflow:backup"
        - notify: "notify:telegram", "notify:console"
        - webhook: "webhook:https://example.com/hook"
    """
    from navig.commands.triggers import add_trigger_interactive, add_trigger_quick

    if name is None:
        # Interactive mode
        add_trigger_interactive()
    else:
        if not action:
            ch.error(
                "Action is required for quick mode. Use --action or run without args for interactive mode."
            )
            return
        add_trigger_quick(
            name=name,
            action=action,
            trigger_type=trigger_type,
            description=description,
            schedule=schedule,
            host=host,
            condition=condition,
        )


@trigger_app.command("remove")
def trigger_remove(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a trigger."""
    from navig.commands.triggers import remove_trigger

    remove_trigger(trigger_id, force=force)


@trigger_app.command("enable")
def trigger_enable(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to enable"),
):
    """Enable a disabled trigger."""
    from navig.commands.triggers import enable_trigger

    enable_trigger(trigger_id)


@trigger_app.command("disable")
def trigger_disable(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to disable"),
):
    """Disable a trigger (stops it from firing)."""
    from navig.commands.triggers import disable_trigger

    disable_trigger(trigger_id)


@trigger_app.command("test")
def trigger_test(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to test"),
):
    """
    Test a trigger (dry run).

    Shows what actions would be executed without actually running them.
    """
    from navig.commands.triggers import test_trigger

    test_trigger(trigger_id)


@trigger_app.command("fire")
def trigger_fire(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to fire"),
):
    """
    Manually fire a trigger.

    Executes all actions associated with the trigger immediately,
    regardless of conditions or cooldown.
    """
    from navig.commands.triggers import fire_trigger

    fire_trigger(trigger_id)


@trigger_app.command("history")
def trigger_history(
    ctx: typer.Context,
    trigger_id: str | None = typer.Argument(None, help="Filter by trigger ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show trigger execution history."""
    from navig.commands.triggers import show_trigger_history

    show_trigger_history(
        trigger_id=trigger_id,
        limit=limit,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("clear-history")
def trigger_clear_history(
    ctx: typer.Context,
    trigger_id: str | None = typer.Argument(
        None, help="Clear history for specific trigger only"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear trigger execution history."""
    from navig.commands.triggers import clear_trigger_history

    clear_trigger_history(trigger_id=trigger_id, force=force)


@trigger_app.command("stats")
def trigger_stats(ctx: typer.Context):
    """Show trigger statistics."""
    from navig.commands.triggers import show_trigger_stats

    show_trigger_stats()


# ============================================================================
# OPERATIONS INSIGHTS & ANALYTICS
# ============================================================================

insights_app = typer.Typer(
    help="Operations analytics and insights",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(insights_app, name="insights")


@insights_app.callback()
def insights_callback(ctx: typer.Context):
    """Operations insights - analytics on your command patterns."""
    if ctx.invoked_subcommand is None:
        from navig.commands.insights import show_insights_summary

        show_insights_summary()
        raise typer.Exit()


@insights_app.command("show")
def insights_show(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show insights summary with key metrics."""
    from navig.commands.insights import show_insights_summary

    show_insights_summary(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("hosts")
def insights_hosts(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show host health scores and trends.

    Calculates a health score (0-100) for each host based on:
    - Success rate (60% weight)
    - Average latency (40% weight)

    Also shows if host performance is improving, stable, or declining.
    """
    from navig.commands.insights import show_host_health

    show_host_health(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("commands")
def insights_commands(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", "-n", help="Number of commands to show"),
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show most frequently used commands with success rates."""
    from navig.commands.insights import show_top_commands

    show_top_commands(
        limit=limit, time_range=time_range, plain=plain, json_out=json_out
    )


@insights_app.command("time")
def insights_time(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show time-based usage patterns.

    Displays a breakdown of operations by hour, showing:
    - Activity levels throughout the day
    - Success rates per time period
    - Most common commands at each hour
    """
    from navig.commands.insights import show_time_patterns

    show_time_patterns(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("anomalies")
def insights_anomalies(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Detect unusual patterns and potential issues.

    Analyzes:
    - Error rate spikes
    - Unusual command frequencies
    - Inactive hosts
    - Performance degradation
    """
    from navig.commands.insights import show_anomalies

    show_anomalies(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("recommend")
def insights_recommend(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Get personalized recommendations based on your usage."""
    from navig.commands.insights import show_recommendations

    show_recommendations(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("report")
def insights_report(
    ctx: typer.Context,
    time_range: str = typer.Option(
        "week", "--range", "-r", help="Time range: today, week, month, all"
    ),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Generate a full analytics report.

    Includes:
    - Overall statistics
    - Host health scores
    - Top commands
    - Detected anomalies
    - Personalized recommendations

    Can be exported to JSON for further analysis.
    """
    from navig.commands.insights import generate_report

    generate_report(time_range=time_range, output_file=output, json_out=json_out)


# ============================================================================
# PACKS SYSTEM - Shareable Operations Bundles
# ============================================================================

pack_app = typer.Typer(
    help="Shareable operations bundles (runbooks, checklists, templates)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(pack_app, name="pack")
app.add_typer(pack_app, name="packs", hidden=True)


@pack_app.callback()
def pack_callback(ctx: typer.Context):
    """Packs - shareable operations bundles."""
    if ctx.invoked_subcommand is None:
        from navig.commands.packs import list_packs

        list_packs()
        raise typer.Exit()


@pack_app.command("list")
def pack_list(
    ctx: typer.Context,
    pack_type: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by type: workflow, runbook, checklist, template",
    ),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag"),
    installed: bool = typer.Option(
        False, "--installed", "-i", help="Show only installed packs"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List available packs."""
    from navig.commands.packs import list_packs

    list_packs(
        pack_type=pack_type,
        tag=tag,
        installed_only=installed,
        plain=plain,
        json_out=json_out,
    )


@pack_app.command("show")
def pack_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show pack details."""
    from navig.commands.packs import show_pack

    show_pack(name, plain=plain, json_out=json_out)


@pack_app.command("install")
def pack_install(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Pack name or path to install"),
    force: bool = typer.Option(False, "--force", "-f", help="Force reinstall"),
):
    """
    Install a pack.

    Sources:
    - Built-in pack name (e.g., "starter/deployment-checklist")
    - Local file path (e.g., "./my-pack.yaml")
    """
    from navig.commands.packs import install_pack

    install_pack(source, force=force)


@pack_app.command("uninstall")
def pack_uninstall(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name to uninstall"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Uninstall a pack."""
    from navig.commands.packs import uninstall_pack

    uninstall_pack(name, force=force)


@pack_app.command("run")
def pack_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name to run"),
    var: list[str] | None = typer.Option(
        None, "--var", "-v", help="Variables (key=value)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
):
    """
    Run a pack (execute its steps).

    Examples:
        navig pack run deployment-checklist
        navig pack run backup-runbook --var host=production
        navig pack run my-workflow --dry-run
    """
    from navig.commands.packs import run_pack

    # Parse variables
    variables = {}
    if var:
        for v in var:
            if "=" in v:
                key, value = v.split("=", 1)
                variables[key] = value

    run_pack(name, variables=variables, dry_run=dry_run, yes=yes)


@pack_app.command("create")
def pack_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name"),
    pack_type: str = typer.Option(
        "runbook", "--type", "-t", help="Pack type: workflow, runbook, checklist"
    ),
    description: str = typer.Option("", "--description", "-d", help="Pack description"),
):
    """Create a new pack in local packs directory."""
    from navig.commands.packs import create_pack

    create_pack(name, pack_type=pack_type, description=description)


@pack_app.command("search")
def pack_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Search for packs by name, description, or tags."""
    from navig.commands.packs import search_packs

    search_packs(query, plain=plain, json_out=json_out)


# ============================================================================
# INSTALL — Community asset installer (brain/<type>/)
# ============================================================================

install_app = typer.Typer(
    help="Install community assets (skills, playbooks, workflows, …) from GitHub.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(install_app, name="install")


@install_app.callback()
def install_callback(ctx: typer.Context):
    """Install community assets from GitHub into store/."""
    if ctx.invoked_subcommand is None:
        from navig.commands.install import list_assets

        list_assets()
        raise typer.Exit()


# ORIGIN, USER, NODE, BOOT, SPACE, BLUEPRINT, DECK, PORTABLE, MIGRATE, SYSTEM
# are now registered lazily via _EXTERNAL_CMD_MAP (see bottom of this file).
# This removes ~10 eager imports that fired on every CLI invocation.


@install_app.command("add")
def install_add(
    ctx: typer.Context,
    spec: str = typer.Argument(
        ..., help="type:owner/repo[@ref]  e.g. skill:myuser/my-skill"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite if already installed."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without writing files."
    ),
):
    """Install an asset from GitHub.

    SPEC format: <type>:<owner>/<repo>[@ref]

    Types: skill, playbook, workflow, formation, stack, plugin, tool, prompt, webflow,
    blueprint, deck

    Examples:

      navig install add skill:myuser/my-skill

      navig install add playbook:myorg/ops-pack@v1.2.0 --force
    """
    from navig.commands.install import install_asset

    try:
        install_asset(spec, force=force, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("list")
def install_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """List installed community assets."""
    from navig.commands.install import list_assets

    list_assets(plain=plain)


@install_app.command("remove")
def install_remove(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo  or  type/name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Remove an installed community asset."""
    from navig.commands.install import remove_asset

    try:
        remove_asset(spec, force=force)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("update")
def install_update(
    ctx: typer.Context,
    spec: str = typer.Argument(
        None, help="Specific asset to update (omit to update all)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Update one or all installed assets to latest."""
    from navig.commands.install import update_assets

    try:
        update_assets(spec, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("upgrade")
def install_upgrade(
    ctx: typer.Context,
    spec: str = typer.Argument(None, help="Specific asset (omit to upgrade all)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes."),
):
    """Upgrade all installed assets (alias for update)."""
    from navig.commands.install import update_assets

    try:
        update_assets(spec, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("show")
def install_show(
    ctx: typer.Context,
    spec: str = typer.Argument(..., help="type:owner/repo"),
):
    """Show details of an installed asset."""
    from navig.commands.install import show_asset

    try:
        show_asset(spec)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@install_app.command("freeze")
def install_freeze(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
):
    """Print installed assets as type/name==version specs."""
    from navig.commands.install import freeze_assets

    freeze_assets(plain=plain)


@install_app.command("status")
def install_status(ctx: typer.Context):
    """Show health of all installed assets."""
    from navig.commands.install import status_assets

    status_assets()


@install_app.command("search")
def install_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (name, description, tags)"),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Search the NAVIG community registry.

    Examples:
        navig install search docker
        navig install search backup --type playbook
        navig install search git --refresh
    """
    from navig.commands.install import search_assets  # type: ignore

    results = search_assets(query, asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        ch.warn(f"No assets found matching {query!r}.")
        ch.dim("  Try 'navig install browse' to see all available assets.")
        return

    if plain:
        for asset in results:
            ch.print(
                f"{asset.get('type','?')}:{asset.get('repo', asset.get('name','?'))}"
                f"  — {asset.get('description', '')}"
            )
        return

    from rich.table import Table

    table = Table(title=f"Registry search: {query!r}", show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Install", style="green")

    for asset in results:
        install_spec = (
            f"{asset.get('type','?')}:{asset.get('repo', asset.get('name',''))}"
        )
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("description", "")[:60],
            f"navig install add {install_spec}",
        )

    ch.console.print(table)
    ch.dim(f"\n{len(results)} result(s). Install with: navig install add <type>:<repo>")


@install_app.command("browse")
def install_browse(
    ctx: typer.Context,
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by asset type: skill, playbook, workflow, plugin, …",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force registry re-fetch."),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """
    Browse the NAVIG community registry.

    Examples:
        navig install browse
        navig install browse --type skill
        navig install browse --type playbook --plain
    """
    from navig.commands.install import browse_assets  # type: ignore

    assets = browse_assets(asset_type=type_filter, force_refresh=refresh)

    if json_out:
        import json as _json

        ch.print(_json.dumps(assets, indent=2, ensure_ascii=False))
        return

    if not assets:
        label = f" of type {type_filter!r}" if type_filter else ""
        ch.warn(f"Registry is empty{label}.")
        ch.dim("  Check your internet connection or run with --refresh.")
        return

    if plain:
        for asset in assets:
            ch.print(
                f"{asset.get('type','?')}  {asset.get('name','?')}  "
                f"{asset.get('description', '')}"
            )
        return

    from rich.table import Table

    title = "Community Registry" + (f" — {type_filter}" if type_filter else "")
    table = Table(title=title, show_lines=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="cyan")
    table.add_column("Author", style="dim")
    table.add_column("Description")

    for asset in assets:
        table.add_row(
            asset.get("type", "?"),
            asset.get("name", "?"),
            asset.get("author", "—"),
            asset.get("description", "")[:60],
        )

    ch.console.print(table)
    ch.dim(f"\n{len(assets)} asset(s). Install: navig install add <type>:<repo>")


# ============================================================================
# QUICK ACTIONS - Shortcuts for frequent operations
# ============================================================================

quick_app = typer.Typer(
    help="Quick action shortcuts for frequent operations",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(quick_app, name="quick")
app.add_typer(quick_app, name="q", hidden=True)


@quick_app.callback()
def quick_callback(ctx: typer.Context):
    """Quick actions - shortcuts for frequent operations."""
    if ctx.invoked_subcommand is None:
        from navig.commands.suggest import show_quick_actions

        show_quick_actions()
        raise typer.Exit()


@quick_app.command("list")
def quick_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all quick actions."""
    from navig.commands.suggest import show_quick_actions

    show_quick_actions(plain=plain, json_out=json_out)


@quick_app.command("run")
def quick_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Quick action name to run"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show command without executing"
    ),
):
    """
    Run a quick action by name.

    Examples:
        navig quick run deploy
        navig quick run backup --dry-run
        navig q run status
    """
    from navig.commands.suggest import run_quick_action

    run_quick_action(name, dry_run=dry_run)


@quick_app.command("add")
def quick_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Short name for the action"),
    command: str = typer.Argument(..., help="Full navig command to run"),
    description: str = typer.Option("", "--desc", "-d", help="Optional description"),
):
    """
    Add a quick action shortcut.

    Examples:
        navig quick add deploy "run 'cd /var/www && git pull'"
        navig quick add backup "db dump --output /tmp/backup.sql"
        navig quick add status "dashboard --no-live"

    Then run with: navig quick run deploy
    """
    from navig.commands.suggest import add_quick_action

    # Ensure command starts with navig
    if not command.startswith("navig "):
        command = f"navig {command}"

    add_quick_action(name, command, description)


@quick_app.command("remove")
def quick_remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Quick action name to remove"),
):
    """Remove a quick action."""
    from pathlib import Path

    import yaml

    from navig.config import get_config_manager

    config_manager = get_config_manager()
    quick_file = Path(config_manager.global_config_dir) / "quick_actions.yaml"

    if not quick_file.exists():
        ch.error(f"Quick action '{name}' not found.")
        return

    with open(quick_file, encoding="utf-8") as f:
        actions = yaml.safe_load(f) or {}

    if name not in actions:
        ch.error(f"Quick action '{name}' not found.")
        return

    del actions[name]

    with open(quick_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(actions, f, default_flow_style=False)

    ch.success(f"Removed quick action: {name}")


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
hosts_app = typer.Typer(
    help="System hosts file management (view, edit, add entries)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(hosts_app, name="hosts")


@hosts_app.callback()
def hosts_callback(ctx: typer.Context):
    """Hosts file operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("hosts", ctx)
        raise typer.Exit()


@hosts_app.command("view")
def hosts_view_cmd(ctx: typer.Context):
    """View the system hosts file with syntax highlighting."""
    from navig.commands.local import hosts_view

    hosts_view(ctx.obj)


@hosts_app.command("edit")
def hosts_edit_cmd(ctx: typer.Context):
    """Open hosts file in editor (requires admin)."""
    from navig.commands.local import hosts_edit

    hosts_edit(ctx.obj)


@hosts_app.command("add")
def hosts_add_cmd(
    ctx: typer.Context,
    ip: str = typer.Argument(..., help="IP address"),
    hostname: str = typer.Argument(..., help="Hostname to add"),
):
    """Add an entry to the hosts file (requires admin)."""
    from navig.commands.local import hosts_add

    hosts_add(ip, hostname, ctx.obj)


# Software/package management
software_app = typer.Typer(
    help="Local software package management (list, search)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(software_app, name="software")


@software_app.callback()
def software_callback(ctx: typer.Context):
    """Software management - run without subcommand to list packages."""
    if ctx.invoked_subcommand is None:
        from navig.commands.local import software_list

        software_list(ctx.obj)
        raise typer.Exit()


@software_app.command("list")
def software_list_cmd(
    ctx: typer.Context,
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Limit number of results"
    ),
):
    """List installed software packages."""
    from navig.commands.local import software_list

    ctx.obj["limit"] = limit
    software_list(ctx.obj)


@software_app.command("search")
def software_search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search term"),
):
    """Search installed packages by name."""
    from navig.commands.local import software_search

    software_search(query, ctx.obj)


# Local system management
local_app = typer.Typer(
    help="Local machine management (system info, security, network)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(local_app, name="local")


@local_app.callback()
def local_callback(ctx: typer.Context):
    """Local system management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("local", ctx)
        raise typer.Exit()


@local_app.command("show")
def local_show_cmd(
    ctx: typer.Context,
    info: bool = typer.Option(True, "--info", "-i", help="Show system information"),
    resources: bool = typer.Option(
        False, "--resources", "-r", help="Show resource usage"
    ),
):
    """Show local system information."""
    if resources:
        from navig.commands.local import resource_usage

        resource_usage(ctx.obj)
    else:
        from navig.commands.local import system_info

        system_info(ctx.obj)


@local_app.command("audit")
def local_audit_cmd(
    ctx: typer.Context,
    ai: bool = typer.Option(False, "--ai", "-a", help="Include AI analysis"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed information"
    ),
):
    """Run local security audit."""
    from navig.commands.local import security_audit

    ctx.obj["ai"] = ai
    ctx.obj["verbose"] = verbose
    security_audit(ctx.obj)


@local_app.command("ports")
def local_ports_cmd(ctx: typer.Context):
    """Show open/listening ports on local machine."""
    from navig.commands.local import security_ports

    security_ports(ctx.obj)


@local_app.command("firewall")
def local_firewall_cmd(ctx: typer.Context):
    """Show local firewall status."""
    from navig.commands.local import security_firewall

    security_firewall(ctx.obj)


@local_app.command("ping")
def local_ping_cmd(
    ctx: typer.Context,
    host: str = typer.Argument(..., help="Host to ping"),
    count: int = typer.Option(4, "--count", "-c", help="Number of pings"),
):
    """Ping a host from local machine."""
    from navig.commands.local import network_ping

    network_ping(host, count, ctx.obj)


@local_app.command("dns")
def local_dns_cmd(
    ctx: typer.Context,
    hostname: str = typer.Argument(..., help="Hostname to lookup"),
):
    """Perform DNS lookup."""
    from navig.commands.local import network_dns

    network_dns(hostname, ctx.obj)


@local_app.command("interfaces")
def local_interfaces_cmd(ctx: typer.Context):
    """Show network interfaces."""
    from navig.commands.local import network_interfaces

    network_interfaces(ctx.obj)


# ============================================================================
# WEB SERVER MANAGEMENT (Unified 'web' group)
# ============================================================================

web_app = typer.Typer(
    help="Web server management (Nginx/Apache vhosts, sites, modules)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(web_app, name="web")


@web_app.callback()
def web_callback(ctx: typer.Context):
    """Web server management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("web", ctx)
        raise typer.Exit()


@web_app.command("vhosts")
def web_vhosts_new(ctx: typer.Context):
    """List virtual hosts (enabled and available)."""
    from navig.commands.webserver import list_vhosts

    list_vhosts(ctx.obj)


@web_app.command("test")
def web_test_new(ctx: typer.Context):
    """Test web server configuration syntax."""
    from navig.commands.webserver import test_config

    test_config(ctx.obj)


@web_app.command("enable")
def web_enable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to enable"),
):
    """Enable a web server site."""
    from navig.commands.webserver import enable_site

    ctx.obj["site_name"] = site_name
    enable_site(ctx.obj)


@web_app.command("disable")
def web_disable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to disable"),
):
    """Disable a web server site."""
    from navig.commands.webserver import disable_site

    ctx.obj["site_name"] = site_name
    disable_site(ctx.obj)


@web_app.command("module-enable")
def web_module_enable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to enable"),
):
    """Enable Apache module (Apache only)."""
    from navig.commands.webserver import enable_module

    ctx.obj["module_name"] = module_name
    enable_module(ctx.obj)


@web_app.command("module-disable")
def web_module_disable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to disable"),
):
    """Disable Apache module (Apache only)."""
    from navig.commands.webserver import disable_module

    ctx.obj["module_name"] = module_name
    disable_module(ctx.obj)


@web_app.command("reload")
def web_reload_new(ctx: typer.Context):
    """Safely reload web server (tests config first)."""
    from navig.commands.webserver import reload_server

    reload_server(ctx.obj)


@web_app.command("recommend")
def web_recommend_new(ctx: typer.Context):
    """Display performance tuning recommendations."""
    from navig.commands.webserver import get_recommendations

    get_recommendations(ctx.obj)


# Nested: web hestia (HestiaCP panel management)
web_hestia_app = typer.Typer(
    help="HestiaCP control panel management",
    invoke_without_command=True,
    no_args_is_help=False,
)
web_app.add_typer(web_hestia_app, name="hestia")


@web_hestia_app.callback()
def web_hestia_callback(ctx: typer.Context):
    """HestiaCP management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_hestia_menu

        launch_hestia_menu()
        raise typer.Exit()


@web_hestia_app.command("list")
def web_hestia_list(
    ctx: typer.Context,
    users: bool = typer.Option(False, "--users", "-u", help="List HestiaCP users"),
    domains: bool = typer.Option(
        False, "--domains", "-d", help="List HestiaCP domains"
    ),
    user_filter: str | None = typer.Option(
        None, "--user", help="Filter domains by username"
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List HestiaCP resources (users, domains)."""
    ctx.obj["plain"] = plain
    if users:
        from navig.commands.hestia import list_domains_cmd

        list_domains_cmd(user_filter, ctx.obj)
    else:
        # Default: show users
        from navig.commands.hestia import list_users_cmd

        list_users_cmd(ctx.obj)


@web_hestia_app.command("add")
def web_hestia_add(
    ctx: typer.Context,
    resource: str = typer.Argument(..., help="Resource type: user or domain"),
    name: str = typer.Argument(..., help="Username or domain name"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Password (for user)"
    ),
    email: str | None = typer.Option(None, "--email", "-e", help="Email (for user)"),
    user: str | None = typer.Option(None, "--user", "-u", help="Username (for domain)"),
):
    """Add HestiaCP user or domain."""
    if resource == "user":
        if not password or not email:
            ch.error("Password and email required for user creation")
            raise typer.Exit(1)
        from navig.commands.hestia import add_user_cmd

        add_user_cmd(name, password, email, ctx.obj)
    elif resource == "domain":
        if not user:
            ch.error("Username required for domain creation (--user)")
            raise typer.Exit(1)
        from navig.commands.hestia import add_domain_cmd

        add_domain_cmd(user, name, ctx.obj)
    else:
        ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
        raise typer.Exit(1)


@web_hestia_app.command("remove")
def web_hestia_remove(
    ctx: typer.Context,
    resource: str = typer.Argument(..., help="Resource type: user or domain"),
    name: str = typer.Argument(..., help="Username or domain name"),
    user: str | None = typer.Option(None, "--user", "-u", help="Username (for domain)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force deletion without confirmation"
    ),
):
    """Remove HestiaCP user or domain."""
    ctx.obj["force"] = force
    if resource == "user":
        from navig.commands.hestia import delete_user_cmd

        delete_user_cmd(name, ctx.obj)
    elif resource == "domain":
        if not user:
            ch.error("Username required for domain deletion (--user)")
            raise typer.Exit(1)
        from navig.commands.hestia import delete_domain_cmd

        delete_domain_cmd(user, name, ctx.obj)
    else:
        ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
        raise typer.Exit(1)


# Legacy aliases for backward compatibility (hidden)
@app.command("webserver-list-vhosts", hidden=True)
def webserver_list_vhosts_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web vhosts']"""
    ch.warning(
        "'navig webserver-list-vhosts' is deprecated. Use 'navig web vhosts' instead."
    )
    from navig.commands.webserver import list_vhosts

    list_vhosts(ctx.obj)


@app.command("webserver-test-config", hidden=True)
def webserver_test_config_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web test']"""
    ch.warning(
        "'navig webserver-test-config' is deprecated. Use 'navig web test' instead."
    )
    from navig.commands.webserver import test_config

    test_config(ctx.obj)


@app.command("webserver-reload", hidden=True)
def webserver_reload_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web reload']"""
    ch.warning(
        "'navig webserver-reload' is deprecated. Use 'navig web reload' instead."
    )
    from navig.commands.webserver import reload_server

    reload_server(ctx.obj)


# ============================================================================
# FILE OPERATIONS (Legacy flat commands - deprecated, use 'navig file' group)
# ============================================================================


@app.command("upload", hidden=True)
def upload_file(
    ctx: typer.Context,
    local: Path = typer.Argument(..., help="Local file/directory path"),
    remote: str | None = typer.Argument(
        None,
        help="Remote path (smart detection if omitted)",
    ),
):
    """[DEPRECATED: Use 'navig file add'] Upload file/directory."""
    deprecation_warning("navig upload", "navig file add")
    from navig.commands.files import upload_file_cmd

    upload_file_cmd(local, remote, ctx.obj)


@app.command("download", hidden=True)
def download_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    local: Path | None = typer.Argument(
        None,
        help="Local path (smart detection if omitted)",
    ),
):
    """[DEPRECATED: Use 'navig file show --download'] Download file/directory."""
    deprecation_warning("navig download", "navig file show --download")
    from navig.commands.files import download_file_cmd

    download_file_cmd(remote, local, ctx.obj)


@app.command("list", hidden=True)
def list_remote(
    ctx: typer.Context,
    remote_path: str = typer.Argument(..., help="Remote directory path"),
):
    """[DEPRECATED: Use 'navig file list'] List remote directory."""
    deprecation_warning("navig list", "navig file list")
    from navig.commands.files import list_remote_directory

    list_remote_directory(remote_path, ctx.obj)


@app.command("delete", hidden=True)
def delete_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path to delete"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Delete directories recursively"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force deletion without confirmation"
    ),
):
    """[DEPRECATED: Use 'navig file remove'] Delete remote file/directory."""
    deprecation_warning("navig delete", "navig file remove")
    from navig.commands.files_advanced import delete_file_cmd

    ctx.obj["recursive"] = recursive
    ctx.obj["force"] = force
    delete_file_cmd(remote, ctx.obj)


@app.command("mkdir", hidden=True)
def make_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path to create"),
    parents: bool = typer.Option(
        True, "--parents", "-p", help="Create parent directories as needed"
    ),
    mode: str = typer.Option("755", "--mode", "-m", help="Permission mode (e.g., 755)"),
):
    """[DEPRECATED: Use 'navig file add --dir'] Create remote directory."""
    deprecation_warning("navig mkdir", "navig file add --dir")
    from navig.commands.files_advanced import mkdir_cmd

    ctx.obj["parents"] = parents
    ctx.obj["mode"] = mode
    mkdir_cmd(remote, ctx.obj)


@app.command("chmod", hidden=True)
def change_permissions(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    mode: str = typer.Argument(..., help="Permission mode (e.g., 755, 644)"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Apply recursively"
    ),
):
    """[DEPRECATED: Use 'navig file edit --mode'] Change permissions."""
    deprecation_warning("navig chmod", "navig file edit --mode")
    from navig.commands.files_advanced import chmod_cmd

    ctx.obj["recursive"] = recursive
    chmod_cmd(remote, mode, ctx.obj)


@app.command("chown", hidden=True)
def change_owner(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    owner: str = typer.Argument(..., help="New owner (user or user:group)"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Apply recursively"
    ),
):
    """[DEPRECATED: Use 'navig file edit --owner'] Change ownership."""
    deprecation_warning("navig chown", "navig file edit --owner")
    from navig.commands.files_advanced import chown_cmd

    ctx.obj["recursive"] = recursive
    chown_cmd(remote, owner, ctx.obj)


@app.command("cat", hidden=True)
def cat_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path to read"),
    lines: int | None = typer.Option(
        None, "--lines", "-n", help="Number of lines to show"
    ),
    head: bool = typer.Option(
        False, "--head", help="Show first N lines (use with --lines)"
    ),
    tail: bool = typer.Option(
        False, "--tail", "-t", help="Show last N lines (use with --lines)"
    ),
):
    """[DEPRECATED: Use 'navig file show'] Read remote file contents."""
    deprecation_warning("navig cat", "navig file show")
    from navig.commands.files_advanced import cat_file_cmd

    cat_file_cmd(remote, ctx.obj, lines=lines, head=head, tail=tail)


@app.command("write-file", hidden=True)
def write_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path to write"),
    content: str | None = typer.Option(
        None, "--content", "-c", help="Content to write"
    ),
    stdin: bool = typer.Option(
        False, "--stdin", "-s", help="Read content from stdin (pipe)"
    ),
    from_file: Path | None = typer.Option(
        None, "--from-file", "-f", help="Read content from local file"
    ),
    append: bool = typer.Option(
        False, "--append", "-a", help="Append to file instead of overwrite"
    ),
    mode: str | None = typer.Option(
        None, "--mode", "-m", help="Set file permissions after writing"
    ),
    owner: str | None = typer.Option(
        None, "--owner", "-o", help="Set file owner after writing"
    ),
):
    """[DEPRECATED: Use 'navig file edit --content'] Write to remote file."""
    deprecation_warning("navig write-file", "navig file edit --content")
    from navig.commands.files_advanced import write_file_cmd

    write_file_cmd(
        remote,
        content,
        ctx.obj,
        stdin=stdin,
        local_file=from_file,
        append=append,
        mode=mode,
        owner=owner,
    )


@app.command("ls", hidden=True)
def ls_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path"),
    all: bool = typer.Option(False, "--all", "-a", help="Show hidden files"),
    long: bool = typer.Option(True, "--long", "-l", help="Long format with details"),
    human: bool = typer.Option(True, "--human", "-h", help="Human-readable sizes"),
):
    """[DEPRECATED: Use 'navig file list'] List remote directory."""
    deprecation_warning("navig ls", "navig file list")
    from navig.commands.files_advanced import list_dir_cmd

    list_dir_cmd(remote, ctx.obj, all=all, long=long, human=human)


@app.command("tree", hidden=True)
def tree_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path"),
    depth: int = typer.Option(2, "--depth", "-d", help="Maximum depth to display"),
    dirs_only: bool = typer.Option(
        False, "--dirs-only", "-D", help="Show only directories"
    ),
):
    """[DEPRECATED: Use 'navig file list --tree'] Show directory tree."""
    deprecation_warning("navig tree", "navig file list --tree")
    from navig.commands.files_advanced import tree_cmd

    tree_cmd(remote, ctx.obj, depth=depth, dirs_only=dirs_only)


# ============================================================================
# DOCKER MANAGEMENT COMMANDS
# ============================================================================

# docker ─ QUANTUM VELOCITY A: dispatched lazily via _EXTERNAL_CMD_MAP
# (navig.commands.docker :: docker_app)  —  175 inline lines removed from cold-start path


# ============================================================================
# ADVANCED DATABASE COMMANDS (DEPRECATED - use 'navig db <subcommand>')
# ============================================================================


@app.command("db-list", hidden=True)
def list_databases(ctx: typer.Context):
    """[DEPRECATED] List all databases with sizes. Use: navig db list"""
    deprecation_warning("navig db-list", "navig db list")
    from navig.commands.database_advanced import list_databases_cmd

    list_databases_cmd(ctx.obj)


@app.command("db-tables", hidden=True)
def list_tables(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
):
    """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
    deprecation_warning("navig db-tables", "navig db tables")
    from navig.commands.database_advanced import list_tables_cmd

    list_tables_cmd(database, ctx.obj)


@app.command("db-optimize", hidden=True)
def optimize_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to optimize"),
):
    """[DEPRECATED] Optimize database table. Use: navig db optimize <table>"""
    deprecation_warning("navig db-optimize", "navig db optimize")
    from navig.commands.database_advanced import optimize_table_cmd

    optimize_table_cmd(table, ctx.obj)


@app.command("db-repair", hidden=True)
def repair_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to repair"),
):
    """[DEPRECATED] Repair database table. Use: navig db repair <table>"""
    deprecation_warning("navig db-repair", "navig db repair")
    from navig.commands.database_advanced import repair_table_cmd

    repair_table_cmd(table, ctx.obj)


@app.command("db-users", hidden=True)
def list_db_users(ctx: typer.Context):
    """[DEPRECATED] List database users. Use: navig db users"""
    deprecation_warning("navig db-users", "navig db users")
    from navig.commands.database_advanced import list_users_cmd

    list_users_cmd(ctx.obj)


# ============================================================================
# DOCKER DATABASE COMMANDS (DEPRECATED - use 'navig db <subcommand>')
# ============================================================================


@app.command("db-containers", hidden=True)
def db_containers(ctx: typer.Context):
    """[DEPRECATED] List Docker containers running database services. Use: navig db containers"""
    deprecation_warning("navig db-containers", "navig db containers")
    from navig.commands.db import db_containers_cmd

    db_containers_cmd(ctx.obj)


@app.command("db-query", hidden=True)
def db_query(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
):
    """[DEPRECATED] Execute SQL query on remote database. Use: navig db run <query>"""
    deprecation_warning("navig db-query", "navig db run")
    from navig.commands.db import db_query_cmd

    db_query_cmd(query, container, user, password, database, db_type, ctx.obj)


@app.command("db-databases", hidden=True)
def db_databases(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one database per line) for scripting"
    ),
):
    """[DEPRECATED] List all databases on remote server. Use: navig db list"""
    deprecation_warning("navig db-databases", "navig db list")
    from navig.commands.db import db_list_cmd

    ctx.obj["plain"] = plain
    db_list_cmd(container, user, password, db_type, ctx.obj)


@app.command("db-show-tables", hidden=True)
def db_show_tables(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one table per line) for scripting"
    ),
):
    """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
    deprecation_warning("navig db-show-tables", "navig db tables")
    from navig.commands.db import db_tables_cmd

    ctx.obj["plain"] = plain
    db_tables_cmd(database, container, user, password, db_type, ctx.obj)


@app.command("db-dump", hidden=True)
def db_dump(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name to dump"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output file path"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
):
    """[DEPRECATED] Dump/backup a database from remote server. Use: navig db dump"""
    deprecation_warning("navig db-dump", "navig db dump")
    from navig.commands.db import db_dump_cmd

    db_dump_cmd(database, output, container, user, password, db_type, ctx.obj)


@app.command("db-shell", hidden=True)
def db_shell(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
):
    """[DEPRECATED] Open interactive database shell via SSH. Use: navig db run --shell"""
    deprecation_warning("navig db-shell", "navig db run --shell")
    from navig.commands.db import db_shell_cmd

    db_shell_cmd(container, user, password, database, db_type, ctx.obj)


# ============================================================================
# SERVER MONITORING & MANAGEMENT (DEPRECATED - use 'navig log/monitor/system')
# ============================================================================


@app.command("logs", hidden=True)
def view_logs(
    ctx: typer.Context,
    service: str = typer.Argument(
        ..., help="Service name (nginx, php-fpm, mysql, app, etc.)"
    ),
    tail: bool = typer.Option(False, "--tail", "-f", help="Follow logs in real-time"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to display"),
):
    """[DEPRECATED] View logs. Use: navig log show <service>"""
    deprecation_warning("navig logs", "navig log show")
    from navig.commands.monitoring import view_service_logs

    view_service_logs(service, tail, lines, ctx.obj)


@app.command("health", hidden=True)
def health_check(ctx: typer.Context):
    """[DEPRECATED] Run health checks. Use: navig monitor show"""
    deprecation_warning("navig health", "navig monitor show")
    from navig.commands.monitoring import run_health_check

    run_health_check(ctx.obj)


@app.command("restart", hidden=True)
def restart_service(
    ctx: typer.Context,
    service: str = typer.Argument(
        ..., help="Service to restart (nginx|php-fpm|mysql|app|docker|all)"
    ),
):
    """[DEPRECATED] Restart service. Use: navig system run --restart <service>"""
    deprecation_warning("navig restart", "navig system run --restart")
    from navig.commands.monitoring import restart_remote_service

    restart_remote_service(service, ctx.obj)


# ============================================================================
# AI ASSISTANT (Unified 'ai' group - Pillar 6: Intelligence)
# ============================================================================

ai_app = typer.Typer(
    help="AI-powered assistance for diagnostics, optimization, and knowledge",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(ai_app, name="ai")


@ai_app.callback()
def ai_callback(ctx: typer.Context):
    """AI Assistant - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("ai", ctx)
        raise typer.Exit()


@ai_app.command("ask")
def ai_ask(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override default AI model",
    ),
):
    """Ask AI about server/configuration (canonical command)."""
    from navig.commands.ai import ask_ai

    ask_ai(question, model, ctx.obj)


@ai_app.command("explain")
def ai_explain(
    ctx: typer.Context,
    log_file: str = typer.Argument(..., help="Log file path to explain"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to analyze"),
):
    """Explain logs/errors using AI."""
    from navig.commands.ai import ask_ai

    question = f"Analyze and explain the last {lines} lines of the log file at {log_file}. Identify any errors, warnings, or issues and suggest solutions."
    ask_ai(question, None, ctx.obj)


@ai_app.command("diagnose")
def ai_diagnose(ctx: typer.Context):
    """AI-powered issue diagnosis based on system state."""
    from navig.commands.assistant import analyze_cmd

    analyze_cmd(ctx.obj)


@ai_app.command("suggest")
def ai_suggest(ctx: typer.Context):
    """Get AI-powered optimization suggestions."""
    from navig.commands.ai import ask_ai

    question = "Analyze the current server configuration and suggest optimizations for performance, security, and reliability."
    ask_ai(question, None, ctx.obj)


@ai_app.command("show")
def ai_show(
    ctx: typer.Context,
    status: bool = typer.Option(False, "--status", "-s", help="Show assistant status"),
    context: bool = typer.Option(
        False, "--context", "-c", help="Show AI context summary"
    ),
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Copy context to clipboard"
    ),
    file: str | None = typer.Option(None, "--file", help="Save context to file"),
):
    """Show AI assistant information (canonical command)."""
    if status:
        from navig.commands.assistant import status_cmd

        status_cmd(ctx.obj)
    elif context:
        from navig.commands.assistant import context_cmd

        context_cmd(ctx.obj, clipboard, file)
    else:
        from navig.commands.assistant import status_cmd

        status_cmd(ctx.obj)


@ai_app.command("run")
def ai_run(
    ctx: typer.Context,
    analyze: bool = typer.Option(False, "--analyze", "-a", help="Run system analysis"),
    reset: bool = typer.Option(False, "--reset", "-r", help="Reset learning data"),
):
    """Run AI operations (canonical command)."""
    if analyze:
        from navig.commands.assistant import analyze_cmd

        analyze_cmd(ctx.obj)
    elif reset:
        from navig.commands.assistant import reset_cmd

        reset_cmd(ctx.obj)
    else:
        from navig.commands.assistant import analyze_cmd

        analyze_cmd(ctx.obj)


@ai_app.command("edit")
def ai_edit(ctx: typer.Context):
    """Configure AI assistant settings (interactive wizard)."""
    from navig.commands.assistant import config_cmd

    config_cmd(ctx.obj)


@ai_app.command("models")
def ai_models(
    ctx: typer.Context,
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="Filter by provider (e.g., openai, airllm)"
    ),
):
    """List available AI models from all providers.

    Examples:
        navig ai models
        navig ai models --provider airllm
        navig ai models --provider openai
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    try:
        from navig.providers import BUILTIN_PROVIDERS

        console.print("[bold cyan]Available AI Models[/bold cyan]")
        console.print()

        for pname, pconfig in BUILTIN_PROVIDERS.items():
            # Filter by provider if specified
            if provider and pname.lower() != provider.lower():
                continue

            if not pconfig.models:
                if pname == "ollama":
                    console.print(
                        f"[bold]{pname}[/bold] [dim](models discovered dynamically)[/dim]"
                    )
                elif pname == "airllm":
                    console.print(
                        f"[bold]{pname}[/bold] [dim](local inference - 70B+ models on limited VRAM)[/dim]"
                    )
                    console.print(
                        "  Suggested models (any HuggingFace model ID works):"
                    )
                    console.print("  • meta-llama/Llama-3.3-70B-Instruct")
                    console.print("  • Qwen/Qwen2.5-72B-Instruct")
                    console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
                    console.print()
                continue

            console.print(f"[bold]{pname}[/bold]")

            table = Table(box=None, show_header=True, padding=(0, 2))
            table.add_column("Model ID", style="cyan")
            table.add_column("Name")
            table.add_column("Context", justify="right")
            table.add_column("Max Tokens", justify="right")

            for model in pconfig.models:
                ctx_str = (
                    f"{model.context_window // 1000}K"
                    if model.context_window >= 1000
                    else str(model.context_window)
                )
                table.add_row(
                    model.id,
                    model.name,
                    ctx_str,
                    str(model.max_tokens),
                )

            console.print(table)
            console.print()

        if provider and provider.lower() not in [
            p.lower() for p in BUILTIN_PROVIDERS.keys()
        ]:
            console.print(f"[yellow]Unknown provider: {provider}[/yellow]")
            console.print(
                f"[dim]Available: {', '.join(BUILTIN_PROVIDERS.keys())}[/dim]"
            )

    except ImportError:
        console.print("[yellow]Provider system not available.[/yellow]")


@ai_app.command("providers")
def ai_providers(
    ctx: typer.Context,
    add: str | None = typer.Option(
        None, "--add", "-a", help="Add API key for provider (e.g., openai, anthropic)"
    ),
    remove: str | None = typer.Option(
        None, "--remove", "-r", help="Remove API key for provider"
    ),
    test: str | None = typer.Option(
        None, "--test", "-t", help="Test provider connection"
    ),
):
    """Manage AI providers and API keys."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    try:
        from navig.providers import BUILTIN_PROVIDERS, AuthProfileManager

        auth = AuthProfileManager()

        if add:
            # Add API key for provider
            import getpass

            provider = add.lower()
            if provider not in BUILTIN_PROVIDERS:
                console.print(
                    f"[yellow]⚠ Unknown provider '{provider}'. Known: {', '.join(BUILTIN_PROVIDERS.keys())}[/yellow]"
                )

            api_key = getpass.getpass(f"Enter API key for {provider}: ")
            if api_key:
                auth.add_api_key(
                    provider=provider, api_key=api_key, profile_id=f"{provider}-default"
                )
                auth.save()
                console.print(f"[green]✓ API key saved for {provider}[/green]")
            else:
                console.print("[yellow]No key entered, cancelled[/yellow]")
            return

        if remove:
            # Remove API key for provider
            provider = remove.lower()
            profile_id = f"{provider}-default"
            if auth.remove_profile(profile_id):
                auth.save()
                console.print(f"[green]✓ Removed API key for {provider}[/green]")
            else:
                console.print(f"[yellow]No API key found for {provider}[/yellow]")
            return

        if test:
            # Test provider connection
            provider = test.lower()
            api_key, source = auth.resolve_auth(provider)
            if not api_key:
                console.print(f"[red]✗ No API key configured for {provider}[/red]")
                console.print(f"  Add one with: navig ai providers --add {provider}")
                return

            console.print(f"[dim]Testing {provider} (key from: {source})...[/dim]")

            # Quick test - try to list models or make a tiny request
            import asyncio

            from navig.providers import BUILTIN_PROVIDERS, create_client

            config = BUILTIN_PROVIDERS.get(provider)
            if not config:
                console.print(f"[red]✗ Unknown provider: {provider}[/red]")
                return

            try:
                client = create_client(config, api_key=api_key, timeout=10)
                # Make a minimal request to test auth
                from navig.providers import CompletionRequest, Message

                async def test_request():
                    request = CompletionRequest(
                        messages=[Message(role="user", content="Hi")],
                        model=config.models[0].id if config.models else "gpt-4o-mini",
                        max_tokens=5,
                    )
                    try:
                        response = await client.complete(request)
                        return True, None
                    except Exception as e:
                        return False, str(e)
                    finally:
                        await client.close()

                success, error = asyncio.run(test_request())
                if success:
                    console.print(f"[green]✓ {provider} is working![/green]")
                else:
                    console.print(f"[red]✗ {provider} error: {error}[/red]")
            except Exception as e:
                console.print(f"[red]✗ Test failed: {e}[/red]")
            return

        # List providers and their status
        console.print("[bold cyan]AI Providers[/bold cyan]")
        console.print()

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Provider", style="cyan")
        table.add_column("API Key", style="green")
        table.add_column("Source")
        table.add_column("Models", style="dim")

        for name, config in BUILTIN_PROVIDERS.items():
            api_key, source = auth.resolve_auth(name)
            key_status = "✓ configured" if api_key else "✗ not set"
            key_style = "green" if api_key else "red"

            model_count = len(config.models)
            models_str = f"{model_count} models" if model_count else "dynamic"

            table.add_row(
                name,
                f"[{key_style}]{key_status}[/{key_style}]",
                source or "-",
                models_str,
            )

        console.print(table)
        console.print()
        console.print("[dim]Add a key: navig ai providers --add <provider>[/dim]")
        console.print(
            "[dim]Test connection: navig ai providers --test <provider>[/dim]"
        )
        console.print("[dim]Configure AirLLM: navig ai airllm --configure[/dim]")
        console.print("[dim]OAuth login: navig ai login openai-codex[/dim]")

    except ImportError:
        console.print(
            "[yellow]Provider system not available. Install httpx: pip install httpx[/yellow]"
        )


@ai_app.command("airllm")
def ai_airllm(
    ctx: typer.Context,
    configure: bool = typer.Option(
        False, "--configure", "-c", help="Configure AirLLM settings"
    ),
    model_path: str | None = typer.Option(
        None, "--model-path", "-p", help="HuggingFace model ID or local path"
    ),
    max_vram: float | None = typer.Option(
        None, "--max-vram", help="Maximum VRAM in GB"
    ),
    compression: str | None = typer.Option(
        None, "--compression", help="Compression mode: 4bit, 8bit, or none"
    ),
    test: bool = typer.Option(
        False, "--test", "-t", help="Test AirLLM with a sample prompt"
    ),
    status: bool = typer.Option(
        False, "--status", "-s", help="Show AirLLM status and configuration"
    ),
):
    """Configure and manage AirLLM local inference provider.

    AirLLM enables running 70B+ models on limited VRAM (4-8GB) through
    layer-wise inference and model sharding.

    Examples:
        navig ai airllm --status
        navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct
        navig ai airllm --configure --compression 4bit --max-vram 8
        navig ai airllm --test
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    # Check if AirLLM is installed
    try:
        from navig.providers import get_airllm_vram_recommendations, is_airllm_available
        from navig.providers.airllm import AirLLMConfig
    except ImportError as _exc:
        console.print("[red]✗ Provider system not available.[/red]")
        raise typer.Exit(1) from _exc

    airllm_available = is_airllm_available()

    if status or (not configure and not test):
        # Show AirLLM status
        console.print("[bold cyan]AirLLM Local Inference Provider[/bold cyan]")
        console.print()

        # Installation status
        if airllm_available:
            console.print("[green]✓ AirLLM is installed[/green]")
        else:
            console.print("[yellow]✗ AirLLM is not installed[/yellow]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            console.print()

        # Current configuration
        console.print()
        console.print("[bold]Current Configuration:[/bold]")

        config = AirLLMConfig.from_env()
        config_table = Table(box=None, show_header=False, padding=(0, 2))
        config_table.add_column("Setting", style="dim")
        config_table.add_column("Value")

        config_table.add_row("Model Path", config.model_path or "[dim]not set[/dim]")
        config_table.add_row("Max VRAM", f"{config.max_vram_gb} GB")
        config_table.add_row("Compression", config.compression or "none")
        config_table.add_row("Device", config.device)
        config_table.add_row(
            "Layer Shards Path", config.layer_shards_path or "[dim]default[/dim]"
        )
        config_table.add_row(
            "Prefetching", "enabled" if config.prefetching else "disabled"
        )

        console.print(config_table)

        # VRAM recommendations
        console.print()
        console.print("[bold]VRAM Recommendations:[/bold]")
        recommendations = get_airllm_vram_recommendations()
        for model_size, rec in recommendations.items():
            console.print(f"  • {model_size}: {rec}")

        # Environment variables
        console.print()
        console.print("[bold]Environment Variables:[/bold]")
        console.print("  AIRLLM_MODEL_PATH     - HuggingFace model ID or local path")
        console.print("  AIRLLM_MAX_VRAM_GB    - Maximum VRAM to use")
        console.print("  AIRLLM_COMPRESSION    - '4bit', '8bit', or empty for none")
        console.print("  AIRLLM_DEVICE         - 'cuda', 'cpu', or 'mps' (macOS)")
        console.print("  HF_TOKEN              - HuggingFace token for gated models")

        # Suggested models
        console.print()
        console.print("[bold]Suggested Models:[/bold]")
        console.print("  • meta-llama/Llama-3.3-70B-Instruct")
        console.print("  • Qwen/Qwen2.5-72B-Instruct")
        console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
        console.print("  • mistralai/Mixtral-8x7B-Instruct-v0.1")

        return

    if configure:
        # Configure AirLLM settings

        config_updates = {}

        if model_path is not None:
            config_updates["AIRLLM_MODEL_PATH"] = model_path
            console.print(f"[green]✓ Model path: {model_path}[/green]")

        if max_vram is not None:
            config_updates["AIRLLM_MAX_VRAM_GB"] = str(max_vram)
            console.print(f"[green]✓ Max VRAM: {max_vram} GB[/green]")

        if compression is not None:
            if compression.lower() == "none":
                compression = ""
            config_updates["AIRLLM_COMPRESSION"] = compression
            console.print(f"[green]✓ Compression: {compression or 'disabled'}[/green]")

        if config_updates:
            # Save to config file
            try:
                config_manager = _get_config_manager()
                # Build update dict with proper key names
                updates = {}
                for key, value in config_updates.items():
                    config_key = f"airllm_{key.lower().replace('airllm_', '')}"
                    updates[config_key] = value
                config_manager.update_global_config(updates)
                console.print()
                console.print(
                    "[green]Configuration saved to ~/.navig/config.yaml[/green]"
                )
            except Exception as e:
                console.print(f"[yellow]⚠ Could not save to config file: {e}[/yellow]")

            # Also show env var export commands
            console.print()
            console.print("[dim]Or set environment variables:[/dim]")
            for key, value in config_updates.items():
                console.print(f'  export {key}="{value}"')
        else:
            console.print("[yellow]No configuration options specified.[/yellow]")
            console.print("Use --model-path, --max-vram, or --compression")

        return

    if test:
        # Test AirLLM with a sample prompt
        if not airllm_available:
            console.print("[red]✗ AirLLM is not installed.[/red]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            raise typer.Exit(1)

        config = AirLLMConfig.from_env()
        if not config.model_path:
            console.print("[red]✗ No model configured.[/red]")
            console.print("  Set AIRLLM_MODEL_PATH or use --configure --model-path")
            raise typer.Exit(1)

        console.print(f"[dim]Testing AirLLM with model: {config.model_path}[/dim]")
        console.print(
            "[dim]This may take a while on first run (downloading/sharding model)...[/dim]"
        )
        console.print()

        import asyncio

        from navig.providers import CompletionRequest, Message, create_airllm_client

        async def run_test():
            try:
                client = create_airllm_client(config)
                request = CompletionRequest(
                    messages=[
                        Message(role="user", content="What is 2 + 2? Answer briefly."),
                    ],
                    model=config.model_path,
                    max_tokens=50,
                )

                response = await client.complete(request)
                await client.close()
                return response
            except Exception as e:
                return str(e)

        with console.status("[bold green]Running inference..."):
            result = asyncio.run(run_test())

        if hasattr(result, "content"):
            console.print("[green]✓ AirLLM is working![/green]")
            console.print()
            console.print(Panel(result.content or "[no response]", title="Response"))
            if result.usage:
                console.print(
                    f"[dim]Tokens: {result.usage.get('prompt_tokens', 0)} prompt, {result.usage.get('completion_tokens', 0)} completion[/dim]"
                )
        else:
            console.print(f"[red]✗ Test failed: {result}[/red]")
            raise typer.Exit(1)


@ai_app.command("login")
def ai_login(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="OAuth provider (e.g., openai-codex)"),
    headless: bool = typer.Option(
        False, "--headless", help="Headless mode (no browser auto-open)"
    ),
):
    """Login to an AI provider using OAuth (e.g., OpenAI Codex)."""
    from rich.console import Console

    console = Console()

    try:
        from navig.providers import (
            OAUTH_PROVIDERS,
            AuthProfileManager,
            run_oauth_flow_headless,
            run_oauth_flow_interactive,
        )

        # Check if any OAuth providers are configured
        if not OAUTH_PROVIDERS:
            console.print(
                "[red]✗ OAuth authentication is not currently available.[/red]"
            )
            console.print()
            console.print("[yellow]Why?[/yellow]")
            console.print("OAuth requires provider-specific client registration.")
            console.print("OpenAI's OAuth is only available to enterprise partners.")
            console.print()
            console.print("[cyan]Use API key authentication instead:[/cyan]")
            console.print("  navig cred add openai sk-... --type api-key")
            console.print("  navig cred add anthropic sk-ant-... --type api-key")
            console.print()
            console.print("[dim]See: docs/development/oauth-limitations.md[/dim]")
            raise typer.Exit(1)

        provider_lower = provider.lower()
        if provider_lower not in OAUTH_PROVIDERS:
            console.print(f"[red]✗ Unknown OAuth provider: {provider}[/red]")
            console.print(
                f"[dim]Available: {', '.join(OAUTH_PROVIDERS.keys()) or 'none'}[/dim]"
            )
            raise typer.Exit(1)

        oauth_config = OAUTH_PROVIDERS[provider_lower]
        console.print(f"[bold cyan]OAuth Login: {oauth_config.name}[/bold cyan]")
        console.print()

        if headless:
            # Headless mode
            console.print(
                "[yellow]Headless mode: Copy the URL below and open it in a browser.[/yellow]"
            )
            console.print()

            def on_auth_url(url: str):
                console.print("[bold]Authorization URL:[/bold]")
                console.print(url)
                console.print()

            def get_callback_input() -> str:
                console.print(
                    "[bold]After signing in, paste the redirect URL here:[/bold]"
                )
                return input("> ")

            result = run_oauth_flow_headless(
                provider_lower,
                on_auth_url=on_auth_url,
                get_callback_input=get_callback_input,
            )
        else:
            # Interactive mode
            def on_progress(msg: str):
                console.print(f"[dim]{msg}[/dim]")

            result = run_oauth_flow_interactive(
                provider_lower,
                on_progress=on_progress,
            )

        if result.success and result.credentials:
            # Save credentials
            auth = AuthProfileManager()
            profile_id = auth.add_oauth_credentials(
                provider=provider_lower,
                access_token=result.credentials.access,
                refresh_token=result.credentials.refresh,
                expires_at=result.credentials.expires,
                client_id=result.credentials.client_id,
                account_id=result.credentials.account_id,
                email=result.credentials.email,
            )

            console.print()
            console.print(
                f"[green]✓ Successfully logged in to {oauth_config.name}![/green]"
            )
            console.print(f"[dim]Profile saved: {profile_id}[/dim]")

            if result.credentials.account_id:
                console.print(f"[dim]Account ID: {result.credentials.account_id}[/dim]")

            console.print()
            console.print("[dim]You can now use this provider with:[/dim]")
            console.print(
                f"  navig ai ask 'your question' --model {provider_lower}:gpt-4o"
            )
        else:
            console.print(f"[red]✗ OAuth failed: {result.error}[/red]")
            raise typer.Exit(1)

    except ImportError as e:
        console.print(f"[yellow]OAuth not available: {e}[/yellow]")
        console.print("[dim]Install httpx: pip install httpx[/dim]")
        raise typer.Exit(1) from e


@ai_app.command("logout")
def ai_logout(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="Provider to logout from"),
):
    """Remove OAuth credentials for a provider."""
    from rich.console import Console

    console = Console()

    try:
        from navig.providers import AuthProfileManager

        auth = AuthProfileManager()
        provider_lower = provider.lower()

        # Find and remove all profiles for this provider
        removed = []
        for profile_id in list(auth.store.profiles.keys()):
            cred = auth.store.profiles[profile_id]
            if cred.provider == provider_lower:
                del auth.store.profiles[profile_id]
                removed.append(profile_id)

        if removed:
            auth.save()
            console.print(f"[green]✓ Logged out from {provider}[/green]")
            for pid in removed:
                console.print(f"[dim]  Removed: {pid}[/dim]")
        else:
            console.print(f"[yellow]No credentials found for {provider}[/yellow]")

    except ImportError:
        console.print("[yellow]Provider system not available.[/yellow]")


# ============================================================================
# AI MEMORY COMMANDS
# ============================================================================

memory_app = typer.Typer(
    help="Manage AI memory - what NAVIG knows about you",
    invoke_without_command=True,
    no_args_is_help=False,
)
ai_app.add_typer(memory_app, name="memory")


@memory_app.callback()
def memory_callback(ctx: typer.Context):
    """AI Memory - what NAVIG knows about you."""
    if ctx.invoked_subcommand is None:
        # Default: show memory
        _memory_show()


def _memory_show():
    """Display current user profile."""
    from rich.console import Console

    console = Console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        console.print(profile.to_human_readable())
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error loading profile: {e}[/red]")


@memory_app.command("show")
def memory_show():
    """Display what NAVIG knows about you."""
    _memory_show()


@memory_app.command("edit")
def memory_edit():
    """Open user profile in your default editor."""
    import os
    from pathlib import Path

    from rich.console import Console

    console = Console()

    profile_path = Path.home() / ".navig" / "memory" / "user_profile.json"

    if not profile_path.exists():
        # Create empty profile first
        try:
            from navig.memory.user_profile import get_profile

            profile = get_profile()
            profile.save()
            console.print(f"[green]Created new profile at: {profile_path}[/green]")
        except Exception as e:
            console.print(f"[red]Error creating profile: {e}[/red]")
            raise typer.Exit(1) from e

    # Get editor from environment
    editor = os.environ.get(
        "EDITOR", os.environ.get("VISUAL", "notepad" if os.name == "nt" else "nano")
    )

    console.print(f"[dim]Opening {profile_path} in {editor}...[/dim]")

    import subprocess

    try:
        subprocess.run([editor, str(profile_path)], check=True)
        console.print(
            "[green]Profile updated. Changes will be loaded on next agent start.[/green]"
        )
    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to open editor: {editor}[/red]")
    except FileNotFoundError:
        console.print(f"[red]Editor not found: {editor}[/red]")
        console.print(f"[dim]Profile is at: {profile_path}[/dim]")


@memory_app.command("add")
def memory_add(
    note: str = typer.Argument(..., help="Note to add to memory"),
    category: str = typer.Option("user_note", "--category", "-c", help="Note category"),
):
    """Add a note to NAVIG's memory about you."""
    from rich.console import Console

    console = Console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        note_obj = profile.add_note(note, category=category, source="user")
        profile.save()
        console.print(f"[green]✓ Added note:[/green] {note[:60]}...")
        console.print(
            f"[dim]Category: {category} | Time: {note_obj.timestamp[:19]}[/dim]"
        )
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error adding note: {e}[/red]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Search NAVIG's memory about you."""
    from rich.console import Console

    console = Console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        results = profile.search_memory(query, limit=limit)

        if results:
            console.print(
                f"[bold]Found {len(results)} result(s) for '{query}':[/bold]\n"
            )
            for i, result in enumerate(results, 1):
                console.print(f"  {i}. {result}")
        else:
            console.print(f"[yellow]No results found for '{query}'[/yellow]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error searching: {e}[/red]")


@memory_app.command("clear")
def memory_clear(
    confirm: bool = typer.Option(
        False, "--confirm", help="Confirm clearing all memory"
    ),
):
    """Clear all memory (requires --confirm)."""
    from rich.console import Console

    console = Console()
    if not confirm:
        console.print(
            "[yellow]⚠️  This will delete all stored user profile data.[/yellow]"
        )
        console.print("[dim]Run with --confirm to proceed.[/dim]")
        raise typer.Exit(1)

    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()

        if profile.clear(confirm=True):
            console.print("[green]✓ Memory cleared. Backup created.[/green]")
        else:
            console.print("[red]Failed to clear memory.[/red]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@memory_app.command("set")
def memory_set(
    field: str = typer.Argument(
        ..., help="Field to set (e.g., identity.name, technical_context.stack)"
    ),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a specific profile field."""
    from rich.console import Console

    console = Console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()

        # Handle list fields (comma-separated)
        if field in [
            "technical_context.stack",
            "technical_context.managed_hosts",
            "technical_context.primary_projects",
            "work_patterns.active_hours",
            "work_patterns.common_tasks",
            "goals",
            "preferences.confirmation_required_for",
        ]:
            value = [v.strip() for v in value.split(",")]

        updated = profile.update({field: value})

        if updated:
            console.print(f"[green]✓ Updated {field} = {value}[/green]")
        else:
            console.print(f"[red]Failed to update {field}. Check field name.[/red]")
            console.print(
                "[dim]Valid fields: identity.name, identity.timezone, identity.role, "
                "technical_context.stack, goals, preferences.communication_style[/dim]"
            )
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# Legacy flat command for backward compatibility
@app.command("ai", hidden=True)
def ai_legacy(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Override default AI model"
    ),
):
    """[DEPRECATED: Use 'navig ai ask'] Ask AI about server."""
    deprecation_warning("navig ai <question>", "navig ai ask <question>")
    from navig.commands.ai import ask_ai

    ask_ai(question, model, ctx.obj)


# ============================================================================
# PROACTIVE ASSISTANT COMMANDS (Deprecated - use 'ai' group)
# ============================================================================

assistant_app = typer.Typer(
    help="[DEPRECATED: Use 'navig ai'] Proactive AI assistant",
    invoke_without_command=True,
    no_args_is_help=False,
    hidden=True,
)
app.add_typer(assistant_app, name="assistant")


@assistant_app.callback()
def assistant_callback(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai'] AI Assistant."""
    deprecation_warning("navig assistant", "navig ai")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_assistant_menu

        launch_assistant_menu()
        raise typer.Exit()


@assistant_app.command("status")
def assistant_status(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai show --status']"""
    deprecation_warning("navig assistant status", "navig ai show --status")
    from navig.commands.assistant import status_cmd

    status_cmd(ctx.obj)


@assistant_app.command("analyze")
def assistant_analyze(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai diagnose']"""
    deprecation_warning("navig assistant analyze", "navig ai diagnose")
    from navig.commands.assistant import analyze_cmd

    analyze_cmd(ctx.obj)


@assistant_app.command("context")
def assistant_context(
    ctx: typer.Context,
    clipboard: bool = typer.Option(
        False, "--clipboard", help="Copy context to clipboard"
    ),
    file: str | None = typer.Option(None, "--file", help="Save context to file"),
):
    """[DEPRECATED: Use 'navig ai show --context']"""
    deprecation_warning("navig assistant context", "navig ai show --context")
    from navig.commands.assistant import context_cmd

    context_cmd(ctx.obj, clipboard, file)


@assistant_app.command("reset")
def assistant_reset(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai run --reset']"""
    deprecation_warning("navig assistant reset", "navig ai run --reset")
    from navig.commands.assistant import reset_cmd

    reset_cmd(ctx.obj)


@assistant_app.command("config")
def assistant_config(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai edit']"""
    deprecation_warning("navig assistant config", "navig ai edit")
    from navig.commands.assistant import config_cmd

    config_cmd(ctx.obj)


# ============================================================================
# HESTIACP MANAGEMENT COMMANDS (DEPRECATED - use 'navig web hestia')
# ============================================================================

hestia_app = typer.Typer(
    help="[DEPRECATED: Use 'navig web hestia'] HestiaCP management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(hestia_app, name="hestia", hidden=True)  # Deprecated


@hestia_app.callback()
def hestia_callback(ctx: typer.Context):
    """HestiaCP management - DEPRECATED, use 'navig web hestia'."""
    deprecation_warning("navig hestia", "navig web hestia")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_hestia_menu

        launch_hestia_menu()
        raise typer.Exit()


@hestia_app.command("users")
def hestia_list_users(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one user per line) for scripting"
    ),
):
    """List HestiaCP users."""
    from navig.commands.hestia import list_users_cmd

    ctx.obj["plain"] = plain
    list_users_cmd(ctx.obj)


@hestia_app.command("domains")
def hestia_list_domains(
    ctx: typer.Context,
    user: str | None = typer.Option(None, "--user", "-u", help="Filter by username"),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one domain per line) for scripting"
    ),
):
    """List HestiaCP domains."""
    from navig.commands.hestia import list_domains_cmd

    ctx.obj["plain"] = plain
    list_domains_cmd(user, ctx.obj)


@hestia_app.command("add-user")
def hestia_add_user(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to create"),
    password: str = typer.Argument(..., help="User password"),
    email: str = typer.Argument(..., help="User email address"),
):
    """Add new HestiaCP user."""
    from navig.commands.hestia import add_user_cmd

    add_user_cmd(username, password, email, ctx.obj)


@hestia_app.command("delete-user")
def hestia_delete_user(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to delete"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force deletion without confirmation"
    ),
):
    """Delete HestiaCP user."""
    ctx.obj["force"] = force
    from navig.commands.hestia import delete_user_cmd

    delete_user_cmd(username, ctx.obj)


@hestia_app.command("add-domain")
def hestia_add_domain(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name to add"),
):
    """Add domain to HestiaCP user."""
    from navig.commands.hestia import add_domain_cmd

    add_domain_cmd(user, domain, ctx.obj)


@hestia_app.command("delete-domain")
def hestia_delete_domain(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name to delete"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force deletion without confirmation"
    ),
):
    """Delete domain from HestiaCP."""
    ctx.obj["force"] = force
    from navig.commands.hestia import delete_domain_cmd

    delete_domain_cmd(user, domain, ctx.obj)


@hestia_app.command("renew-ssl")
def hestia_renew_ssl(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name"),
):
    """Renew SSL certificate for domain."""
    from navig.commands.hestia import renew_ssl_cmd

    renew_ssl_cmd(user, domain, ctx.obj)


@hestia_app.command("rebuild-web")
def hestia_rebuild_web(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
):
    """Rebuild web configuration for user."""
    from navig.commands.hestia import rebuild_web_cmd

    rebuild_web_cmd(user, ctx.obj)


@hestia_app.command("backup-user")
def hestia_backup_user(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to backup"),
):
    """Backup HestiaCP user."""
    from navig.commands.hestia import backup_user_cmd

    backup_user_cmd(user, ctx.obj)


# ============================================================================
# TEMPLATE MANAGEMENT COMMANDS
# ============================================================================

template_app = typer.Typer(
    help="[DEPRECATED: Use 'navig flow template'] Manage templates",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(template_app, name="template", hidden=True)  # Deprecated


@template_app.callback()
def template_callback(ctx: typer.Context):
    """Template management - DEPRECATED, use 'navig flow template'."""
    deprecation_warning("navig template", "navig flow template")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_template_menu

        launch_template_menu()
        raise typer.Exit()


@template_app.command("list")
def template_list(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one template per line) for scripting"
    ),
):
    """List all available templates."""
    from navig.commands.template import list_templates_cmd

    ctx.obj["plain"] = plain
    list_templates_cmd(ctx.obj)


@template_app.command("enable")
def template_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable an template."""
    from navig.commands.template import enable_template_cmd

    enable_template_cmd(name, ctx.obj)


@template_app.command("disable")
def template_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable an template."""
    from navig.commands.template import disable_template_cmd

    disable_template_cmd(name, ctx.obj)


@template_app.command("toggle")
def template_toggle(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to toggle"),
):
    """Toggle template enabled/disabled state."""
    from navig.commands.template import toggle_template_cmd

    toggle_template_cmd(name, ctx.obj)


@template_app.command("info")
def template_info(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to show details for"),
):
    """Show detailed information about an template."""
    from navig.commands.template import show_template_cmd

    show_template_cmd(name, ctx.obj)


@template_app.command("validate")
def template_validate(ctx: typer.Context):
    """Validate all template configurations."""
    from navig.commands.template import validate_templates_cmd

    validate_templates_cmd(ctx.obj)


@template_app.command("edit")
def template_edit(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to edit"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Edit host-specific template override file in $EDITOR."""
    from navig.commands.template import edit_template_cmd

    ctx.obj["server"] = server
    edit_template_cmd(name, ctx.obj)


# ============================================================================
# ADDON COMMANDS (alias for template commands)
# ============================================================================

addon_app = typer.Typer(help="[DEPRECATED: Use 'navig flow template'] Addon commands")
app.add_typer(addon_app, name="addon", hidden=True)  # Deprecated


@addon_app.callback()
def addon_callback(ctx: typer.Context):
    """Addon management - DEPRECATED, use 'navig flow template'."""
    deprecation_warning("navig addon", "navig flow template")


@addon_app.command("list")
def addon_list(ctx: typer.Context):
    """List available templates."""
    from navig.commands.template import addon_list_deprecated

    addon_list_deprecated(ctx.obj)


@addon_app.command("enable")
def addon_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable a template."""
    from navig.commands.template import addon_enable_deprecated

    addon_enable_deprecated(name, ctx.obj)


@addon_app.command("disable")
def addon_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable a template."""
    from navig.commands.template import addon_disable_deprecated

    addon_disable_deprecated(name, ctx.obj)


@addon_app.command("info")
def addon_info(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to show"),
):
    """Show template info."""
    from navig.commands.template import addon_info_deprecated

    addon_info_deprecated(name, ctx.obj)


@addon_app.command("run")
def addon_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to run"),
    command: str | None = typer.Argument(None, help="Template command to execute"),
    args: list[str] | None = typer.Argument(
        None, help="Arguments for the template command"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without changes"
    ),
):
    """Run a template command (deprecated; use flow template run)."""
    deprecation_warning("navig addon run", "navig flow template run")
    from navig.commands.template import deploy_template_cmd

    deploy_template_cmd(
        name,
        command_name=command,
        command_args=args or [],
        dry_run=dry_run,
        ctx_obj=ctx.obj,
    )


@addon_app.command("deploy", hidden=True)
def addon_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to run/deploy"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without changes"
    ),
):
    """Legacy deploy alias (deprecated)."""
    from navig.commands.template import addon_run_deprecated

    addon_run_deprecated(name, ctx.obj, dry_run=dry_run)


# ============================================================================
# SERVER-SPECIFIC TEMPLATE COMMANDS
# ============================================================================

server_template_app = typer.Typer(help="Manage per-server template configurations")
app.add_typer(server_template_app, name="server-template")


@server_template_app.command("list")
def server_template_list(
    ctx: typer.Context,
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    enabled_only: bool = typer.Option(
        False, "--enabled", "-e", help="Show only enabled templates"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one template per line) for scripting"
    ),
):
    """List template configurations for a server."""
    from navig.commands.server_template import list_server_templates_cmd

    ctx.obj["server"] = server
    ctx.obj["enabled_only"] = enabled_only
    ctx.obj["plain"] = plain
    list_server_templates_cmd(ctx.obj)


@server_template_app.command("show")
def server_template_show(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to show"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Show merged configuration for a server template."""
    from navig.commands.server_template import show_template_config_cmd

    ctx.obj["server"] = server
    show_template_config_cmd(template_name, ctx.obj)


@server_template_app.command("enable")
def server_template_enable(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to enable"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Enable an template for a specific server."""
    from navig.commands.server_template import enable_server_template_cmd

    ctx.obj["server"] = server
    enable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("disable")
def server_template_disable(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to disable"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Disable an template for a specific server."""
    from navig.commands.server_template import disable_server_template_cmd

    ctx.obj["server"] = server
    disable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("set")
def server_template_set(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name"),
    key_path: str = typer.Argument(
        ..., help="Dot-separated config path (e.g., 'paths.web_root')"
    ),
    value: str = typer.Argument(..., help="Value to set (JSON-parseable)"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Set a custom value for a server template configuration."""
    from navig.commands.server_template import set_template_value_cmd

    ctx.obj["server"] = server
    set_template_value_cmd(template_name, key_path, value, ctx.obj)


@server_template_app.command("sync")
def server_template_sync(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to sync"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite all custom settings"
    ),
):
    """Sync template configuration from template."""
    from navig.commands.server_template import sync_template_cmd

    ctx.obj["server"] = server
    ctx.obj["force"] = force
    sync_template_cmd(template_name, ctx.obj)


@server_template_app.command("init")
def server_template_init(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to initialize"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    enable: bool = typer.Option(
        False, "--enable", "-e", help="Enable template after initialization"
    ),
):
    """Manually initialize an template for a server."""
    from navig.commands.server_template import init_template_cmd

    ctx.obj["server"] = server
    ctx.obj["enable"] = enable
    init_template_cmd(template_name, ctx.obj)


# ============================================================================
# MCP SERVER MANAGEMENT COMMANDS
# ============================================================================

mcp_app = typer.Typer(
    help="Manage MCP (Model Context Protocol) servers",
    invoke_without_command=True,
    no_args_is_help=False,
)
# Registration removed — "mcp" is dispatched via _EXTERNAL_CMD_MAP -> navig.commands.mcp_cmd


@mcp_app.callback()
def mcp_callback(ctx: typer.Context):
    """MCP management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_mcp_menu

        launch_mcp_menu()
        raise typer.Exit()


@mcp_app.command("search")
def mcp_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
):
    """Search MCP directory for servers."""
    from navig.commands.mcp import search_mcp_cmd

    search_mcp_cmd(query, ctx.obj)


@mcp_app.command("install")
def mcp_install(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to install"),
):
    """Install an MCP server."""
    from navig.commands.mcp import install_mcp_cmd

    install_mcp_cmd(name, ctx.obj)


@mcp_app.command("uninstall")
def mcp_uninstall(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to uninstall"),
):
    """Uninstall an MCP server."""
    from navig.commands.mcp import uninstall_mcp_cmd

    uninstall_mcp_cmd(name, ctx.obj)


@mcp_app.command("list")
def mcp_list(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one server per line) for scripting"
    ),
):
    """List installed MCP servers."""
    from navig.commands.mcp import list_mcp_cmd

    ctx.obj["plain"] = plain
    list_mcp_cmd(ctx.obj)


@mcp_app.command("enable")
def mcp_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to enable"),
):
    """Enable an MCP server."""
    from navig.commands.mcp import enable_mcp_cmd

    enable_mcp_cmd(name, ctx.obj)


@mcp_app.command("disable")
def mcp_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to disable"),
):
    """Disable an MCP server."""
    from navig.commands.mcp import disable_mcp_cmd

    disable_mcp_cmd(name, ctx.obj)


@mcp_app.command("start")
def mcp_start(
    ctx: typer.Context,
    name: str = typer.Argument(
        ..., help="MCP server name to start (or 'all' for all enabled)"
    ),
):
    """Start an MCP server."""
    from navig.commands.mcp import start_mcp_cmd

    start_mcp_cmd(name, ctx.obj)


@mcp_app.command("stop")
def mcp_stop(
    ctx: typer.Context,
    name: str = typer.Argument(
        ..., help="MCP server name to stop (or 'all' for all running)"
    ),
):
    """Stop an MCP server."""
    from navig.commands.mcp import stop_mcp_cmd

    stop_mcp_cmd(name, ctx.obj)


@mcp_app.command("restart")
def mcp_restart(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to restart"),
):
    """Restart an MCP server."""
    from navig.commands.mcp import restart_mcp_cmd

    restart_mcp_cmd(name, ctx.obj)


@mcp_app.command("status")
def mcp_status(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to check status"),
):
    """Show detailed MCP server status."""
    from navig.commands.mcp import status_mcp_cmd

    status_mcp_cmd(name, ctx.obj)


@mcp_app.command("serve")
def mcp_serve(
    ctx: typer.Context,
    transport: str = typer.Option(
        "stdio", "--transport", "-t", help="Transport mode: stdio, websocket"
    ),
    port: int = typer.Option(3001, "--port", "-p", help="Port for WebSocket mode"),
    token: str = typer.Option(
        None, "--token", help="Auth token (auto-generated if omitted)"
    ),
):
    """Start NAVIG as an MCP server for AI assistants like Copilot.

    This exposes NAVIG's hosts, apps, wiki, and database info to AI assistants
    via the Model Context Protocol (MCP).

    Examples:
        navig mcp serve                          # Start in stdio mode (for VS Code)
        navig mcp serve --transport websocket    # WebSocket on port 3001
        navig mcp serve -t websocket -p 4000     # WebSocket on custom port
    """
    from navig.mcp_server import start_mcp_server

    # Infer transport from port for backward compatibility
    if transport == "stdio" and port != 3001:
        transport = "websocket"

    if transport == "stdio":
        start_mcp_server(mode="stdio")
    elif transport == "websocket":
        ch.info(f"Starting NAVIG MCP WebSocket server on port {port}...")
        start_mcp_server(mode="websocket", port=port, token=token)
    else:
        ch.error(f"Unknown transport: {transport}. Use 'stdio' or 'websocket'.")
        raise typer.Exit(1)


@mcp_app.command("config")
def mcp_config_cmd(
    ctx: typer.Context,
    target: str = typer.Argument("vscode", help="Config target: vscode, claude"),
    output: bool = typer.Option(False, "--output", "-o", help="Output config to file"),
):
    """Generate MCP configuration for AI assistants.

    Examples:
        navig mcp config vscode    # Show VS Code MCP config
        navig mcp config claude    # Show Claude Desktop config
        navig mcp config vscode -o # Write to .vscode/mcp.json
    """
    import json
    from pathlib import Path

    from navig.mcp_server import generate_claude_mcp_config, generate_vscode_mcp_config

    if target == "vscode":
        config = generate_vscode_mcp_config()
        filename = ".vscode/mcp.json"
    elif target == "claude":
        config = generate_claude_mcp_config()
        filename = "claude_desktop_config.json"
    else:
        ch.error(f"Unknown target: {target}. Use 'vscode' or 'claude'")
        raise typer.Exit(1)

    config_json = json.dumps(config, indent=2)

    if output:
        # Write to file
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_json)
        ch.success(f"✓ Config written to {filename}")
    else:
        ch.header(f"MCP Configuration for {target.title()}")
        ch.dim("")
        ch.console.print_json(config_json)
        ch.dim("")
        ch.info(f"Add this to your {target} configuration to enable NAVIG integration.")


# ============================================================================
# App INITIALIZATION
# ============================================================================

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


# ============================================================================
# CONFIGURATION MANAGEMENT COMMANDS
# ============================================================================

config_app = typer.Typer(
    help="Manage NAVIG configuration and settings",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(config_app, name="config")


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
    from navig.commands.config import migrate

    migrate(dry_run=dry_run, no_backup=no_backup)


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
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat warnings as errors",
    ),
    json: bool = typer.Option(
        False, "--json", help="Output validation results as JSON"
    ),
):
    """Alias for: navig config validate."""
    from navig.commands.config import validate

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    if scope:
        opts["scope"] = scope
    if strict:
        opts["strict"] = True
    validate(host=host, options=opts)


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
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat warnings as errors",
    ),
    json: bool = typer.Option(
        False, "--json", help="Output validation results as JSON"
    ),
):
    from navig.commands.config import validate

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    if scope:
        opts["scope"] = scope
    if strict:
        opts["strict"] = True
    validate(host=host, options=opts)


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
    json: bool = typer.Option(
        False, "--json", help="Output installation result as JSON"
    ),
):
    """Install NAVIG YAML JSON Schemas for editor validation/autocomplete."""
    from navig.commands.config import install_schemas

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    install_schemas(
        scope=scope, write_vscode_settings=write_vscode_settings, options=opts
    )


@config_app.command("show-global", hidden=True)
def config_show_legacy(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Host name or host:app to display"),
):
    """Display host or app configuration."""
    from navig.commands.config import show

    show(target=target)


@config_app.command("settings")
def config_settings(ctx: typer.Context):
    """Display current NAVIG settings including execution mode and confirmation level."""
    from navig.commands.config import show_settings

    show_settings()


@config_app.command("set-mode")
def config_set_mode(
    ctx: typer.Context,
    mode: str = typer.Argument(..., help="Execution mode: 'interactive' or 'auto'"),
):
    """
    Set the default execution mode.

    Modes:
        interactive - Prompts for confirmation based on confirmation level (default)
        auto - Bypasses all confirmation prompts
    """
    from navig.commands.config import set_mode

    set_mode(mode)


@config_app.command("set-confirmation-level")
def config_set_confirmation_level(
    ctx: typer.Context,
    level: str = typer.Argument(
        ..., help="Confirmation level: 'critical', 'standard', or 'verbose'"
    ),
):
    """
    Set the confirmation level for interactive mode.

    Levels:
        critical - Only confirm destructive operations (DROP, DELETE, rm)
        standard - Confirm state-changing operations (default)
        verbose - Confirm all operations including reads
    """
    from navig.commands.config import set_confirmation_level

    set_confirmation_level(level)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(
        ..., help="Configuration key (e.g., 'log_level', 'execution.mode')"
    ),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a global configuration value."""
    from navig.commands.config import set_config

    set_config(key, value)


@config_app.command("get-raw", hidden=True)
def config_get_legacy(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Configuration key to retrieve"),
):
    """Get a configuration value."""
    from navig.commands.config import get_config

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
    """
    Export NAVIG configuration to a backup file.

    Alias for: navig backup export
    Backs up all hosts, apps, and settings. Sensitive data is redacted by default.

    Examples:
        navig config backup
        navig config backup --format json --output ~/my-backup.json
        navig config backup --include-secrets --encrypt
    """
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


# ============================================================================
# CONFIGURATION BACKUP & EXPORT COMMANDS
# ============================================================================

backup_app = typer.Typer(
    help="Backup and export NAVIG configuration",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(backup_app, name="backup")


@backup_app.callback()
def backup_callback(ctx: typer.Context):
    """Backup management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("backup", ctx)
        raise typer.Exit()


@backup_app.command("export")
def backup_export(
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
    """
    Export NAVIG configuration to a backup file.

    Creates a portable backup of all hosts, apps, and settings.
    By default, sensitive data (passwords, API keys) is redacted.

    Examples:
        navig backup export
        navig backup export --format json --output ~/my-backup.json
        navig backup export --include-secrets --encrypt
    """
    from navig.commands.config_backup import export_config

    export_config(
        {
            "output": output,
            "format": format,
            "include_secrets": include_secrets,
            "encrypt": encrypt,
            "password": password,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("import")
def backup_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to import"),
    merge: bool = typer.Option(
        True,
        "--merge/--replace",
        help="Merge with existing config (default) or replace",
    ),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password (prompted if needed)"
    ),
):
    """
    Import NAVIG configuration from a backup file.

    Restores hosts, apps, and settings from a previous export.

    Examples:
        navig backup import navig-config-20241206.tar.gz
        navig backup import backup.json --replace
        navig backup import encrypted-backup.tar.gz.enc --password mypassword
    """
    from navig.commands.config_backup import import_config

    import_config(
        {
            "file": file,
            "merge": merge,
            "password": password,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("show")
def backup_show(
    ctx: typer.Context,
    file: Path | None = typer.Argument(None, help="Backup file to inspect"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password if encrypted"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text for scripting"
    ),
):
    """Show backup details or list all backups (canonical command)."""
    if file:
        from navig.commands.navig_backup import inspect_export

        inspect_export(
            {
                "file": file,
                "password": password,
                "json": ctx.obj.get("json", False),
            }
        )
    else:
        from navig.commands.navig_backup import list_exports

        list_exports(
            {
                "json": ctx.obj.get("json", False),
                "plain": plain,
            }
        )


@backup_app.command("run")
def backup_run(
    ctx: typer.Context,
    config: bool = typer.Option(
        False, "--config", help="Backup system configuration files"
    ),
    db_all: bool = typer.Option(False, "--db-all", help="Backup all databases"),
    hestia: bool = typer.Option(
        False, "--hestia", help="Backup HestiaCP configuration"
    ),
    web: bool = typer.Option(False, "--web", help="Backup web server configuration"),
    all: bool = typer.Option(False, "--all", help="Run comprehensive backup"),
    restore: str | None = typer.Option(
        None, "--restore", help="Restore from a comprehensive backup by name"
    ),
    component: str | None = typer.Option(
        None, "--component", help="Specific component to restore"
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option(
        "gzip",
        "--compress",
        "-c",
        help="Compression for database backups: none|gzip|zstd",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Run server backup/restore operations (system config, DBs, Hestia, web)."""
    selected_count = sum(
        1 for flag in [config, db_all, hestia, web, all, restore is not None] if flag
    )

    if selected_count != 1:
        ch.error(
            "Choose exactly one backup operation.",
            "Use one of: --config, --db-all, --hestia, --web, --all, or --restore <name>.",
        )
        raise typer.Exit(1)

    from navig.commands import backup as backup_cmds

    if restore is not None:
        ctx.obj["force"] = force
        backup_cmds.restore_backup_cmd(restore, component, ctx.obj)
        return

    if config:
        backup_cmds.backup_system_config(name, ctx.obj)
    elif db_all:
        backup_cmds.backup_all_databases(name, compress, ctx.obj)
    elif hestia:
        backup_cmds.backup_hestia(name, ctx.obj)
    elif web:
        backup_cmds.backup_web_config(name, ctx.obj)
    else:
        backup_cmds.backup_all(name, compress, ctx.obj)


@backup_app.command("restore")
def backup_restore(
    ctx: typer.Context,
    backup_name: str = typer.Argument(..., help="Backup name to restore from"),
    component: str | None = typer.Option(
        None, "--component", "-c", help="Specific component to restore"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Restore from a comprehensive backup by name."""
    from navig.commands.backup import restore_backup_cmd

    ctx.obj["force"] = force
    restore_backup_cmd(backup_name, component, ctx.obj)


@backup_app.command("list", hidden=True)
def backup_list(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one backup per line) for scripting"
    ),
):
    """[DEPRECATED: Use 'navig backup show'] List available backups."""
    deprecation_warning("navig backup list", "navig backup show")
    from navig.commands.config_backup import list_exports

    list_exports(
        {
            "json": ctx.obj.get("json", False),
            "plain": plain,
        }
    )


@backup_app.command("inspect", hidden=True)
def backup_inspect(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to inspect"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password if encrypted"
    ),
):
    """[DEPRECATED: Use 'navig backup show <file>'] Inspect backup contents."""
    deprecation_warning("navig backup inspect", "navig backup show <file>")
    from navig.commands.config_backup import inspect_export

    inspect_export(
        {
            "file": file,
            "password": password,
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("remove")
def backup_remove(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """Remove/delete a backup file (canonical command)."""
    from navig.commands.config_backup import delete_export

    delete_export(
        {
            "file": file,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("delete", hidden=True)
def backup_delete(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """[DEPRECATED: Use 'navig backup remove'] Delete backup file."""
    deprecation_warning("navig backup delete", "navig backup remove")
    from navig.commands.config_backup import delete_export

    delete_export(
        {
            "file": file,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("config")
def backup_config_cmd(
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
    """
    Backup/export NAVIG configuration (hosts, apps, settings).

    Canonical alias for: navig backup export
    The inverse of: navig backup import

    Examples:
        navig backup config
        navig backup config --format json --output ~/my-backup.json
        navig backup config --include-secrets --encrypt
    """
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


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

# ============================================================================
# WORKFLOW COMMANDS
# ============================================================================

# ============================================================
# PILLAR 4: AUTOMATION - flow (primary), workflow/task (deprecated aliases)
# ============================================================
flow_app = typer.Typer(
    help="Manage and execute reusable command flows (workflows)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(flow_app, name="flow")


@flow_app.callback()
def flow_callback(ctx: typer.Context):
    """Flow management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("flow", ctx)
        raise typer.Exit()


@flow_app.command("list")
def flow_list():
    """List all available flows."""
    from navig.commands.workflow import list_workflows

    list_workflows()


@flow_app.command("show")
def flow_show(name: str = typer.Argument(..., help="Flow name")):
    """Display flow definition and steps."""
    from navig.commands.workflow import show_workflow

    show_workflow(name)


@flow_app.command("run")
def flow_run(
    name: str = typer.Argument(..., help="Flow name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without executing"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip all confirmation prompts"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: list[str] | None = typer.Option(
        None, "--var", "-V", help="Variable override (name=value)"
    ),
):
    """Execute a flow."""
    from navig.commands.workflow import run_workflow

    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@flow_app.command("test")
def flow_test(name: str = typer.Argument(..., help="Flow name")):
    """Test/validate flow syntax and structure."""
    from navig.commands.workflow import validate_workflow

    validate_workflow(name)


@flow_app.command("add")
def flow_add(
    name: str = typer.Argument(..., help="New flow name"),
    global_scope: bool = typer.Option(
        False, "--global", "-g", help="Create in global directory"
    ),
):
    """Create a new flow."""
    from navig.commands.workflow import create_workflow

    create_workflow(name, global_scope=global_scope)


# ============================================================================
# SKILLS COMMANDS
# ============================================================================

skills_app = typer.Typer(
    help="Manage AI skill definitions",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(skills_app, name="skills")
app.add_typer(skills_app, name="skill", hidden=True)


@skills_app.callback()
def skills_callback(ctx: typer.Context):
    """Skills management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("skills", ctx)
        raise typer.Exit()


@skills_app.command("list")
def skills_list(
    ctx: typer.Context,
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List available AI skills."""
    from navig.commands.skills import list_skills_cmd

    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    list_skills_cmd(ctx.obj)


@skills_app.command("tree")
def skills_tree(
    ctx: typer.Context,
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show skills grouped by category."""
    from navig.commands.skills import tree_skills_cmd

    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    tree_skills_cmd(ctx.obj)


@skills_app.command("show")
def skills_show(
    ctx: typer.Context,
    name: str = typer.Argument(
        ...,
        help="Skill name (e.g., 'docker-manage', 'git-basics', 'official/docker-ops')",
    ),
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed skill information (commands, examples, metadata)."""
    from navig.commands.skills import show_skill_cmd

    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    show_skill_cmd(name, ctx.obj)


@skills_app.command("run")
def skills_run(
    ctx: typer.Context,
    spec: str = typer.Argument(
        ...,
        help="Skill spec: <skill-name>:<command> or <skill-name> (runs entrypoint)",
    ),
    args: list[str] | None = typer.Argument(
        None, help="Arguments passed to the skill command"
    ),
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm risky commands"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Run a skill command.

    Spec format:
      <skill>:<command>  — run a named navig-command from the skill
      <skill>            — run the skill's entrypoint (main.py / index.js)

    Examples:
        navig skills run docker-manage:ps
        navig skills run git-basics:git-status
        navig skills run file-operations:list-files /var/log
        navig skills run my-custom-skill   # runs entrypoint
    """
    from navig.commands.skills import run_skill_cmd

    if json_output:
        ctx.obj["json"] = True
    if yes:
        ctx.obj["yes"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    exit_code = run_skill_cmd(spec, args or [], ctx.obj)
    if exit_code != 0:
        raise typer.Exit(exit_code)


@skills_app.command("synthesize")
def skills_synthesize(
    ctx: typer.Context,
    min_occurrences: int = typer.Option(
        3,
        "--min-occurrences",
        "-m",
        min=1,
        help="Minimum pattern repetitions to consider.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        min=1,
        max=100,
        help="Maximum number of patterns to analyse.",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Write approved skill YAML to ~/.navig/skills/."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without writing any files."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-approve all safe drafts."
    ),
) -> None:
    """
    Synthesize new skill YAML files from repeated command patterns.

    Scans ~/.navig/data/pattern_log.sqlite, clusters repeated sequences,
    and generates ready-to-use NAVIG skill definitions.

    Examples:
        navig skills synthesize                  # preview top patterns
        navig skills synthesize --apply          # generate + save skills
        navig skills synthesize --min-occurrences 5 --apply --yes
    """
    import typer as _typer  # noqa: F401

    try:
        from navig.agent.pattern_analyzer import PatternAnalyzer  # type: ignore
        from navig.agent.pattern_observer import (  # type: ignore
            DEFAULT_DB_PATH,
            PatternObserver,
        )
        from navig.agent.skill_drafter import SkillDrafter  # type: ignore
    except ImportError as exc:
        ch.error(f"Synthesis pipeline not available: {exc}")
        raise typer.Exit(1) from exc

    observer = PatternObserver(DEFAULT_DB_PATH)
    records = observer.get_recent(limit=500)

    if not records:
        ch.warn(
            "No command patterns found in pattern log.\n"
            "  Run a few commands first to build the pattern database.\n"
            f"  Log path: {DEFAULT_DB_PATH}"
        )
        raise typer.Exit(0)

    analyzer = PatternAnalyzer(min_occurrences=min_occurrences, max_results=limit)
    scored = analyzer.score_by_frequency(records)

    if not scored:
        ch.warn(
            f"No patterns found with ≥{min_occurrences} occurrences.\n"
            "  Try lowering --min-occurrences."
        )
        raise typer.Exit(0)

    drafter = SkillDrafter()

    # -- Preview table --------------------------------------------------------
    from rich.table import Table

    table = Table(title=f"Top {len(scored)} Synthesisable Patterns", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Sequence", style="cyan")
    table.add_column("Occurrences", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Safe")

    drafts = []
    for idx, pattern in enumerate(scored, 1):
        draft = drafter.draft(pattern)
        drafts.append(draft)
        safe_icon = "[green]✓[/green]" if draft.safe else "[red]✗[/red]"
        table.add_row(
            str(idx),
            " → ".join(list(pattern.sequence)[:4]),
            str(pattern.occurrences),
            f"{pattern.score:.0f}",
            safe_icon,
        )

    ch.console.print(table)

    if dry_run:
        ch.dim("  (dry-run: no files written)")
        raise typer.Exit(0)

    if not apply:
        ch.dim("\nRun with --apply to save skill YAML files.")
        raise typer.Exit(0)

    # -- Apply ----------------------------------------------------------------
    saved = 0
    skipped = 0
    for draft in drafts:
        if not draft.safe:
            if yes:
                ch.warn(f"Skipping unsafe draft: {draft.name}")
                skipped += 1
                continue
            choice = typer.confirm(
                f"Draft '{draft.name}' has safety warnings. Save anyway?", default=False
            )
            if not choice:
                skipped += 1
                continue

        path = drafter.apply(draft)
        ch.success(f"Saved: {path}")
        saved += 1

    ch.print(f"\n[bold]{saved}[/bold] skill(s) saved, {skipped} skipped.")


# ============================================================================
# SCAFFOLD COMMANDS (Lazy-loaded)
# ============================================================================
scaffold_app = typer.Typer(
    help="Scaffold project structures from templates",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(scaffold_app, name="scaffold")


@scaffold_app.callback(invoke_without_command=True)
def scaffold_callback(ctx: typer.Context):
    """Scaffold management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("scaffold", ctx)
        raise typer.Exit()


@scaffold_app.command("apply")
def scaffold_apply(
    template_path: Path = typer.Argument(..., help="Path to YAML template file"),
    target_dir: str = typer.Option(
        ".", "--target-dir", "-d", help="Target directory (local or remote)"
    ),
    host: str | None = typer.Option(
        None, "--host", "-h", help="Remote host to deploy to (defaults to local)"
    ),
    set_var: list[str] | None = typer.Option(
        None, "--set", help="Set variable like key=value"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate without creating files"
    ),
):
    """Generate files/directories from a template."""
    from navig.commands.scaffold import apply

    apply(template_path, target_dir, host, set_var, dry_run)


# Nested: flow template (consolidates template + addon)
flow_template_app = typer.Typer(
    help="Manage server templates and extensions",
    invoke_without_command=True,
    no_args_is_help=False,
)
flow_app.add_typer(flow_template_app, name="template")


@flow_template_app.callback()
def flow_template_callback(ctx: typer.Context):
    """Template management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_template_menu

        launch_template_menu()
        raise typer.Exit()


@flow_template_app.command("list")
def flow_template_list(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text for scripting"
    ),
):
    """List all available templates."""
    from navig.commands.template import list_templates_cmd

    ctx.obj["plain"] = plain
    list_templates_cmd(ctx.obj)


@flow_template_app.command("show")
def flow_template_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name"),
):
    """Show template details."""
    from navig.commands.template import show_template_cmd

    show_template_cmd(name, ctx.obj)


@flow_template_app.command("add")
def flow_template_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable/add a template."""
    from navig.commands.template import enable_template_cmd

    enable_template_cmd(name, ctx.obj)


@flow_template_app.command("remove")
def flow_template_remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable/remove a template."""
    from navig.commands.template import disable_template_cmd

    disable_template_cmd(name, ctx.obj)


@flow_template_app.command("run")
def flow_template_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to deploy"),
    command: str | None = typer.Argument(None, help="Template command to run"),
    args: list[str] | None = typer.Argument(
        None, help="Arguments for the template command"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without changes"
    ),
):
    """Deploy/run a template."""
    from navig.commands.template import deploy_template_cmd

    deploy_template_cmd(
        name,
        command_name=command,
        command_args=args or [],
        dry_run=dry_run,
        ctx_obj=ctx.obj,
    )


# DEPRECATED: workflow_app and task alias - use flow instead
workflow_app = typer.Typer(
    help="[DEPRECATED: Use 'navig flow'] Manage workflows",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(workflow_app, name="workflow", hidden=True)  # Deprecated
# Removed "task" alias — task_app (line ~1487) is the canonical registration


@workflow_app.callback()
def workflow_callback(ctx: typer.Context):
    """Workflow management - DEPRECATED, use 'navig flow'."""
    deprecation_warning("navig workflow/task", "navig flow")
    if ctx.invoked_subcommand is None:
        from navig.commands.workflow import list_workflows

        list_workflows()


@workflow_app.command("list")
def workflow_list():
    """List all available workflows."""
    from navig.commands.workflow import list_workflows

    list_workflows()


@workflow_app.command("show")
def workflow_show(name: str = typer.Argument(..., help="Workflow name")):
    """Display workflow definition and steps."""
    from navig.commands.workflow import show_workflow

    show_workflow(name)


@workflow_app.command("run")
def workflow_run(
    name: str = typer.Argument(..., help="Workflow name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without executing"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip all confirmation prompts"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: list[str] | None = typer.Option(
        None, "--var", "-V", help="Variable override (name=value)"
    ),
):
    """Execute a workflow."""
    from navig.commands.workflow import run_workflow

    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@workflow_app.command("test")
def workflow_test(name: str = typer.Argument(..., help="Workflow name")):
    """Test/validate workflow syntax and structure (canonical command)."""
    from navig.commands.workflow import validate_workflow

    validate_workflow(name)


@workflow_app.command("validate", hidden=True)
def workflow_validate(name: str = typer.Argument(..., help="Workflow name")):
    """[DEPRECATED: Use 'navig workflow test'] Validate workflow."""
    deprecation_warning("navig workflow validate", "navig workflow test")
    from navig.commands.workflow import validate_workflow

    validate_workflow(name)


@workflow_app.command("add")
def workflow_add(
    name: str = typer.Argument(..., help="New workflow name"),
    global_scope: bool = typer.Option(
        False, "--global", "-g", help="Create in global directory"
    ),
):
    """Add/create a new workflow (canonical command)."""
    from navig.commands.workflow import create_workflow

    create_workflow(name, global_scope=global_scope)


@workflow_app.command("create", hidden=True)
def workflow_create(
    name: str = typer.Argument(..., help="New workflow name"),
    global_scope: bool = typer.Option(
        False, "--global", "-g", help="Create in global directory"
    ),
):
    """[DEPRECATED: Use 'navig workflow add'] Create new workflow."""
    deprecation_warning("navig workflow create", "navig workflow add")
    from navig.commands.workflow import create_workflow

    create_workflow(name, global_scope=global_scope)


@workflow_app.command("remove")
def workflow_remove(
    name: str = typer.Argument(..., help="Workflow name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove/delete a workflow (canonical command)."""
    from navig.commands.workflow import delete_workflow

    delete_workflow(name, force=force)


@workflow_app.command("delete", hidden=True)
def workflow_delete(
    name: str = typer.Argument(..., help="Workflow name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """[DEPRECATED: Use 'navig workflow remove'] Delete workflow."""
    deprecation_warning("navig workflow delete", "navig workflow remove")
    from navig.commands.workflow import delete_workflow

    delete_workflow(name, force=force)


@workflow_app.command("edit")
def workflow_edit(name: str = typer.Argument(..., help="Workflow name")):
    """Open workflow in default editor."""
    from navig.commands.workflow import edit_workflow

    edit_workflow(name)


# ============================================================================
# WIKI MANAGEMENT
# ============================================================================

# Lazy-load wiki commands so `navig --help` stays fast.
from typer.core import TyperGroup


class _LazyWikiGroup(TyperGroup):
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        # Import only when the user actually invokes `navig wiki ...`.
        from typer.main import get_command

        from navig.commands import wiki as wiki_module

        wiki_click_cmd = get_command(wiki_module.wiki_app)
        # Copy subcommands from the real wiki group into this group.
        if hasattr(wiki_click_cmd, "commands"):
            for name, cmd in wiki_click_cmd.commands.items():
                # Avoid overwriting anything already registered.
                if name not in self.commands:
                    self.add_command(cmd, name)

        self._loaded = True

    def get_command(self, ctx, cmd_name):
        self._ensure_loaded()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        self._ensure_loaded()
        return super().list_commands(ctx)


wiki_app = typer.Typer(
    help="Wiki & knowledge base management",
    invoke_without_command=True,
    no_args_is_help=False,
    cls=_LazyWikiGroup,
)


@wiki_app.callback()
def wiki_callback(ctx: typer.Context):
    """Wiki commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("wiki", ctx)
        raise typer.Exit()


app.add_typer(wiki_app, name="wiki")


# ============================================================================
# DISPATCH - MULTI-NETWORK RELIABLE MESSAGE ROUTER
# ============================================================================


class _LazyDispatchGroup(TyperGroup):
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        from typer.main import get_command

        from navig.commands import dispatch as dispatch_module

        cmd = get_command(dispatch_module.dispatch_app)
        if hasattr(cmd, "commands"):
            for name, c in cmd.commands.items():
                if name not in self.commands:
                    self.add_command(c, name)
        self._loaded = True

    def get_command(self, ctx, cmd_name):
        self._ensure_loaded()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        self._ensure_loaded()
        return super().list_commands(ctx)


dispatch_app = typer.Typer(
    help="Multi-network reliable message dispatch (Telegram, Discord, Matrix)",
    invoke_without_command=True,
    no_args_is_help=False,
    cls=_LazyDispatchGroup,
)


@dispatch_app.callback()
def dispatch_cli_callback(ctx: typer.Context):
    """Dispatch commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("dispatch", ctx)
        raise typer.Exit()


app.add_typer(dispatch_app, name="dispatch")


# ============================================================================
# CONTACTS - ADDRESS BOOK FOR NL ROUTING
# ============================================================================


class _LazyContactsGroup(TyperGroup):
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        from typer.main import get_command

        from navig.commands import dispatch as dispatch_module

        cmd = get_command(dispatch_module.contacts_app)
        if hasattr(cmd, "commands"):
            for name, c in cmd.commands.items():
                if name not in self.commands:
                    self.add_command(c, name)
        self._loaded = True

    def get_command(self, ctx, cmd_name):
        self._ensure_loaded()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        self._ensure_loaded()
        return super().list_commands(ctx)


contacts_app = typer.Typer(
    help="Address book for NL contact routing (Phase 2)",
    invoke_without_command=True,
    no_args_is_help=False,
    cls=_LazyContactsGroup,
)


@contacts_app.callback()
def contacts_cli_callback(ctx: typer.Context):
    """Contacts commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("contacts", ctx)
        raise typer.Exit()


app.add_typer(contacts_app, name="contacts")


# ============================================================================
# GATEWAY - AUTONOMOUS AGENT CONTROL PLANE
# ============================================================================

gateway_app = typer.Typer(
    help="Autonomous agent gateway server (24/7 control plane)",
    invoke_without_command=True,
    no_args_is_help=False,
)


_DEFAULT_GATEWAY_PORT = 8789
_DEFAULT_GATEWAY_HOST = "127.0.0.1"


def _load_gateway_cli_defaults() -> tuple[int, str]:
    """Return gateway port/host from config with stable CLI fallbacks."""
    from navig.gateway.client import gateway_cli_defaults

    return gateway_cli_defaults()


def _gateway_request_headers() -> dict[str, str]:
    """Return auth headers for gateway API calls when configured."""
    from navig.gateway.client import gateway_request_headers

    return gateway_request_headers()


def _gw_request(method: str, path: str, **kwargs):
    """Send an authenticated request to the local gateway."""
    from navig.gateway.client import gateway_request

    return gateway_request(method, path, **kwargs)


def _gw_base_url() -> str:
    """Return local gateway base URL from config (gateway.port / host)."""
    from navig.gateway.client import gateway_base_url

    return gateway_base_url()


@gateway_app.callback()
def gateway_callback(ctx: typer.Context):
    """Gateway commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("gateway", ctx)
        raise typer.Exit()


@gateway_app.command("start")
def gateway_start(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port (default: gateway.port from config, fallback 8789)",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Bind address (default: gateway.host from config, fallback 0.0.0.0)",
    ),
    background: bool = typer.Option(
        False, "--background", "-b", help="Run in background"
    ),
):
    """
    Start the autonomous agent gateway server.

    The gateway provides:
    - HTTP/WebSocket API for agent communication
    - Session persistence across restarts
    - Heartbeat-based health monitoring
    - Cron job scheduling
    - Multi-channel message routing

    Examples:
        navig gateway start
        navig gateway start --port 9000
        navig gateway start --background
    """
    import asyncio

    # Fill port/host from config if not explicitly passed
    if port is None:
        port, _configured_host = _load_gateway_cli_defaults()
    if host is None:
        _port, configured_host = _load_gateway_cli_defaults()
        host = configured_host

    ch.info(f"Starting NAVIG Gateway on {host}:{port}...")

    try:
        from navig.gateway import GatewayConfig, NavigGateway

        # Build config dict for GatewayConfig
        raw_config = {
            "gateway": {
                "enabled": True,
                "port": port,
                "host": host,
            }
        }

        gateway_config = GatewayConfig(raw_config)

        if background:
            import subprocess

            cmd = [
                sys.executable,
                "-m",
                "navig",
                "gateway",
                "start",
                "--port",
                str(port),
                "--host",
                host,
            ]

            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "close_fds": True,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )

            proc = subprocess.Popen(cmd, **kwargs)
            ch.success(f"Gateway started in background (pid={proc.pid})")
            ch.info("Check status with: navig gateway status")
            return

        gateway = NavigGateway(config=gateway_config)
        asyncio.run(gateway.start())

    except KeyboardInterrupt:
        ch.info("Gateway stopped by user")
    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("Install with: pip install aiohttp")
    except Exception as e:
        ch.error(f"Gateway error: {e}")


@gateway_app.command("stop")
def gateway_stop():
    """
    Stop the running gateway server.

    Sends a shutdown signal to the running gateway via its API.
    If the gateway is running in the foreground, use Ctrl+C instead.

    Examples:
        navig gateway stop
    """
    import requests

    try:
        # First check if gateway is running
        try:
            health_response = _gw_request("GET", "/health", timeout=2)
            if health_response.status_code != 200:
                ch.warning("Gateway does not appear to be running")
                return
        except Exception:
            ch.warning("Gateway is not running")
            return

        # Try to stop via API
        try:
            response = _gw_request("POST", "/shutdown", timeout=5)
            if response.status_code == 200:
                ch.success("Gateway shutdown signal sent")
            else:
                ch.warning(f"Shutdown request returned status {response.status_code}")
                ch.info("If running in foreground, use Ctrl+C to stop")
        except requests.exceptions.ConnectionError:
            # Connection closed - gateway probably stopped
            ch.success("Gateway stopped")
        except Exception as e:
            ch.warning(f"Could not send shutdown signal: {e}")
            ch.info("If running in foreground, use Ctrl+C to stop")
            ch.info("Or kill the process manually")
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")


@gateway_app.command("status")
def gateway_status():
    """Show gateway status."""
    import requests

    try:
        # Get detailed status from /status endpoint
        response = _gw_request("GET", "/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            ch.success("Gateway is running")
            ch.info(f"  Status: {data.get('status', 'unknown')}")

            # Format uptime nicely
            uptime_sec = data.get("uptime_seconds")
            if uptime_sec:
                hours, remainder = divmod(int(uptime_sec), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    ch.info(f"  Uptime: {hours}h {minutes}m {seconds}s")
                elif minutes > 0:
                    ch.info(f"  Uptime: {minutes}m {seconds}s")
                else:
                    ch.info(f"  Uptime: {seconds}s")

            # Show session count
            sessions = data.get("sessions", {})
            if sessions:
                ch.info(f"  Active sessions: {sessions.get('active', 0)}")

            # Show cron/heartbeat summary
            cron = data.get("cron", {})
            if cron:
                ch.info(
                    f"  Cron jobs: {cron.get('jobs', 0)} ({cron.get('enabled_jobs', 0)} enabled)"
                )

            hb = data.get("heartbeat", {})
            if hb.get("running"):
                ch.info("  Heartbeat: active")
        else:
            ch.warning(f"Gateway returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error checking gateway: {e}")


@gateway_app.command("session")
def gateway_session(
    action: str = typer.Argument(..., help="Action: list, show, clear"),
    session_key: str = typer.Argument(None, help="Session key (for show/clear)"),
):
    """
    Manage gateway sessions.

    Examples:
        navig gateway session list
        navig gateway session show agent:default:telegram:123
        navig gateway session clear agent:default:telegram:123
    """
    import requests

    try:
        if action == "list":
            response = _gw_request("GET", "/sessions", timeout=5)
            if response.status_code == 200:
                sessions = response.json().get("sessions", [])
                if sessions:
                    ch.info(f"Active sessions ({len(sessions)}):")
                    for s in sessions:
                        ch.info(f"  • {s.get('key', 'unknown')}")
                else:
                    ch.info("No active sessions")
            else:
                ch.error(f"Failed to list sessions: {response.status_code}")

        elif action == "show" and session_key:
            response = _gw_request("GET", f"/sessions/{session_key}", timeout=5)
            if response.status_code == 200:
                session = response.json()
                ch.info(f"Session: {session_key}")
                ch.info(f"  Messages: {session.get('message_count', 0)}")
                ch.info(f"  Created: {session.get('created_at', 'unknown')}")
                ch.info(f"  Updated: {session.get('updated_at', 'unknown')}")
            else:
                ch.error(f"Session not found: {session_key}")

        elif action == "clear" and session_key:
            response = _gw_request("DELETE", f"/sessions/{session_key}", timeout=5)
            if response.status_code == 200:
                ch.success(f"Cleared session: {session_key}")
            else:
                ch.error(f"Failed to clear session: {response.status_code}")

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Actions: list, show <key>, clear <key>")

    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(gateway_app, name="gateway")

# ============================================================================
# BRIDGE / FARMORE / COPILOT — deferred to _register_external_commands
# ============================================================================
# QUANTUM VELOCITY K4: These were imported eagerly at module level, paying the
# full import cost (~30-60ms) even for unrelated commands like `navig host list`.
# They are now registered lazily via _EXTERNAL_CMD_MAP in _register_external_commands
# and only imported when the user actually invokes `navig bridge|farmore|copilot`.
# (entries added to _EXTERNAL_CMD_MAP below)


# ============================================================================
# BOT - TELEGRAM BOT LAUNCHER
# ============================================================================

bot_app = typer.Typer(
    help="Telegram bot and multi-channel agent launcher",
    invoke_without_command=True,
    no_args_is_help=False,
)


@bot_app.callback()
def bot_callback(ctx: typer.Context):
    """Bot commands - run without subcommand to start bot."""
    if ctx.invoked_subcommand is None:
        # Default action: start bot in direct mode
        ctx.invoke(bot_start)


@bot_app.command("start")
def bot_start(
    gateway: bool = typer.Option(
        False, "--gateway", "-g", help="Start with gateway (session persistence)"
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Gateway port (default: gateway.port from config, fallback 8789)",
    ),
    background: bool = typer.Option(
        False, "--background", "-b", help="Run in background"
    ),
):
    """
    Start the NAVIG Telegram bot.

    By default runs in direct mode (standalone).
    Use --gateway to start both gateway and bot together.

    Examples:
        navig bot                    # Start bot (direct mode)
        navig bot --gateway          # Start gateway + bot together
        navig bot -g -p 9000         # Gateway on custom port
    """
    import os
    import subprocess

    # Check for telegram token
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        ch.error("TELEGRAM_BOT_TOKEN not set!")
        ch.info("  Get token from @BotFather on Telegram")
        ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
        raise typer.Exit(1)

    if gateway:
        if port is None:
            port, _host = _load_gateway_cli_defaults()
        ch.info("Starting NAVIG with Gateway + Telegram Bot...")
        ch.info(f"  Gateway: http://localhost:{port}")
        ch.info("  Bot: Telegram")
        cmd = [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
            "--port",
            str(port),
        ]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)
    else:
        ch.info("Starting NAVIG Telegram Bot (direct mode)...")
        ch.warning("⚠️  Conversations reset on bot restart")
        ch.info("   Use 'navig bot --gateway' for session persistence")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)


@bot_app.command("status")
def bot_status():
    """Check if bot is running."""
    import subprocess

    patterns = (
        r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"
    )

    # Check for running python processes with navig_bot
    try:
        if sys.platform == "win32":
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
            )
            pids = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip().isdigit()
            ]
            if pids:
                ch.success("Bot appears to be running")
                ch.info(f"  PIDs: {', '.join(pids)}")
            else:
                ch.warning("Bot does not appear to be running")
        else:
            result = subprocess.run(
                ["pgrep", "-f", patterns], capture_output=True, text=True
            )
            if result.returncode == 0:
                ch.success("Bot is running")
                ch.info(f"  PIDs: {result.stdout.strip()}")
            else:
                ch.warning("Bot is not running")
    except Exception as e:
        ch.error(f"Could not check status: {e}")


@bot_app.command("stop")
def bot_stop():
    """Stop all running NAVIG bot/gateway processes."""
    import subprocess

    patterns = (
        r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"
    )

    try:
        if sys.platform == "win32":
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            find_result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
            )
            pids = [
                line.strip()
                for line in find_result.stdout.splitlines()
                if line.strip().isdigit()
            ]
            if not pids:
                ch.warning("No running processes found")
                return
            for pid in pids:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/T", "/F"],
                    capture_output=True,
                    text=True,
                )
            ch.success(f"Stopped NAVIG bot/gateway processes: {', '.join(pids)}")
        else:
            result = subprocess.run(
                ["pkill", "-f", patterns], capture_output=True, text=True
            )
            if result.returncode == 0:
                ch.success("Stopped NAVIG bot/gateway")
            else:
                ch.warning("No running processes found")
    except Exception as e:
        ch.error(f"Error stopping: {e}")


app.add_typer(bot_app, name="bot")


# ============================================================================
# START - QUICK LAUNCHER (ALIAS)
# ============================================================================


@app.command("start")
def quick_start(
    bot: bool = typer.Option(
        True, "--bot/--no-bot", "-b/-B", help="Start Telegram bot"
    ),
    gateway: bool = typer.Option(
        True, "--gateway/--no-gateway", "-g/-G", help="Start gateway"
    ),
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
    import os
    import subprocess

    if bot:
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not telegram_token:
            ch.error("TELEGRAM_BOT_TOKEN not set!")
            ch.info("  Get token from @BotFather on Telegram")
            ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
            raise typer.Exit(1)

    if gateway and port is None:
        port, _host = _load_gateway_cli_defaults()

    if gateway and bot:
        ch.info("Starting NAVIG (Gateway + Telegram Bot)...")
        cmd = [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
            "--port",
            str(port),
        ]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
            ch.info(f"  Gateway: http://localhost:{port}")
            ch.info("  Status: navig bot status")
            ch.info("  Stop: navig bot stop")
        else:
            os.execv(sys.executable, cmd)

    elif bot:
        ch.info("Starting NAVIG Telegram Bot (standalone)...")
        ch.warning("⚠️  Conversations reset on restart")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)

    elif gateway:
        ch.info(f"Starting NAVIG Gateway on port {port}...")
        gateway_start(port=port, host="0.0.0.0", background=background)


# ============================================================================
# HEARTBEAT - PERIODIC HEALTH CHECKS
# ============================================================================

heartbeat_app = typer.Typer(
    help="Periodic health check system",
    invoke_without_command=True,
    no_args_is_help=False,
)


@heartbeat_app.callback()
def heartbeat_callback(ctx: typer.Context):
    """Heartbeat commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("heartbeat", ctx)
        raise typer.Exit()


@heartbeat_app.command("status")
def heartbeat_status():
    """Show heartbeat status."""
    from datetime import datetime

    import requests

    try:
        response = _gw_request("GET", "/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            hb = data.get("heartbeat", {})
            config = data.get("config", {})

            if hb.get("running"):
                ch.success("Heartbeat is running")

                # Get interval from config
                interval = config.get("heartbeat_interval", "30m")
                ch.info(f"  Interval: {interval}")

                # Parse and display next run time
                next_run = hb.get("next_run")
                if next_run:
                    try:
                        next_dt = datetime.fromisoformat(
                            next_run.replace("Z", "+00:00")
                        )
                        now = (
                            datetime.now(next_dt.tzinfo)
                            if next_dt.tzinfo
                            else datetime.now()
                        )
                        diff = next_dt - now
                        minutes = int(diff.total_seconds() / 60)
                        if minutes > 0:
                            ch.info(f"  Next check: in {minutes} minutes")
                        else:
                            ch.info("  Next check: imminent")
                    except Exception:
                        ch.info(f"  Next check: {next_run}")
                else:
                    ch.info("  Next check: unknown")

                # Display last run
                last_run = hb.get("last_run")
                if last_run:
                    ch.info(f"  Last run: {last_run}")
                else:
                    ch.info("  Last run: never")
            else:
                ch.warning("Heartbeat is not running")
                ch.info("Start gateway to enable heartbeat: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("trigger")
def heartbeat_trigger():
    """Trigger an immediate heartbeat check."""
    import requests

    ch.info("Triggering heartbeat check...")

    try:
        response = _gw_request("POST", "/heartbeat/trigger", timeout=300)
        if response.status_code == 200:
            result = response.json()
            if result.get("suppressed"):
                ch.success("HEARTBEAT_OK - All systems healthy")
            elif result.get("issues"):
                ch.warning(f"Issues found: {len(result['issues'])}")
                for issue in result["issues"]:
                    ch.warning(f"  • {issue}")
            else:
                ch.success("Heartbeat completed")
        else:
            ch.error(f"Heartbeat failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("history")
def heartbeat_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of entries to show"),
):
    """Show heartbeat history."""
    import requests

    try:
        response = _gw_request("GET", f"/heartbeat/history?limit={limit}", timeout=5)
        if response.status_code == 200:
            history = response.json().get("history", [])
            if history:
                ch.info(f"Heartbeat history (last {len(history)}):")
                for entry in history:
                    status = "✅" if entry.get("success") else "❌"
                    suppressed = " (OK)" if entry.get("suppressed") else ""
                    ch.info(
                        f"  {status} {entry.get('timestamp', '?')}{suppressed} - {entry.get('duration', 0):.1f}s"
                    )
            else:
                ch.info("No heartbeat history")
        else:
            ch.error(f"Failed to get history: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("configure")
def heartbeat_configure(
    interval: int = typer.Option(None, "--interval", "-i", help="Interval in minutes"),
    enable: bool = typer.Option(
        None, "--enable/--disable", help="Enable/disable heartbeat"
    ),
):
    """Configure heartbeat settings."""
    config_manager = _get_config_manager()

    if interval is not None or enable is not None:
        config = config_manager.global_config
        if "heartbeat" not in config:
            config["heartbeat"] = {}

        if interval is not None:
            config["heartbeat"]["interval"] = interval
            ch.success(f"Set heartbeat interval to {interval} minutes")

        if enable is not None:
            config["heartbeat"]["enabled"] = enable
            ch.success(f"Heartbeat {'enabled' if enable else 'disabled'}")

        config_manager.save_global()
    else:
        # Show current config
        config = config_manager.global_config
        hb = config.get("heartbeat", {})
        ch.info("Heartbeat configuration:")
        ch.info(f"  Enabled: {hb.get('enabled', True)}")
        ch.info(f"  Interval: {hb.get('interval', 30)} minutes")
        ch.info(f"  Timeout: {hb.get('timeout', 300)} seconds")


app.add_typer(heartbeat_app, name="heartbeat")


# ============================================================================
# CRON - PERSISTENT JOB SCHEDULING
# ============================================================================

cron_app = typer.Typer(
    help="Persistent job scheduling",
    invoke_without_command=True,
    no_args_is_help=False,
)


@cron_app.callback()
def cron_callback(ctx: typer.Context):
    """Cron commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("cron", ctx)
        raise typer.Exit()


@cron_app.command("list")
def cron_list():
    """List all scheduled jobs."""
    import requests

    try:
        response = _gw_request("GET", "/cron/jobs", timeout=5)
        if response.status_code == 200:
            jobs = response.json().get("jobs", [])
            if jobs:
                ch.info(f"Scheduled jobs ({len(jobs)}):")
                for job in jobs:
                    status = "✅" if job.get("enabled") else "⏸️"
                    next_run = job.get("next_run", "N/A")
                    ch.info(f"  {status} [{job.get('id')}] {job.get('name')}")
                    ch.info(f"      Schedule: {job.get('schedule')}")
                    ch.info(f"      Next run: {next_run}")
            else:
                ch.info("No scheduled jobs")
                ch.info(
                    'Add one with: navig cron add "job name" "every 30 minutes" "navig host test"'
                )
        else:
            ch.error(f"Failed to list jobs: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="Job name"),
    schedule: str = typer.Argument(
        ..., help="Schedule (e.g., 'every 30 minutes', '0 * * * *')"
    ),
    command: str = typer.Argument(..., help="Command to run"),
    disabled: bool = typer.Option(
        False, "--disabled", help="Create job in disabled state"
    ),
):
    """
    Add a new scheduled job.

    Schedule formats:
    - Natural language: "every 30 minutes", "hourly", "daily"
    - Cron expression: "*/5 * * * *", "0 9 * * *"

    Examples:
        navig cron add "Disk check" "every 30 minutes" "navig host monitor disk"
        navig cron add "Daily backup" "0 2 * * *" "navig backup export"
        navig cron add "Health check" "hourly" "Check all hosts and report issues"
    """
    import requests

    try:
        response = _gw_request(
            "POST",
            "/cron/jobs",
            json={
                "name": name,
                "schedule": schedule,
                "command": command,
                "enabled": not disabled,
            },
            timeout=5,
        )
        if response.status_code == 200:
            job = response.json()
            ch.success(f"Created job: {job.get('id')}")
            ch.info(f"  Name: {name}")
            ch.info(f"  Schedule: {schedule}")
            ch.info(f"  Next run: {job.get('next_run', 'N/A')}")
        else:
            ch.error(f"Failed to create job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    import requests

    try:
        response = _gw_request("DELETE", f"/cron/jobs/{job_id}", timeout=5)
        if response.status_code == 200:
            ch.success(f"Removed job: {job_id}")
        else:
            ch.error(f"Failed to remove job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
):
    """Run a job immediately."""
    import requests

    ch.info(f"Running job {job_id}...")

    try:
        response = _gw_request("POST", f"/cron/jobs/{job_id}/run", timeout=300)
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                ch.success("Job completed successfully")
                if result.get("output"):
                    ch.info(f"Output:\n{result['output'][:1000]}")
            else:
                ch.error(f"Job failed: {result.get('error', 'unknown')}")
        else:
            ch.error(f"Failed to run job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID to enable"),
):
    """Enable a disabled job."""
    import requests

    try:
        response = _gw_request("POST", f"/cron/jobs/{job_id}/enable", timeout=5)
        if response.status_code == 200:
            ch.success(f"Enabled job: {job_id}")
        else:
            ch.error(f"Failed to enable job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("disable")
def cron_disable(
    job_id: str = typer.Argument(..., help="Job ID to disable"),
):
    """Disable a job without removing it."""
    import requests

    try:
        response = _gw_request("POST", f"/cron/jobs/{job_id}/disable", timeout=5)
        if response.status_code == 200:
            ch.success(f"Disabled job: {job_id}")
        else:
            ch.error(f"Failed to disable job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("status")
def cron_status():
    """Show cron service status."""
    import requests

    try:
        response = _gw_request("GET", "/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            cron = data.get("cron", {})

            # Cron is running if gateway is up and jobs exist
            total_jobs = cron.get("jobs", cron.get("total_jobs", 0))
            enabled_jobs = cron.get("enabled_jobs", 0)

            if data.get("status") == "running":
                ch.success("Cron service is running")
                ch.info(f"  Total jobs: {total_jobs}")
                ch.info(f"  Enabled jobs: {enabled_jobs}")
                if cron.get("next_job"):
                    ch.info(
                        f"  Next job: {cron.get('next_job')} in {cron.get('next_run_in', '?')}"
                    )
            else:
                ch.warning("Cron service is not running")
                ch.info("Start gateway to enable cron: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start gateway to enable cron: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(cron_app, name="cron")


# ============================================================================
# APPROVAL SYSTEM (Human-in-the-loop for agent actions)
# ============================================================================

approve_app = typer.Typer(
    help="Human approval system for agent actions",
    invoke_without_command=True,
    no_args_is_help=False,
)


@approve_app.callback()
def approve_callback(ctx: typer.Context):
    """Approval management - run without subcommand to list pending."""
    if ctx.invoked_subcommand is None:
        approve_list()


@approve_app.command("list")
def approve_list():
    """List pending approval requests."""
    import requests

    try:
        response = _gw_request("GET", "/approval/pending", timeout=5)
        if response.status_code == 200:
            data = response.json()
            pending = data.get("pending", [])

            if not pending:
                ch.info("No pending approval requests")
                return

            ch.info(f"Pending approval requests ({len(pending)}):")
            for req in pending:
                level_color = {
                    "confirm": "yellow",
                    "dangerous": "red",
                    "never": "bright_red",
                }.get(req.get("level", ""), "white")

                ch.console.print(
                    f"  [{req['id']}] {req['action']} ({req['level']}) - {req.get('description', '')}",
                    style=level_color,
                )
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("yes")
def approve_yes(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Approve a pending request."""
    import requests

    try:
        response = _gw_request(
            "POST",
            f"/approval/{request_id}/respond",
            json={"approved": True, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} approved")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("no")
def approve_no(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Deny a pending request."""
    import requests

    try:
        response = _gw_request(
            "POST",
            f"/approval/{request_id}/respond",
            json={"approved": False, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} denied")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("policy")
def approve_policy():
    """Show approval policy (patterns and levels)."""
    try:
        from navig.approval import ApprovalPolicy

        policy = ApprovalPolicy.default()

        ch.info("Approval Policy Patterns:")
        ch.console.print("\n[bold green]SAFE (no approval needed):[/bold green]")
        for pattern in policy.patterns.get("safe", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold yellow]CONFIRM (requires approval):[/bold yellow]")
        for pattern in policy.patterns.get("confirm", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold red]DANGEROUS (always confirm):[/bold red]")
        for pattern in policy.patterns.get("dangerous", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold bright_red]NEVER (always denied):[/bold bright_red]")
        for pattern in policy.patterns.get("never", []):
            ch.console.print(f"  • {pattern}")
    except ImportError:
        ch.error("Approval module not available")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(approve_app, name="approve")


# ============================================================================
# BROWSER AUTOMATION
# ============================================================================

browser_app = typer.Typer(
    help="Browser automation for web tasks",
    invoke_without_command=True,
    no_args_is_help=False,
)


@browser_app.callback()
def browser_callback(ctx: typer.Context):
    """Browser automation - run without subcommand to show status."""
    if ctx.invoked_subcommand is None:
        browser_status()


@browser_app.command("status")
def browser_status():
    """Show browser status."""
    import requests

    try:
        response = _gw_request("GET", "/browser/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("started"):
                ch.success("Browser is running")
                if data.get("has_page"):
                    ch.info("  Active page loaded")
                else:
                    ch.info("  No page loaded")
            else:
                ch.info("Browser is not running")
        elif response.status_code == 503:
            ch.warning("Browser module not available (install playwright)")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("open")
def browser_open(
    url: str = typer.Argument(..., help="URL to navigate to"),
):
    """Navigate browser to URL."""
    import requests

    try:
        response = _gw_request(
            "POST",
            "/browser/navigate",
            json={"url": url},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Navigated to: {url}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("screenshot")
def browser_screenshot(
    path: str | None = typer.Option(None, "--path", "-p", help="Save path"),
    full_page: bool = typer.Option(False, "--full", "-f", help="Capture full page"),
):
    """Capture browser screenshot."""
    import requests

    try:
        response = _gw_request(
            "POST",
            "/browser/screenshot",
            json={"path": path, "full_page": full_page},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Screenshot saved: {data.get('path', 'unknown')}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("click")
def browser_click(
    selector: str = typer.Argument(..., help="CSS selector to click"),
):
    """Click element on page."""
    import requests

    try:
        response = _gw_request(
            "POST",
            "/browser/click",
            json={"selector": selector},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Clicked: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("fill")
def browser_fill(
    selector: str = typer.Argument(..., help="CSS selector for input"),
    value: str = typer.Argument(..., help="Value to fill"),
):
    """Fill input field."""
    import requests

    try:
        response = _gw_request(
            "POST",
            "/browser/fill",
            json={"selector": selector, "value": value},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Filled: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("stop")
def browser_stop():
    """Stop browser."""
    import requests

    try:
        response = _gw_request("POST", "/browser/stop", timeout=10)
        if response.status_code == 200:
            ch.success("Browser stopped")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


# browser_app moved to navig/commands/browser.py — registered lazily via _EXTERNAL_CMD_MAP
# app.add_typer(browser_app, name="browser")  # ← removed to avoid double-registration


# ============================================================================
# TASK QUEUE (Async operations queue)
# ============================================================================

queue_app = typer.Typer(
    help="Task queue for async operations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@queue_app.callback()
def queue_callback(ctx: typer.Context):
    """Task queue - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        queue_list()


@queue_app.command("list")
def queue_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max tasks to show"),
):
    """List queued tasks."""
    import requests

    try:
        params = {"limit": limit}
        if status:
            params["status"] = status

        response = _gw_request("GET", "/tasks", params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            tasks = data.get("tasks", [])

            if not tasks:
                ch.info("No tasks in queue")
                return

            ch.info(f"Tasks ({len(tasks)}):")
            for task in tasks:
                status_color = {
                    "pending": "blue",
                    "queued": "cyan",
                    "running": "yellow",
                    "completed": "green",
                    "failed": "red",
                    "cancelled": "dim",
                }.get(task.get("status", ""), "white")

                ch.console.print(
                    f"  [{task['id']}] {task['name']} - {task['status']}",
                    style=status_color,
                )
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("add")
def queue_add(
    name: str = typer.Argument(..., help="Task name"),
    handler: str = typer.Argument(..., help="Handler to execute"),
    params: str | None = typer.Option(None, "--params", "-p", help="JSON params"),
    priority: int = typer.Option(50, "--priority", help="Priority (lower = higher)"),
):
    """Add a task to the queue."""
    import json as json_mod

    import requests

    try:
        task_params = {}
        if params:
            task_params = json_mod.loads(params)

        response = _gw_request(
            "POST",
            "/tasks",
            json={
                "name": name,
                "handler": handler,
                "params": task_params,
                "priority": priority,
            },
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Task added: {data.get('id')}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except json_mod.JSONDecodeError:
        ch.error("Invalid JSON in --params")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("show")
def queue_show(
    task_id: str = typer.Argument(..., help="Task ID"),
):
    """Show task details."""
    import requests

    try:
        response = _gw_request("GET", f"/tasks/{task_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            ch.info(f"Task: {data.get('name', 'unknown')}")
            ch.console.print(f"  ID: {data.get('id')}")
            ch.console.print(f"  Handler: {data.get('handler')}")
            ch.console.print(f"  Status: {data.get('status')}")
            ch.console.print(f"  Priority: {data.get('priority')}")
            if data.get("error"):
                ch.console.print(f"  Error: {data.get('error')}", style="red")
            if data.get("result"):
                ch.console.print(f"  Result: {data.get('result')}")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("cancel")
def queue_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel"),
):
    """Cancel a pending task."""
    import requests

    try:
        response = _gw_request("POST", f"/tasks/{task_id}/cancel", timeout=5)
        if response.status_code == 200:
            ch.success(f"Task {task_id} cancelled")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("stats")
def queue_stats():
    """Show queue statistics."""
    import requests

    try:
        response = _gw_request("GET", "/tasks/stats", timeout=5)
        if response.status_code == 200:
            data = response.json()

            ch.info("Task Queue Statistics:")
            ch.console.print(f"  Total tasks: {data.get('total_tasks', 0)}")
            ch.console.print(f"  Heap size: {data.get('heap_size', 0)}")
            ch.console.print(f"  Completed: {data.get('completed_count', 0)}")

            counts = data.get("status_counts", {})
            if counts:
                ch.console.print("\n  Status breakdown:")
                for status, count in counts.items():
                    ch.console.print(f"    {status}: {count}")

            worker = data.get("worker", {})
            if worker:
                ch.console.print("\n  Worker:")
                ch.console.print(f"    Running: {worker.get('running', False)}")
                ch.console.print(f"    Active tasks: {worker.get('active_tasks', 0)}")
                ch.console.print(f"    Completed: {worker.get('tasks_completed', 0)}")
                ch.console.print(f"    Failed: {worker.get('tasks_failed', 0)}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(queue_app, name="queue")


# ============================================================================
# MEMORY MANAGEMENT
# ============================================================================

memory_app = typer.Typer(
    help="Manage conversation memory and knowledge base",
    no_args_is_help=True,
)


@memory_app.command("sessions")
def memory_sessions(
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum sessions to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List conversation sessions."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore

        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation history yet")
            return

        store = ConversationStore(db_path)
        sessions = store.list_sessions(limit=limit)

        if not sessions:
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation sessions found")
            return

        if plain:
            for s in sessions:
                print(
                    f"{s.session_key}\t{s.message_count}\t{s.total_tokens}\t{s.updated_at.isoformat()}"
                )
        else:
            from rich.table import Table

            table = Table(title="Conversation Sessions")
            table.add_column("Session", style="cyan")
            table.add_column("Messages", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Last Updated", style="dim")

            for s in sessions:
                table.add_row(
                    s.session_key,
                    str(s.message_count),
                    str(s.total_tokens),
                    s.updated_at.strftime("%Y-%m-%d %H:%M"),
                )

            ch.console.print(table)

        store.close()

    except ImportError as e:
        ch.error(f"Memory module not available: {e}")
    except Exception as e:
        ch.error(f"Error listing sessions: {e}")


@memory_app.command("history")
def memory_history(
    session: str = typer.Argument(..., help="Session key to show"),
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum messages"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Show conversation history for a session."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore

        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            ch.error("No conversation history")
            return

        store = ConversationStore(db_path)
        messages = store.get_history(session, limit=limit)

        if not messages:
            ch.info(f"No messages in session '{session}'")
            store.close()
            return

        if plain:
            for m in messages:
                print(f"{m.role}\t{m.timestamp.isoformat()}\t{m.content[:100]}")
        else:
            ch.info(f"Session: {session} ({len(messages)} messages)")
            ch.console.print()

            for m in messages:
                role_style = "bold cyan" if m.role == "user" else "bold green"
                ch.console.print(
                    f"[{role_style}]{m.role.upper()}[/] ({m.timestamp.strftime('%H:%M')})"
                )
                ch.console.print(
                    m.content[:500] + ("..." if len(m.content) > 500 else "")
                )
                ch.console.print()

        store.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear")
def memory_clear(
    session: str = typer.Option(None, "--session", "-s", help="Clear specific session"),
    all_sessions: bool = typer.Option(False, "--all", help="Clear all sessions"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear conversation memory."""
    from pathlib import Path

    if not session and not all_sessions:
        ch.error("Specify --session or --all")
        raise typer.Exit(1)

    try:
        from navig.memory import ConversationStore

        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory" / "memory.db"

        if not db_path.exists():
            ch.info("No memory to clear")
            return

        if not force:
            target = "all sessions" if all_sessions else f"session '{session}'"
            if not typer.confirm(f"Clear {target}?"):
                raise typer.Abort()

        store = ConversationStore(db_path)

        if all_sessions:
            sessions = store.list_sessions(limit=1000)
            count = 0
            for s in sessions:
                if store.delete_session(s.session_key):
                    count += 1
            ch.success(f"Cleared {count} sessions")
        else:
            if store.delete_session(session):
                ch.success(f"Cleared session '{session}'")
            else:
                ch.warning(f"Session '{session}' not found")

        store.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("knowledge")
def memory_knowledge(
    action: str = typer.Argument("list", help="list, add, search, clear"),
    key: str = typer.Option(None, "--key", "-k", help="Knowledge key"),
    content: str = typer.Option(None, "--content", "-c", help="Knowledge content"),
    query: str = typer.Option(None, "--query", "-q", help="Search query"),
    tags: str = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    limit: int = typer.Option(20, "--limit", "-l", help="Result limit"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Manage knowledge base entries."""
    from pathlib import Path

    try:
        from navig.memory import KnowledgeBase, KnowledgeEntry

        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory" / "knowledge.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        kb = KnowledgeBase(db_path, embedding_provider=None)

        if action == "list":
            # List all entries
            entries = kb.export_entries()[:limit]

            if not entries:
                ch.info("Knowledge base is empty")
                return

            if plain:
                for e in entries:
                    print(f"{e['key']}\t{e['source']}\t{e['content'][:80]}")
            else:
                from rich.table import Table

                table = Table(title="Knowledge Base")
                table.add_column("Key", style="cyan")
                table.add_column("Source", style="dim")
                table.add_column("Content", max_width=50)
                table.add_column("Tags")

                for e in entries:
                    import json

                    tags_list = json.loads(e.get("tags", "[]"))
                    table.add_row(
                        e["key"],
                        e.get("source", ""),
                        (
                            e["content"][:50] + "..."
                            if len(e["content"]) > 50
                            else e["content"]
                        ),
                        ", ".join(tags_list),
                    )

                ch.console.print(table)

        elif action == "add":
            if not key or not content:
                ch.error("--key and --content required for add")
                raise typer.Exit(1)

            tag_list = [t.strip() for t in tags.split(",")] if tags else []

            entry = KnowledgeEntry(
                key=key,
                content=content,
                tags=tag_list,
                source="cli",
            )
            kb.upsert(entry, compute_embedding=False)
            ch.success(f"Added knowledge: {key}")

        elif action == "search":
            if not query:
                ch.error("--query required for search")
                raise typer.Exit(1)

            tag_list = [t.strip() for t in tags.split(",")] if tags else None
            results = kb.text_search(query, limit=limit, tags=tag_list)

            if not results:
                ch.info("No matching entries")
                return

            if plain:
                for e in results:
                    print(f"{e.key}\t{e.content[:80]}")
            else:
                for e in results:
                    ch.console.print(f"[cyan]{e.key}[/]")
                    ch.console.print(f"  {e.content[:200]}")
                    if e.tags:
                        ch.console.print(f"  Tags: {', '.join(e.tags)}")
                    ch.console.print()

        elif action == "clear":
            if not typer.confirm("Clear entire knowledge base?"):
                raise typer.Abort()
            count = kb.clear()
            ch.success(f"Cleared {count} entries")

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: list, add, search, clear")

        kb.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("stats")
def memory_stats():
    """Show memory usage statistics."""
    from pathlib import Path

    try:
        from navig.memory import ConversationStore, KnowledgeBase

        config = _get_config_manager()

        # Conversation stats
        conv_db = Path(config.global_config_dir) / "memory" / "memory.db"
        if conv_db.exists():
            store = ConversationStore(conv_db)
            sessions = store.list_sessions(limit=1000)
            total_messages = sum(s.message_count for s in sessions)
            total_tokens = sum(s.total_tokens for s in sessions)
            store.close()

            ch.info("Conversation Memory:")
            ch.console.print(f"  Sessions: {len(sessions)}")
            ch.console.print(f"  Messages: {total_messages}")
            ch.console.print(f"  Tokens: {total_tokens:,}")
            ch.console.print(f"  Size: {conv_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Conversation Memory: empty")

        ch.console.print()

        # Knowledge stats
        kb_db = Path(config.global_config_dir) / "memory" / "knowledge.db"
        if kb_db.exists():
            kb = KnowledgeBase(kb_db, embedding_provider=None)
            count = kb.count()
            kb.close()

            ch.info("Knowledge Base:")
            ch.console.print(f"  Entries: {count}")
            ch.console.print(f"  Size: {kb_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Knowledge Base: empty")

    except Exception as e:
        ch.error(f"Error: {e}")


# Memory Bank Commands (file-based knowledge with vector search)


@memory_app.command("bank")
def memory_bank_status(
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Show memory bank status and statistics.

    The memory bank is a file-based knowledge store at ~/.navig/memory/
    that supports hybrid search (vector + keyword).

    Examples:
        navig memory bank
        navig memory bank --plain
    """
    try:
        from navig.memory import get_memory_manager

        manager = get_memory_manager(
            use_embeddings=False
        )  # Don't load embeddings for status
        status = manager.get_status()

        if plain:
            print(f"directory={status['memory_directory']}")
            print(f"files={status['indexed_files']}")
            print(f"chunks={status['total_chunks']}")
            print(f"tokens={status['total_tokens']}")
            print(f"embedded={status['embedded_chunks']}")
            print(f"size_mb={status['database_size_mb']}")
            print(f"embeddings={status['embeddings_enabled']}")
        else:
            ch.info("Memory Bank Status")
            ch.console.print(f"  Directory: {status['memory_directory']}")
            ch.console.print(f"  Indexed files: {status['indexed_files']}")
            ch.console.print(f"  Total chunks: {status['total_chunks']}")
            ch.console.print(f"  Total tokens: {status['total_tokens']:,}")
            ch.console.print(f"  Embedded chunks: {status['embedded_chunks']}")
            ch.console.print(f"  Database size: {status['database_size_mb']} MB")

            if status["embeddings_enabled"]:
                ch.console.print(f"  Embedding model: {status['embedding_model']}")
            else:
                ch.console.print("  Embeddings: [dim]disabled[/dim]")

        manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("index")
def memory_bank_index(
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-index even unchanged files"
    ),
    no_embed: bool = typer.Option(
        False, "--no-embed", help="Skip embedding generation"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show file-by-file progress"
    ),
):
    """Index files in the memory bank.

    Scans ~/.navig/memory/ for .md/.txt files and creates
    searchable chunks with vector embeddings.

    Examples:
        navig memory index
        navig memory index --force
        navig memory index --no-embed
    """
    try:
        from navig.memory import get_memory_manager

        def progress(file_path: str, status: str):
            if verbose:
                icon = (
                    "✓" if status == "indexed" else "→" if status == "skipped" else "✗"
                )
                ch.console.print(f"  {icon} {file_path}")

        ch.info("Indexing memory bank...")

        manager = get_memory_manager(use_embeddings=not no_embed)
        result = manager.index(
            force=force,
            embed=not no_embed,
            progress_callback=progress if verbose else None,
        )

        ch.success(
            f"Indexed {result.files_processed} files ({result.files_skipped} skipped)"
        )
        ch.console.print(f"  Created {result.chunks_created} chunks")
        ch.console.print(f"  Total tokens: {result.total_tokens:,}")
        ch.console.print(f"  Embedded: {result.chunks_embedded} chunks")
        ch.console.print(f"  Duration: {result.duration_seconds:.2f}s")

        if result.errors:
            ch.warning(f"Errors ({len(result.errors)}):")
            for err in result.errors[:5]:
                ch.console.print(f"  • {err}")

        manager.close()

    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("For embeddings, install: pip install sentence-transformers")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("search")
def memory_bank_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-l", help="Maximum results"),
    file: str = typer.Option(None, "--file", "-f", help="Filter by file pattern"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    keyword_only: bool = typer.Option(
        False, "--keyword", "-k", help="Keyword-only search (no embeddings)"
    ),
):
    """Search the memory bank with hybrid search.

    Uses 70% vector similarity + 30% BM25 keyword matching.
    Falls back to keyword-only if embeddings unavailable.

    Examples:
        navig memory search "docker networking"
        navig memory search "nginx config" --limit 10
        navig memory search "deploy" --file "*.md"
        navig memory search "docker" --keyword
    """
    import json as json_module

    try:
        from navig.memory import get_memory_manager

        # Try with embeddings first, fall back to keyword-only
        use_embeddings = not keyword_only
        manager = None

        try:
            manager = get_memory_manager(use_embeddings=use_embeddings)
            response = manager.search(query, limit=limit, file_filter=file)
        except ImportError:
            # Embeddings not available, fall back to keyword-only
            if not keyword_only and not plain:
                ch.warning("Embeddings unavailable, using keyword-only search")
                ch.info("For semantic search: pip install sentence-transformers numpy")
            manager = get_memory_manager(use_embeddings=False)
            # Use keyword-only search via search engine (proper normalization)
            response = manager.search_engine.search(
                query, limit=limit, file_filter=file, keyword_only=True
            )

        if json_output:
            print(json_module.dumps(response.to_dict(), indent=2))
            if manager:
                manager.close()
            return

        if not response.results:
            if plain:
                print("No results")
            else:
                ch.info("No matching results found")
            if manager:
                manager.close()
            return

        if plain:
            for r in response.results:
                print(
                    f"{r.combined_score:.3f}\t{r.file_path}:{r.line_start}\t{r.snippet[:80]}"
                )
        else:
            ch.info(
                f"Found {len(response.results)} results ({response.search_time_ms:.1f}ms)"
            )
            ch.console.print()

            for i, r in enumerate(response.results, 1):
                score_bar = "█" * int(r.combined_score * 10)
                ch.console.print(
                    f"[bold cyan]{i}.[/bold cyan] [dim]{r.citation()}[/dim]"
                )
                ch.console.print(
                    f"   Score: [green]{score_bar}[/green] {r.combined_score:.3f}"
                )
                ch.console.print(f"   {r.snippet}")
                ch.console.print()

        if manager:
            manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("files")
def memory_bank_files(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """List indexed files in the memory bank."""
    try:
        from navig.memory import get_memory_manager

        manager = get_memory_manager(use_embeddings=False)
        files = manager.list_files()

        if not files:
            if plain:
                print("No files")
            else:
                ch.info("No files indexed yet")
                ch.info(f"Add .md files to: {manager.memory_dir}")
            manager.close()
            return

        if plain:
            for f in files:
                print(f"{f['file_path']}\t{f['chunk_count']}\t{f['total_tokens']}")
        else:
            from rich.table import Table

            table = Table(title="Indexed Memory Files")
            table.add_column("File", style="cyan")
            table.add_column("Chunks", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Indexed", style="dim")

            for f in files:
                indexed_at = f["indexed_at"][:10] if f.get("indexed_at") else "-"
                table.add_row(
                    f["file_path"],
                    str(f["chunk_count"]),
                    str(f["total_tokens"]),
                    indexed_at,
                )

            ch.console.print(table)

        manager.close()

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear-bank")
def memory_bank_clear(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear the memory bank index (keeps original files).

    This clears the search index but preserves the original
    .md files in ~/.navig/memory/

    Examples:
        navig memory clear-bank
        navig memory clear-bank --force
    """
    try:
        from navig.memory import get_memory_manager

        if not force:
            if not typer.confirm("Clear memory bank index? (files are preserved)"):
                raise typer.Abort()

        manager = get_memory_manager(use_embeddings=False)
        result = manager.clear(confirm=True)

        ch.success("Memory bank index cleared")
        ch.console.print(f"  Files removed: {result.get('files_deleted', 0)}")
        ch.console.print(f"  Chunks removed: {result.get('chunks_deleted', 0)}")
        ch.console.print(f"  Cache cleared: {result.get('cache_cleared', 0)}")

        manager.close()

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


# ── Key Facts (Conversational Memory) Commands ────────────────


@memory_app.command("facts")
def memory_facts_list(
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max facts to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List stored key facts (what NAVIG remembers about you).

    Shows persistent memories extracted from conversations:
    preferences, decisions, identity, technical context.

    Examples:
        navig memory facts
        navig memory facts --category preference
        navig memory facts --json
    """
    import json as json_module

    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()
        facts = store.get_active(limit=limit, category=category)

        if not facts:
            if plain:
                print("No facts stored")
            else:
                ch.info("No key facts stored yet.")
                ch.info("Facts are automatically extracted from conversations.")
            return

        if json_output:
            print(
                json_module.dumps([f.to_dict() for f in facts], indent=2, default=str)
            )
            return

        if plain:
            for f in facts:
                tags = ",".join(f.tags[:3]) if f.tags else ""
                print(
                    f"{f.id[:8]}\t{f.category}\t{f.confidence:.2f}\t{tags}\t{f.content[:80]}"
                )
        else:
            from rich.table import Table

            table = Table(title=f"Key Facts ({len(facts)} active)")
            table.add_column("ID", style="dim", max_width=8)
            table.add_column("Category", style="cyan")
            table.add_column("Confidence", justify="right")
            table.add_column("Content", max_width=60)
            table.add_column("Tags", style="dim", max_width=20)

            for f in facts:
                conf_color = (
                    "green"
                    if f.confidence >= 0.8
                    else "yellow" if f.confidence >= 0.6 else "red"
                )
                table.add_row(
                    f.id[:8],
                    f.category,
                    f"[{conf_color}]{f.confidence:.2f}[/{conf_color}]",
                    f.content[:60],
                    ", ".join(f.tags[:3]),
                )
            ch.console.print(table)

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("remember")
def memory_remember(
    content: str = typer.Argument(..., help="Fact to remember"),
    category: str = typer.Option(
        "context",
        "--category",
        "-c",
        help="preference|decision|identity|technical|context",
    ),
    tags: str = typer.Option("", "--tags", "-t", help="Comma-separated tags"),
):
    """Manually add a key fact to memory.

    Examples:
        navig memory remember "User prefers dark mode" --category preference
        navig memory remember "Deploy target is AWS eu-west-1" --category technical --tags aws,deploy
    """
    try:
        from navig.memory.key_facts import VALID_CATEGORIES, KeyFact, get_key_fact_store

        cat = category.lower().strip()
        if cat not in VALID_CATEGORIES:
            ch.warning(f"Unknown category '{cat}', using 'context'")
            cat = "context"

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        fact = KeyFact(
            content=content,
            category=cat,
            tags=tag_list,
            confidence=1.0,  # Manually added = full confidence
            source_platform="cli",
        )

        store = get_key_fact_store()
        result = store.upsert(fact)
        ch.success(f"Remembered: {result.content}")
        ch.console.print(
            f"  ID: [dim]{result.id[:8]}[/dim]  Category: [cyan]{result.category}[/cyan]"
        )

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("forget")
def memory_forget(
    fact_id: str = typer.Argument(None, help="Fact ID (prefix) to forget"),
    query: str = typer.Option(
        None, "--query", "-q", help="Search and forget matching facts"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a key fact from memory (soft-delete).

    Examples:
        navig memory forget abc12345
        navig memory forget --query "dark mode"
    """
    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()

        if query:
            results = store.search_keyword(query, limit=10)
            if not results:
                ch.info("No matching facts found")
                return

            for fact, _ in results:
                ch.console.print(f"  [{fact.id[:8]}] {fact.content}")

            if not force and not typer.confirm(f"Forget {len(results)} fact(s)?"):
                ch.info("Cancelled")
                return

            for fact, _ in results:
                store.soft_delete(fact.id)
            ch.success(f"Forgot {len(results)} fact(s)")

        elif fact_id:
            # Match by prefix
            facts = store.get_active(limit=500)
            matches = [f for f in facts if f.id.startswith(fact_id)]

            if not matches:
                ch.error(f"No fact found matching '{fact_id}'")
                return

            for f in matches:
                if not force:
                    ch.console.print(f"  {f.content}")
                    if not typer.confirm("Forget this fact?"):
                        continue
                store.soft_delete(f.id)
                ch.success(f"Forgot: {f.content[:60]}")
        else:
            ch.error("Provide a fact ID or --query")

    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("fact-stats")
def memory_fact_stats(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show key facts memory statistics.

    Examples:
        navig memory fact-stats
        navig memory fact-stats --json
    """
    import json as json_module

    try:
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()
        stats = store.get_stats()

        if json_output:
            print(json_module.dumps(stats, indent=2, default=str))
            return

        ch.info("Key Facts Memory Statistics:")
        ch.console.print(f"  Total facts:      {stats['total']}")
        ch.console.print(f"  Active:           [green]{stats['active']}[/green]")
        ch.console.print(f"  Deleted:          [red]{stats['deleted']}[/red]")
        ch.console.print(f"  Superseded:       [yellow]{stats['superseded']}[/yellow]")
        ch.console.print(f"  DB path:          [dim]{stats['db_path']}[/dim]")

        if stats.get("by_category"):
            ch.console.print("\n  By category:")
            for cat, count in stats["by_category"].items():
                ch.console.print(f"    {cat}: {count}")

    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("sync")
def memory_sync(
    ctx: typer.Context,
    from_url: str = typer.Option(
        ..., "--from", help="Source gateway URL (e.g. http://10.0.0.5:7422)."
    ),
    formation: str = typer.Option("", "--formation", "-f", help="Formation ID filter."),
    limit: int = typer.Option(
        500, "--limit", "-n", min=1, max=5000, help="Max chunks to pull."
    ),
    token: str = typer.Option(
        "", "--token", "-t", help="Bearer token for remote gateway."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported without writing."
    ),
) -> None:
    """
    Pull memory chunks from a remote NAVIG formation.

    Connects to a remote NAVIG gateway, exports its memory chunks,
    and imports them into the local memory store.

    Uses HTTP transport — not mesh (mesh explicitly excludes memory sync).

    Examples:
        navig memory sync --from http://10.0.0.5:7422
        navig memory sync --from http://remote:7422 --formation myproject
        navig memory sync --from http://remote:7422 --token mytoken --dry-run
    """
    ch.info(f"Connecting to {from_url} …")

    try:
        import json as _json
        from urllib.request import Request, urlopen

        from navig.memory.sync import import_chunks

        params = f"limit={limit}"
        if formation:
            params += f"&formation_id={formation}"

        url = f"{from_url.rstrip('/')}/memory/sync/export?{params}"
        headers: dict = {"User-Agent": "navig-sync/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:  # noqa: S310
                payload = _json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            ch.error(f"Failed to connect to {from_url}: {exc}")
            raise typer.Exit(1) from exc

        chunks = (
            payload.get("chunks", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(chunks, list):
            ch.error("Remote returned unexpected format.")
            raise typer.Exit(1)

        ch.info(f"Received {len(chunks)} chunk(s) from remote.")

        if dry_run:
            ch.dim("  (dry-run: nothing written)")
            raise typer.Exit(0)

        from navig.config import get_config_manager

        cfg = get_config_manager()
        db_path = cfg.storage_dir / "memory" / "chunks.db"

        imported, skipped = import_chunks(db_path, chunks, formation)
        ch.success(f"Sync complete: {imported} imported, {skipped} skipped.")

    except (SystemExit, typer.Exit):
        raise
    except Exception as exc:
        ch.error(f"Sync failed: {exc}")
        raise typer.Exit(1) from exc


app.add_typer(memory_app, name="memory")


# ============================================================================
# INTERACTIVE MENU
# ============================================================================


@app.command("menu")
def menu_command(ctx: typer.Context):
    """
    Launch interactive menu interface.

    Navigate NAVIG using a terminal UI with arrow keys and keyboard shortcuts.
    Mr. Robot inspired theme with Rich formatting.

    Features:
    - Host and app management
    - Database operations
    - File transfers
    - System monitoring
    - Command history tracking

    Navigation:
    - Arrow keys or numbers to select menu items
    - Enter to confirm selection
    - ESC or 'q' to go back
    - '?' for help
    - Ctrl+C to exit

    Note: If experiencing freezes on Windows, questionary may need to be uninstalled.
    The menu will work fine with number-based selection only.
    """
    try:
        from navig.commands.interactive import launch_menu

        launch_menu(ctx.obj)
    except ImportError as e:
        ch.error(f"Failed to load interactive menu: {e}")
        ch.info("Ensure Rich is installed: pip install rich")
        sys.exit(1)
    except Exception as e:
        ch.error(f"Interactive menu error: {e}")
        sys.exit(1)


@app.command("interactive", hidden=True)
def interactive_command(ctx: typer.Context):
    """Alias for 'menu' command - launch interactive interface."""
    try:
        from navig.commands.interactive import launch_menu

        launch_menu(ctx.obj)
    except ImportError as e:
        ch.error(f"Failed to load interactive menu: {e}")
        ch.info("Ensure Rich is installed: pip install rich")
        sys.exit(1)
    except Exception as e:
        ch.error(f"Interactive menu error: {e}")
        sys.exit(1)


# ============================================================================
# CONFIG MANAGEMENT
# ============================================================================
# Extend the existing top-level `config_app` instead of registering a second
# Typer group with the same public command name.


@config_app.command("migrate")
def config_migrate(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview changes without saving"
    ),
):
    """
    Migrate configuration to the latest version.
    """
    import yaml

    from navig.config import get_config_manager
    from navig.core.migrations import migrate_config

    cm = get_config_manager()
    global_config_file = cm.global_config_dir / "config.yaml"

    if not global_config_file.exists():
        ch.error("No global configuration found.")
        raise typer.Exit(1)

    try:
        # Load raw config to avoid auto-migration on load
        with open(global_config_file, encoding="utf-8") as f:
            raw_config = yaml.safe_load(f) or {}

        migrated, modified = migrate_config(raw_config)

        if not modified:
            ch.success("Configuration is already up to date.")
            return

        if dry_run:
            ch.info("Dry run: Configuration would be updated.")
            ch.info(f"New version: {migrated.get('version')}")
        else:
            with open(global_config_file, "w", encoding="utf-8") as f:
                yaml.dump(migrated, f, default_flow_style=False, sort_keys=False)
            ch.success(f"Configuration migrated to version {migrated.get('version')}")

    except Exception as e:
        ch.error(f"Migration failed: {e}")
        raise typer.Exit(1) from e


@config_app.command("audit")
def config_audit(
    fix: bool = typer.Option(
        False, "--fix", help="Attempt to fix issues automatically"
    ),
):
    """
    Audit configuration for security and validity.
    """
    run_audit({"fix": fix})


@config_app.command("show")
def config_show(
    scope: str = typer.Argument("global", help="Scope: global or host name"),
):
    """Show configuration."""
    cm = _get_config_manager()

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
def config_get(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.default_provider)"),
):
    """
    Get a configuration value.
    """
    cm = _get_config_manager()
    config = cm._load_global_config()

    # Traverse nested keys
    keys = key.split(".")
    value = config

    try:
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                value = None
                break

        if value is None:
            ch.warning(f"Key '{key}' not found or is empty.")
        else:
            if isinstance(value, (dict, list)):
                ch.print_json(value)
            else:
                ch.console.print(str(value))

    except Exception as e:
        ch.error(f"Error retrieving key: {e}")


@config_app.command("set-raw", hidden=True)
def config_set_legacy(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.model_preference)"),
    value: str = typer.Argument(
        ..., help="Value to set (JSON/YAML format for complex types)"
    ),
):
    """
    Set a configuration value.
    """
    try:
        import yaml

        from navig.config import get_config_manager

        # Parse value - try JSON/YAML first, fallback to string
        try:
            parsed_value = yaml.safe_load(value)
        except Exception:
            parsed_value = value

        cm = get_config_manager()
        global_config_file = cm.global_config_dir / "config.yaml"

        if not global_config_file.exists():
            ch.error("No global configuration found.")
            raise typer.Exit(1)

        with open(global_config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # Update nested key
        keys = key.split(".")
        target = config

        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
            if not isinstance(target, dict):
                ch.error(f"Cannot set key '{key}' because '{k}' is not a dictionary.")
                raise typer.Exit(1)

        target[keys[-1]] = parsed_value

        # Save
        with open(global_config_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        ch.success(f"Updated '{key}' to: {parsed_value}")

    except Exception as e:
        ch.error(f"Error setting config: {e}")
        raise typer.Exit(1) from e


# ============================================================================
# CALENDAR INTEGRATION (Proactive Assistance)
# ============================================================================

calendar_app = typer.Typer(
    name="calendar",
    help="Calendar integration for proactive assistance.",
    no_args_is_help=True,
)
app.add_typer(calendar_app, name="calendar")


@calendar_app.command("list")
def calendar_list(
    hours: int = typer.Option(24, "--hours", "-h", help="Hours ahead to look"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List upcoming calendar events.

    Examples:
        navig calendar list
        navig calendar list --hours 48
    """
    import asyncio
    from datetime import datetime, timedelta

    try:
        from navig.agent.proactive import GoogleCalendar, MockCalendar

        # Try Google, fallback to Mock
        try:
            provider = GoogleCalendar()
            if not provider.service:
                ch.warning("Google Calendar not authenticated. Using mock data.")
                provider = MockCalendar()
        except Exception:
            provider = MockCalendar()

        now = datetime.now()
        events = asyncio.run(provider.list_events(now, now + timedelta(hours=hours)))

        if json_output:
            import json

            ch.raw_print(
                json.dumps(
                    [
                        {
                            "id": e.id,
                            "title": e.title,
                            "start": e.start.isoformat(),
                            "end": e.end.isoformat(),
                            "location": e.location,
                        }
                        for e in events
                    ],
                    indent=2,
                )
            )
            return

        if not events:
            ch.info(f"No events in the next {hours} hours.")
            return

        table = ch.Table(title=f"Upcoming Events ({hours}h)")
        table.add_column("Time", style="cyan")
        table.add_column("Title", style="yellow")
        table.add_column("Location", style="green")

        for event in events:
            table.add_row(
                event.start.strftime("%m/%d %H:%M"), event.title, event.location or "-"
            )

        ch.console.print(table)

    except Exception as e:
        ch.error(f"Error listing events: {e}")


@calendar_app.command("auth")
def calendar_auth():
    """
    Authenticate with Google Calendar.

    Requires a credentials.json file from Google Cloud Console.
    """
    try:
        from navig.agent.proactive.google_calendar import GoogleCalendar

        provider = GoogleCalendar()
        if provider.service:
            ch.success("Successfully authenticated with Google Calendar!")
        else:
            ch.error("Authentication failed. Check credentials.json file.")
    except ImportError:
        ch.error("Google API libraries not installed.")
        ch.dim("Run: pip install google-auth-oauthlib google-api-python-client")
    except Exception as e:
        ch.error(f"Authentication error: {e}")


# ============================================================================
# EMAIL INTEGRATION (Proactive Assistance)
# ============================================================================

email_app = typer.Typer(
    name="email",
    help="Email integration for proactive assistance.",
    no_args_is_help=True,
)
app.add_typer(email_app, name="email")


@email_app.command("list")
def email_list(
    limit: int = typer.Option(10, "--limit", "-n", help="Max messages to fetch"),
    provider: str = typer.Option(
        "gmail", "--provider", "-p", help="Provider: gmail, outlook, fastmail, imap"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List unread emails.

    Requires email credentials in config or environment.

    Examples:
        navig email list
        navig email list --limit 20 --provider outlook
    """
    import asyncio

    try:
        from navig.agent.proactive import MockEmail
        from navig.config import get_config_manager

        cm = get_config_manager()
        email_config = cm.global_config.get("email", {})

        email_addr = email_config.get("address") or os.environ.get(
            "NAVIG_EMAIL_ADDRESS"
        )
        password = None
        provider_key = (
            str(email_config.get("provider") or provider or "").strip().lower()
        )
        legacy_password = email_config.get("password")

        # AUDIT DECISION:
        # Is this the correct implementation? Yes — vault-first lookup prevents reading plaintext
        # secrets from config when a secure credential exists.
        # Does it break any existing callers? No — env vars and legacy config still work as fallback.
        # Is there a simpler alternative? Yes, but insecure; this keeps compatibility while hardening.
        try:
            if provider_key:
                from navig.vault import get_vault

                secret = get_vault().get_secret(
                    provider_key,
                    key="password",
                    caller="cli.email_list",
                )
                if secret:
                    password = secret.reveal()
        except Exception:
            # Vault failures should not block legacy/env fallback behavior.
            password = None

        if not password:
            password = os.environ.get("NAVIG_EMAIL_PASSWORD")
        if not password and legacy_password:
            password = legacy_password
            ch.warning(
                "Using legacy plaintext email password from config. "
                "Run 'navig email setup <provider>' to migrate to vault."
            )

        if not email_addr or not password:
            ch.warning("Email not configured. Using mock data.")
            ch.dim(
                "Set email.address in config and store credentials via 'navig email setup',"
            )
            ch.dim("or use env vars:")
            ch.dim("  NAVIG_EMAIL_ADDRESS, NAVIG_EMAIL_PASSWORD")
            email_provider = MockEmail()
        else:
            from navig.agent.proactive.imap_email import (
                FastmailProvider,
                GmailProvider,
                OutlookProvider,
            )

            providers_map = {
                "gmail": GmailProvider,
                "outlook": OutlookProvider,
                "fastmail": FastmailProvider,
            }

            if provider in providers_map:
                email_provider = providers_map[provider](email_addr, password)
            else:
                ch.error(f"Unknown provider: {provider}")
                return

        messages = asyncio.run(email_provider.list_unread(limit=limit))

        if json_output:
            import json

            ch.raw_print(
                json.dumps(
                    [
                        {
                            "id": m.id,
                            "subject": m.subject,
                            "sender": m.sender,
                            "snippet": m.snippet,
                            "received_at": m.received_at.isoformat(),
                        }
                        for m in messages
                    ],
                    indent=2,
                )
            )
            return

        if not messages:
            ch.info("No unread emails.")
            return

        table = ch.Table(title=f"Unread Emails ({len(messages)})")
        table.add_column("From", style="cyan", max_width=25)
        table.add_column("Subject", style="yellow")
        table.add_column("Preview", style="dim", max_width=40)

        for msg in messages:
            table.add_row(
                msg.sender[:25] if len(msg.sender) > 25 else msg.sender,
                msg.subject,
                msg.snippet[:40] + "..." if len(msg.snippet) > 40 else msg.snippet,
            )

        ch.console.print(table)

    except Exception as e:
        ch.error(f"Error listing emails: {e}")


@email_app.command("setup")
def email_setup(
    provider: str = typer.Argument(..., help="Provider: gmail, outlook, fastmail"),
):
    """
    Interactive email setup.

    Stores credentials securely in NAVIG config.
    """
    import getpass

    try:
        import yaml

        from navig.config import get_config_manager
        from navig.vault import get_vault

        provider = provider.strip().lower()

        ch.info(f"Setting up {provider} email...")

        email_addr = input("Email address: ").strip()

        if provider == "gmail":
            ch.dim("Gmail requires an App Password (not your regular password)")
            ch.dim("Generate at: https://myaccount.google.com/apppasswords")

        password = getpass.getpass("Password/App Password: ")

        # Test connection
        ch.info("Testing connection...")

        from navig.agent.proactive.imap_email import (
            FastmailProvider,
            GmailProvider,
            OutlookProvider,
        )

        providers_map = {
            "gmail": GmailProvider,
            "outlook": OutlookProvider,
            "fastmail": FastmailProvider,
        }

        if provider not in providers_map:
            ch.error(f"Unknown provider: {provider}")
            return

        import asyncio

        test_provider = providers_map[provider](email_addr, password)
        messages = asyncio.run(test_provider.list_unread(limit=1))

        ch.success(f"Connection successful! Found {len(messages)} unread message(s).")

        # AUDIT DECISION:
        # Is this the correct implementation? Yes — store password in vault, never plaintext config.
        # Does it break any existing callers? No — runtime still supports env and legacy fallback.
        # Is there a simpler alternative? Yes, but storing plaintext in YAML is unacceptable for prod.
        vault = get_vault()
        existing = vault.get(provider, caller="cli.email_setup")
        metadata = {"email": email_addr}
        if existing:
            vault.update(existing.id, data={"password": password}, metadata=metadata)
        else:
            vault.add(
                provider=provider,
                credential_type="email",
                data={"password": password},
                profile_id="default",
                label=f"{provider} ({email_addr})",
                metadata=metadata,
            )

        # Save non-secret provider settings to config
        cm = get_config_manager()
        config_file = cm.global_config_dir / "config.yaml"

        config = {}
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

        existing_email = config.get("email")
        if not isinstance(existing_email, dict):
            existing_email = {}

        existing_email.update(
            {
                "provider": provider,
                "address": email_addr,
            }
        )
        # Remove legacy plaintext secret if present.
        existing_email.pop("password", None)
        config["email"] = existing_email

        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False)

        ch.success("Email configuration saved (password stored in vault).")
        ch.dim(f"Stored in: {config_file}")

    except Exception as e:
        ch.error(f"Setup failed: {e}")


# ============================================================================
# PROACTIVE ASSISTANCE ENGINE
# ============================================================================

proactive_app = typer.Typer(
    name="proactive",
    help="Proactive assistance engine (calendar, email, alerts).",
    no_args_is_help=True,
)
app.add_typer(proactive_app, name="proactive")


@proactive_app.command("status")
def proactive_status():
    """Show proactive engine status."""
    try:
        from navig.agent.proactive import ProactiveEngine  # noqa: F401
        from navig.config import get_config_manager

        cm = get_config_manager()

        ch.console.print("\n[bold]Proactive Assistance Status[/bold]\n")

        # Calendar
        calendar_config = cm.global_config.get("calendar", {})
        if calendar_config.get("provider"):
            ch.console.print(
                f"  📅 Calendar: [green]{calendar_config.get('provider')}[/green]"
            )
        else:
            ch.console.print("  📅 Calendar: [dim]Not configured[/dim]")

        # Email
        email_config = cm.global_config.get("email", {})
        if email_config.get("address"):
            ch.console.print(
                f"  📧 Email: [green]{email_config.get('address')}[/green]"
            )
        else:
            ch.console.print("  📧 Email: [dim]Not configured[/dim]")

        # Engine status
        ch.console.print("\n  ⚙️  Engine: [yellow]Starts with gateway[/yellow]")
        ch.dim("\n  Run 'navig gateway start' to activate proactive assistance.")

    except Exception as e:
        ch.error(f"Error: {e}")


@proactive_app.command("test")
def proactive_test():
    """Run a test check for upcoming events/emails."""
    import asyncio

    try:
        from navig.agent.proactive.engine import get_proactive_engine

        ch.info("Running proactive check...")

        engine = get_proactive_engine()
        asyncio.run(engine.run_checks(None))

        ch.success("Proactive check complete!")

    except Exception as e:
        ch.error(f"Error: {e}")


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
# ============================================================================

# Commands whose sub-app needs an external module import.
_EXTERNAL_CMD_MAP = {
    # name          →  (module_path,               attr_name)
    # ── QUANTUM VELOCITY K4: bridge/farmore/copilot moved here from module-level ──
    "bridge": ("navig.commands.bridge", "bridge_app"),
    "farmore": ("navig.commands.farmore", "farmore_app"),
    "copilot": ("navig.commands.ask", "copilot_app"),
    "inbox": ("navig.commands.inbox", "inbox_app"),
    "sync": ("navig.commands.sync", "sync_app"),
    "agent": ("navig.commands.agent", "agent_app"),
    "service": ("navig.commands.service", "service_app"),
    "stack": ("navig.commands.stack", "stack_app"),
    "tray": ("navig.commands.tray", "tray_app"),
    "formation": ("navig.commands.formation", "formation_app"),
    "council": ("navig.commands.council", "council_app"),
    "auto": ("navig.commands.auto", "auto_app"),
    "evolve": ("navig.commands.evolution", "evolution_app"),
    "script": ("navig.commands.script", "script_app"),
    "calendar": ("navig.commands.calendar", "calendar_app"),
    "mode": ("navig.commands.mode", "mode_app"),
    "email": ("navig.commands.email", "email_app"),
    "voice": ("navig.commands.voice", "voice_app"),
    "crash": ("navig.commands.crash", "crash_app"),
    "telegram": ("navig.commands.telegram", "telegram_app"),
    "tg": ("navig.commands.telegram", "telegram_app"),
    "matrix": ("navig.commands.matrix", "matrix_app"),
    "mx": ("navig.commands.matrix", "matrix_app"),
    "store": ("navig.commands.store", "store_app"),
    "cred": ("navig.commands.vault", "cred_app"),
    # Operating-mode profiles (node / builder / operator / architect)
    "profile": ("navig.commands.profile", "profile_app"),
    # Credential profiles (vault round-trip selection) — was the original "profile" entry
    "cred-profile": ("navig.commands.vault", "profile_app"),
    "flux": ("navig.commands.flux", "flux_app"),
    "fx": ("navig.commands.flux", "flux_app"),
    "cortex": ("navig.commands.cortex", "cortex_app"),
    "desktop": ("navig.commands.desktop", "desktop_app"),
    "net": ("navig.commands.net", "net_app"),
    "host": ("navig.commands.host", "host_app"),
    "h": ("navig.commands.host", "host_app"),
    "app": ("navig.commands.app", "app_app"),
    "a": ("navig.commands.app", "app_app"),
    "file": ("navig.commands.files", "file_app"),
    "f": ("navig.commands.files", "file_app"),
    "log": ("navig.commands.log", "log_app"),
    "l": ("navig.commands.log", "log_app"),
    "server": ("navig.commands.server", "server_app"),
    "s": ("navig.commands.server", "server_app"),
    "db": ("navig.commands.db", "db_app"),
    "database": ("navig.commands.db", "db_app"),
    # ── Phase 2: Links database ───────────────────────────────────────────
    "links": ("navig.commands.links", "links_app"),
    # ── Phase 3: Knowledge graph ──────────────────────────────────────────
    "kg": ("navig.commands.kg", "kg_app"),
    "knowledge": ("navig.commands.kg", "kg_app"),
    # ── Phase 4: Webhooks ─────────────────────────────────────────────────
    "webhook": ("navig.commands.webhook", "webhook_app"),
    "webhooks": ("navig.commands.webhook", "webhook_app"),
    # ── Phase 3: Go cron daemon CLI ───────────────────────────────────────
    # (navig.commands.cron is the existing gateway-based CLI;
    #  navig.commands.cron_local targets the new Go YAML daemon directly)
    "cron": ("navig.commands.cron", "cron_app"),
    # ── P1-15: Self-diagnostics ───────────────────────────────────────────────
    "doctor": ("navig.commands.doctor", "doctor_app"),
    # ── QUANTUM VELOCITY A: docker lazy dispatch ──────────────────────────────
    # Moved from 175-line inline block → navig/commands/docker.py :: docker_app
    # Saves parsing Typer decorators on every non-docker cold start.
    "docker": ("navig.commands.docker", "docker_app"),
    # ── Prompts: agent system-prompt management (.navig/store/prompts/) ───────
    "prompts": ("navig.commands.prompts", "prompts_app"),
    # ── Browser: Playwright/gateway web automation ────────────────────────────
    # Extracted from inline definition → navig/commands/browser.py :: browser_app
    "browser": ("navig.commands.browser", "browser_app"),
    # ── Multi-network reliable dispatch (Phase 0/1/2) ─────────────────────────
    "dispatch": ("navig.commands.dispatch", "dispatch_app"),
    "contacts": ("navig.commands.dispatch", "contacts_app"),
    "ct": ("navig.commands.dispatch", "contacts_app"),
    # ── System paths inspection & MCP server registration ──────────────────────
    "paths": ("navig.commands.paths_cmd", "paths_app"),
    "mcp": ("navig.commands.mcp_cmd", "mcp_app"),
    # ── Generic mention & keyword tracker ─────────────────────────────────────
    "radar": ("navig.commands.radar", "radar_app"),
    # ── Unified event observation system ──────────────────────────────────────
    "watch": ("navig.commands.watch_cmd", "watch_app"),
    # ── Flux Mesh: peer management, config sync, remote upgrade ───────────────
    "mesh": ("navig.commands.mesh", "mesh_app"),
    # ── Debug / observability: toggle debug mode, show log sizes ─────────────
    "debug": ("navig.commands.debug_cmd", "debug_app"),
    # ── Memory: conversations, key-facts, sessions ────────────────────────────
    "memory": ("navig.commands.memory", "memory_app"),
    # ── Spaces context: personal / workspace / studio mode switcher ────────────
    "spaces": ("navig.commands.spaces", "spaces_context_app"),
    # ── PERF: commands migrated from main.py unconditional try/except blocks ─
    # Were imported on EVERY CLI invocation; now dispatched lazily via this map.
    "telemetry": ("navig.commands.telemetry", "telemetry_app"),
    "wut": ("navig.commands.wut", "app"),
    "eval": ("navig.commands.eval", "app"),
    "agents": ("navig.commands.agents", "app"),
    "webdash": ("navig.commands.webdash", "app"),
    "explain": ("navig.commands.explain", "app"),
    "snapshot": ("navig.commands.snapshot", "app"),
    "replay": ("navig.commands.replay", "app"),
    "cloud": ("navig.commands.cloud", "app"),
    "benchmark": ("navig.commands.benchmark", "app"),
    # ── Finance: beancount double-entry accounting (pip install navig[finance]) ──
    "finance": ("navig.commands.finance", "finance_app"),
    # ── Work: lifecycle/stage tracker for leads, projects, tasks, etc. ────────
    "work": ("navig.commands.work", "work_app"),
    # ── Formerly eager-loaded inline — now lazy via this map ─────────────────
    "origin": ("navig.commands.origin", "origin_app"),
    "user": ("navig.commands.user", "user_app"),
    "node": ("navig.commands.node", "node_app"),
    "boot": ("navig.commands.boot_cmd", "boot_app"),
    "space": ("navig.commands.space", "space_app"),
    "blueprint": ("navig.commands.blueprint", "blueprint_app"),
    "deck": ("navig.commands.deck", "deck_app"),
    "portable": ("navig.commands.portable", "portable_app"),
    "migrate": ("navig.commands.migrate", "migrate_app"),
    "system": ("navig.commands.system_cmd", "system_app"),
    # ── Mount: NTFS junction registry + PowerShell helper generation ──────────
    "mount": ("navig.commands.mount", "mount_app"),
}


def _register_external_commands(*, register_all: bool = False):
    """Register external command sub-apps.

    Called once from main.py after fast-path check.  Uses ``sys.argv``
    to decide *which* commands need importing:

    * If argv[1] is recognised as an inline command (defined in this
      file), **no external modules are imported at all**.
    * If argv[1] is an external command, only *that* module is loaded.
    * If we cannot decide (e.g. ``navig --help`` fell through), we
      import everything so the help screen is complete.

    Args:
        register_all: If True, skip argv heuristic and register every
                      external command.  Useful for tests and tooling.
    """
    import importlib

    if register_all:
        target = None  # triggers fallback path below
    else:
        target = sys.argv[1] if len(sys.argv) > 1 else None

    # ------------------------------------------------------------------
    # Fast path: target is a known external command → import only it
    # ------------------------------------------------------------------
    if target in _EXTERNAL_CMD_MAP:
        mod_path, attr = _EXTERNAL_CMD_MAP[target]
        try:
            mod = importlib.import_module(mod_path)
            app.add_typer(
                getattr(mod, attr),
                name=target,
                hidden=(
                    target in ("tg", "mx", "fx", "h", "a", "f", "l", "s", "database")
                ),
            )
        except Exception as _ie:
            import sys as _sys

            _sys.stderr.write(
                f"[navig] \u26a0 command '{target}' unavailable (registration failed: {_ie})\n"
            )
        return

    # AHK sub-app (Windows only)
    if target == "ahk" and sys.platform == "win32":
        try:
            from navig.commands.ahk import ahk_app

            app.add_typer(ahk_app, name="ahk")
        except ImportError:
            pass  # optional dependency not installed; feature disabled
        return

    # ------------------------------------------------------------------
    # If target is an inline command (or a flag like --debug), skip
    # external imports entirely for maximum startup speed.
    # ------------------------------------------------------------------
    if target is not None and not target.startswith("-"):
        # Likely an inline command – no external imports needed.
        return

    # ------------------------------------------------------------------
    # Fallback: register everything (e.g. bare ``navig`` with no args)
    # ------------------------------------------------------------------
    for cmd_name, (mod_path, attr) in _EXTERNAL_CMD_MAP.items():
        try:
            mod = importlib.import_module(mod_path)
            app.add_typer(
                getattr(mod, attr),
                name=cmd_name,
                hidden=(
                    cmd_name in ("tg", "mx", "fx", "h", "a", "f", "l", "s", "database")
                ),
            )
        except Exception as _ie:
            import sys as _sys

            _sys.stderr.write(
                f"[navig] \u26a0 command '{cmd_name}' unavailable (registration failed: {_ie})\n"
            )

    if sys.platform == "win32":
        try:
            from navig.commands.ahk import ahk_app

            app.add_typer(ahk_app, name="ahk")
        except ImportError:
            pass  # optional dependency not installed; feature disabled


# Legacy aliases for backward compatibility
@app.command("sql", hidden=True)
def sql_query(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute"),
):
    """[DEPRECATED: Use 'navig db query'] Execute SQL query through tunnel."""
    ch.warning("'navig sql' is deprecated. Use 'navig db query' instead.")
    from navig.commands.db import db_query_cmd

    db_query_cmd(query, None, "root", None, None, None, ctx.obj)


@app.command("sqlfile", hidden=True)
def sql_file(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="SQL file to execute"),
):
    """[DEPRECATED: Use 'navig db file'] Execute SQL file through tunnel."""
    ch.warning("'navig sqlfile' is deprecated. Use 'navig db file' instead.")
    from navig.commands.database import execute_sql_file

    execute_sql_file(file, ctx.obj)


@app.command("restore", hidden=True)
def restore_db(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to restore from"),
):
    """[DEPRECATED: Use 'navig db restore'] Restore database from backup file."""
    ch.warning("'navig restore' is deprecated. Use 'navig db restore' instead.")
    from navig.commands.database import restore_database

    restore_database(file, ctx.obj)


@app.command("backup-config", hidden=True)
def backup_system_config_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup system configuration files. Use: navig backup run --config"""
    deprecation_warning("navig backup-config", "navig backup run --config")
    from navig.commands.backup import backup_system_config

    backup_system_config(name, ctx.obj)


@app.command("backup-db-all", hidden=True)
def backup_all_databases_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option(
        "gzip", "--compress", "-c", help="Compression: none|gzip|zstd"
    ),
):
    """[DEPRECATED] Backup all databases with compression. Use: navig backup run --db-all"""
    deprecation_warning("navig backup-db-all", "navig backup run --db-all")
    from navig.commands.backup import backup_all_databases

    backup_all_databases(name, compress, ctx.obj)


@app.command("backup-hestia", hidden=True)
def backup_hestia_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup HestiaCP configuration. Use: navig backup run --hestia"""
    deprecation_warning("navig backup-hestia", "navig backup run --hestia")
    from navig.commands.backup import backup_hestia

    backup_hestia(name, ctx.obj)


@app.command("backup-web", hidden=True)
def backup_web_config_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup web server configurations. Use: navig backup run --web"""
    deprecation_warning("navig backup-web", "navig backup run --web")
    from navig.commands.backup import backup_web_config

    backup_web_config(name, ctx.obj)


@app.command("backup-all", hidden=True)
def backup_all_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option(
        "gzip", "--compress", "-c", help="Compression for databases: none|gzip|zstd"
    ),
):
    """[DEPRECATED] Comprehensive backup. Use: navig backup run --all"""
    deprecation_warning("navig backup-all", "navig backup run --all")
    from navig.commands.backup import backup_all

    backup_all(name, compress, ctx.obj)


@app.command("list-backups", hidden=True)
def list_backups_cmd(ctx: typer.Context):
    """[DEPRECATED] List all available backups. Use: navig backup list"""
    deprecation_warning("navig list-backups", "navig backup list")
    from navig.commands.backup import list_backups_cmd as list_backups

    list_backups(ctx.obj)


@app.command("restore-backup", hidden=True)
def restore_backup_cmd(
    ctx: typer.Context,
    backup_name: str = typer.Argument(..., help="Backup name to restore from"),
    component: str | None = typer.Option(
        None, "--component", "-c", help="Specific component to restore"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """[DEPRECATED] Restore from comprehensive backup. Use: navig backup restore"""
    deprecation_warning("navig restore-backup", "navig backup restore")
    ctx.obj["force"] = force
    from navig.commands.backup import restore_backup_cmd as restore_backup

    restore_backup(backup_name, component, ctx.obj)


# ============================================================================
# MONITORING & HEALTH CHECKS (Unified 'monitor' group)
# ============================================================================

# ============================================================================
# SELF-UPDATE
# ============================================================================


@app.command("update")
def update_cmd(
    check: bool = typer.Option(
        False, "--check", "-c", help="Check for updates only — do not apply."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Apply even if already on latest."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would happen, no changes."
    ),
    channel: str | None = typer.Option(
        None, "--channel", help="Update channel: stable or beta.", hidden=True
    ),
) -> None:
    """Check for and apply NAVIG self-updates.

    Preserves all user data across every update and runs pending
    data-root migrations automatically after upgrading.
    """
    from navig.commands.update import _run_update  # noqa: PLC0415

    _run_update(check=check, force=force, dry_run=dry_run)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    _register_external_commands()
    app()
