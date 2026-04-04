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
        ch.dim(f"Python {sys.version.split()[0]} on {platform.system()} {platform.release()}")
        # Show a random quote
        import random

        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f"💬 {quote} - {author}")


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
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from rich.console import Console as _RC

    _con = _RC()
    src_dir = Path(__file__).resolve().parent.parent.parent  # navig/cli/__init__.py → navig-core/
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
                _con.print(f"[green]✓[/green] NAVIG v{__version__}  [dim]{commit}[/dim]")
                _con.print("[dim]Run [bold]navig upgrade[/bold] to pull latest commits.[/dim]")
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
                _con.print(f"[red]✗[/red] Upgrade failed:\n[dim]{r.stderr.strip()[:400]}[/dim]")
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
                Path(__file__).resolve().parent.parent.parent / ".venv" / "Scripts" / "navig.exe"
            )
            _path_navig = shutil.which("navig")
            _path_exe = Path(_path_navig) if _path_navig else None
            if _venv_exe.exists() and _path_exe and _path_exe.exists() and _venv_exe != _path_exe:
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
    from rich.table import Table

    from navig.cli.registry import get_schema as _get_schema

    # --schema: emit the canonical command registry and exit
    if schema_out:
        from navig.cli.registry import get_schema as _get_schema

        typer.echo(jsonlib.dumps(_get_schema(), indent=2))
        raise typer.Exit()

    console = Console()
    # Help markdown files live at navig/help/, one level above navig/cli/
    help_dir = Path(__file__).resolve().parent.parent / "help"
    schema = _get_schema()
    schema_commands = [
        row
        for row in schema.get("commands", [])
        if isinstance(row, dict) and str(row.get("path", "")).strip().startswith("navig ")
    ]
    manifest_topics = sorted(
        {
            str(row.get("path", "")).split()[1]
            for row in schema_commands
            if len(str(row.get("path", "")).split()) >= 2
        }
    )

    md_topics = []
    if help_dir.exists():
        md_topics = sorted(
            {
                p.stem
                for p in help_dir.glob("*.md")
                if p.is_file() and p.stem.lower() not in {"readme"}
            }
        )

    registry_topics = sorted(set(HELP_REGISTRY.keys()) | set(manifest_topics))
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

    # Fall back to generated registry topic index.
    if normalized in manifest_topics:
        topic_commands = [
            row
            for row in schema_commands
            if str(row.get("path", "")).startswith(f"navig {normalized}")
        ]

        if want_json:
            payload_commands = []
            for row in topic_commands:
                path = str(row.get("path", "")).strip()
                prefix = f"navig {normalized}"
                cmd_name = path[len(prefix) :].strip() if path.startswith(prefix) else path
                payload_commands.append(
                    {
                        "name": cmd_name or normalized,
                        "path": path,
                        "description": row.get("summary", ""),
                        "status": row.get("status", "stable"),
                        "since": row.get("since", ""),
                    }
                )

            typer.echo(
                jsonlib.dumps(
                    {
                        "topic": normalized,
                        "desc": HELP_REGISTRY.get(normalized, {}).get("desc", ""),
                        "commands": payload_commands,
                        "source": "registry",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        if want_plain:
            console.print(f"navig {normalized}")
            for row in topic_commands:
                path = str(row.get("path", "")).strip()
                summary = str(row.get("summary", "")).strip()
                console.print(f"  {path} - {summary}")
            raise typer.Exit()

        table = Table(box=None, show_header=False, padding=(0, 2), collapse_padding=True)
        table.add_column("Command", style="cyan")
        table.add_column("Description", style="dim")
        for row in topic_commands:
            path = str(row.get("path", "")).strip()
            summary = str(row.get("summary", "")).strip()
            if row.get("status") == "deprecated" and isinstance(row.get("deprecated"), dict):
                replacement = row["deprecated"].get("replaced_by")
                if replacement:
                    summary = f"{summary} (deprecated -> {replacement})"
            table.add_row(path, summary)

        console.print()
        console.print(f"[bold cyan]navig {normalized}[/bold cyan]")
        console.print("[dim]Generated command registry[/dim]")
        console.print(table)
        raise typer.Exit()

    # Final fallback to legacy centralized help registry.
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
                console.print(f"  [cyan]*[/cyan] [yellow]{item['file']}[/yellow]: {title.strip()}")
            console.print("\n[dim]Use 'navig docs <query>' to search documentation.[/dim]")
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
                    console.print(f"[bold white]{i}. {r.get('title', 'Untitled')}[/bold white]")
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
    import json as jsonlib

    from rich.console import Console

    console = Console()
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")

    try:
        from navig.tools.web import web_search

        (console.print(f"[dim]Searching for '{query}'...[/dim]") if not want_json else None)

        result = web_search(
            query=query,
            count=limit,
            provider=provider,
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
            console.print("\n[dim]Tip: Set up Brave Search API for better results:[/dim]")
            console.print("[dim]  1. Get key from https://brave.com/search/api/[/dim]")
            console.print("[dim]  2. navig config set web.search.api_key=YOUR_KEY[/dim]")
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
    # ── Installer pipeline (non-interactive) ─────────────────────────────────
    if profile:
        from navig.installer import run_install
        from navig.installer.profiles import VALID_PROFILES

        valid_profiles = set(VALID_PROFILES) | {"quickstart"}
        effective_profile = "operator" if profile == "quickstart" else profile

        if profile not in valid_profiles:
            import typer as _t

            _t.echo(
                f"Unknown profile '{profile}'. Valid: {', '.join(sorted(valid_profiles))}",
                err=True,
            )
            raise SystemExit(1)

        run_install(profile=effective_profile, dry_run=dry_run, quiet=quiet)
        if profile == "quickstart":
            from navig.commands.init import run_chat_first_handoff

            run_chat_first_handoff(profile=profile, dry_run=dry_run, quiet=quiet)
        return

    # ── Interactive onboarding (canonical engine flow + optional TUI) ───────
    import os
    import sys

    try:
        from navig.commands.init import _maybe_send_first_run_ping
    except (ImportError, AttributeError):
        def _maybe_send_first_run_ping() -> None:  # type: ignore[misc]
            pass
    from navig.commands.onboard import run_onboard
    from navig.onboarding.runner import run_engine_onboarding
    want_json = bool(getattr(ctx, "obj", {}) and ctx.obj.get("json"))

    if status:
        import json as _json
        from navig.commands.init import show_init_status

        payload = show_init_status(render=not want_json)
        if want_json:
            typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if settings:
        from navig.commands.status import show_status

        show_status({"all": True, "plain": False})
        return

    # UI mode selector (additive, backward-compatible):
    # - default: CLI engine flow (current stable behavior)
    # - opt-in: --tui or NAVIG_INIT_UI=tui
    # - force classic: NAVIG_INIT_UI=cli
    ui_mode = os.getenv("NAVIG_INIT_UI", "auto").strip().lower()
    use_tui = tui or ui_mode == "tui"
    if ui_mode == "cli":
        use_tui = False

    if use_tui:
        if not _init_tui_capable():
            from rich.console import Console as _C

            _C().print(
                "[yellow]TUI unavailable in this terminal; falling back to CLI onboarding.[/yellow]"
            )
        else:
            flow = "manual" if (reconfigure or provider) else "auto"
            try:
                run_onboard(flow=flow)
                try:
                    _maybe_send_first_run_ping()
                except Exception:  # noqa: BLE001
                    pass
                return
            except Exception:
                from rich.console import Console as _C

                _C().print(
                    "[yellow]TUI launch failed; falling back to CLI onboarding.[/yellow]"
                )

    jump_to_step = "ai-provider" if provider else None
    state = run_engine_onboarding(
        force=reconfigure,
        jump_to_step=jump_to_step,
        show_banner=True,
        respect_skip_env=False,
        skip_if_configured=True,
    )

    if state is None and not reconfigure and not provider:
        import json as _json
        from rich.console import Console as _C
        from navig.commands.init import show_init_status

        payload = show_init_status(render=not want_json)
        if want_json:
            payload = {**payload, "already_configured": True}
            typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _C().print(
                "[green]NAVIG is already configured.[/green] "
                "Use [bold]navig init --reconfigure[/bold] to run setup again."
            )
        return

    try:
        _maybe_send_first_run_ping()
    except Exception:  # noqa: BLE001
        pass


def _init_tui_capable() -> bool:
    """Return True when this terminal can run full-screen init TUI."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


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
        (a, r) for a, r in zip(actions, results) if a.reversible and r.state == ModuleState.APPLIED
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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Detailed output"),
    var: list[str] | None = typer.Option(None, "--var", "-V", help="Variable (name=value)"),
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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate; skip all writes"),
    now_date: str | None = typer.Option(None, "--date", "-d", help="Override date YYYY-MM-DD"),
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
    spec: str = typer.Argument(..., help="type:owner/repo[@ref]  e.g. skill:myuser/my-skill"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
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
    spec: str = typer.Argument(None, help="Specific asset to update (omit to update all)."),
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
                f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', '?'))}"
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
        install_spec = f"{asset.get('type', '?')}:{asset.get('repo', asset.get('name', ''))}"
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
                f"{asset.get('type', '?')}  {asset.get('name', '?')}  "
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Show command without executing"),
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
    limit: int | None = typer.Option(None, "--limit", "-l", help="Limit number of results"),
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
    resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed information"),
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
    domains: bool = typer.Option(False, "--domains", "-d", help="List HestiaCP domains"),
    user_filter: str | None = typer.Option(None, "--user", help="Filter domains by username"),
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
    password: str | None = typer.Option(None, "--password", "-p", help="Password (for user)"),
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
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
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
    json: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
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
    json: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
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
    json: bool = typer.Option(False, "--json", help="Output installation result as JSON"),
):
    """Install NAVIG YAML JSON Schemas for editor validation/autocomplete."""
    from navig.commands.config import install_schemas

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    install_schemas(scope=scope, write_vscode_settings=write_vscode_settings, options=opts)


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
    key: str = typer.Argument(..., help="Configuration key (e.g., 'log_level', 'execution.mode')"),
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
    set_var: list[str] | None = typer.Option(None, "--set", help="Set variable like key=value"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without creating files"),
):
    """Generate files/directories from a template."""
    from navig.commands.scaffold import apply

    apply(template_path, target_dir, host, set_var, dry_run)


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


# ── Shared gateway helpers (used by bot_app, heartbeat, cron, approval, tasks) ─
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
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
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

    # Check for telegram token (vault-first, env/config fallback)
    from navig.messaging.secrets import resolve_telegram_bot_token

    telegram_token = resolve_telegram_bot_token()
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
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
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
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
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

    patterns = r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"

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
            pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
            if pids:
                ch.success("Bot appears to be running")
                ch.info(f"  PIDs: {', '.join(pids)}")
            else:
                ch.warning("Bot does not appear to be running")
        else:
            result = subprocess.run(["pgrep", "-f", patterns], capture_output=True, text=True)
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

    patterns = r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"

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
                line.strip() for line in find_result.stdout.splitlines() if line.strip().isdigit()
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
            result = subprocess.run(["pkill", "-f", patterns], capture_output=True, text=True)
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
    import os
    import subprocess

    if bot:
        from navig.messaging.secrets import resolve_telegram_bot_token

        telegram_token = resolve_telegram_bot_token()
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
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
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
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
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
                        next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                        now = datetime.now(next_dt.tzinfo) if next_dt.tzinfo else datetime.now()
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
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable/disable heartbeat"),
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
# CRON — deferred to _register_external_commands
# ============================================================================
# Inline cron_app removed (~215 lines). Full implementation lives in
# navig.commands.cron (cron_app) and is registered via _EXTERNAL_CMD_MAP.


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
# MEMORY MANAGEMENT — extracted to navig/commands/memory.py (P1-14)
# ============================================================================


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
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without saving"),
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
    fix: bool = typer.Option(False, "--fix", help="Attempt to fix issues automatically"),
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
    value: str = typer.Argument(..., help="Value to set (JSON/YAML format for complex types)"),
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
