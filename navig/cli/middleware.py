"""
navig/cli/middleware.py

Main-callback middleware helpers extracted from __init__.py.

Each function handles one cross-cutting concern that runs on *every* CLI
invocation via the @app.callback (main) handler.  Keeping them here:
  • reduces main() in __init__.py to ~130 lines of glue code
  • makes each concern testable in isolation
  • keeps __init__.py from becoming a monolith again

Entry points (called from main() in __init__.py):
  init_operation_recorder(ctx, host, app, verbose)
  init_debug_logger(ctx, debug_log, host, app, verbose, quiet, dry_run)
  register_fact_extraction()
  init_proactive_assistant(ctx, quiet)
"""

from __future__ import annotations

import atexit
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import typer

_log = logging.getLogger(__name__)


# ============================================================================
# 1. OPERATION RECORDER
# ============================================================================

def init_operation_recorder(
    ctx: typer.Context,
    host: Optional[str],
    app: Optional[str],
    verbose: bool,
) -> None:
    """Start an operation record and register an atexit handler to complete it.

    Stores ``_operation_record``, ``_operation_start``, and
    ``_operation_recorder`` in ``ctx.obj`` if recording proceeds.

    Silently skips for meta commands (help, history, version, …).
    """
    # Lazy import so startup isn't slowed by DB/IO init on every invocation.
    ch = _lazy_ch()

    try:
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

    # Register atexit completion handler (safe to call even if no record was stored)
    if "_operation_record" in ctx.obj:
        _register_operation_complete_atexit(ctx)


def _register_operation_complete_atexit(ctx: typer.Context) -> None:
    """Register an atexit handler that completes the current operation record."""

    def record_operation_on_exit() -> None:
        def _do_record() -> None:
            try:
                record = ctx.obj.get("_operation_record")
                recorder = ctx.obj.get("_operation_recorder")
                start_time = ctx.obj.get("_operation_start", time.time())

                if record and recorder:
                    duration_ms = (time.time() - start_time) * 1000
                    recorder.complete_operation(
                        record=record,
                        success=True,
                        output="",
                        duration_ms=duration_ms,
                    )
            except Exception:
                pass  # Silent fail for recording

        # daemon=False so the write completes before process exits.
        # join(1.0) caps CLI exit delay to ≤1 second even on slow DB.
        t = threading.Thread(target=_do_record, daemon=False)
        t.start()
        t.join(timeout=1.0)

    atexit.register(record_operation_on_exit)


# ============================================================================
# 2. DEBUG LOGGER
# ============================================================================

def init_debug_logger(
    ctx: typer.Context,
    debug_log: bool,
    host: Optional[str],
    app: Optional[str],
    verbose: bool,
    quiet: bool,
    dry_run: bool,
) -> None:
    """Initialise the DebugLogger (or no-op) and store it in ``ctx.obj``.

    Reads the ``debug_log`` / ``debug_log_path`` / ``debug_log_max_size_mb``
    / ``debug_log_max_files`` / ``debug_log_truncate_output_kb`` keys from the
    global config YAML, using a fast-path that avoids a full config load if the
    `ConfigManager` has not been initialised yet.
    """
    ch = _lazy_ch()

    # Ensure the key is always present so subcommands can safely access it.
    ctx.obj["debug_logger"] = None

    # Performance: read debug settings from raw YAML without full config load.
    _debug_raw_cfg = None
    debug_log_enabled = debug_log
    if not debug_log_enabled:
        try:
            from navig.cli import _get_config_manager  # late import to avoid circular

            _cm = _get_config_manager()
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

    if not debug_log_enabled:
        return

    try:
        from navig.debug_logger import DebugLogger

        # Reuse raw YAML dict if available; fall back to full config only if needed
        try:
            from navig.cli import _get_config_manager as _gcm

            _cm = _gcm()
            _dgc = _debug_raw_cfg or (
                _cm.global_config if _cm._global_config_loaded else {}
            )
        except Exception:
            _dgc = _debug_raw_cfg or {}

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
        def log_command_end_on_exit() -> None:
            debug_logger.log_command_end(True)

        atexit.register(log_command_end_on_exit)

        if verbose:
            ch.dim(f"→ Debug logging enabled: {debug_logger.log_path}")

    except Exception as e:
        if verbose:
            ch.warning(f"Failed to initialize debug logger: {e}")


# ============================================================================
# 3. FACT EXTRACTION
# ============================================================================

def register_fact_extraction() -> None:
    """Register an atexit daemon thread that extracts CLI facts into memory.

    Silently skips meta commands (memory, kg, index, history, version, help).
    Never surfaces errors to the user.
    """
    _SKIP_FACT_CMDS = frozenset(["memory", "kg", "index", "history", "version", "help"])
    _invoked_cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if _invoked_cmd in _SKIP_FACT_CMDS or _invoked_cmd in ("", "--help", "--version"):
        return

    def _extract_facts_on_exit() -> None:
        def _do_extract() -> None:
            try:
                command_str = " ".join(sys.argv[1:])
                if not command_str.strip():
                    return
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

        threading.Thread(target=_do_extract, daemon=True).start()

    atexit.register(_extract_facts_on_exit)


# ============================================================================
# 4. PROACTIVE ASSISTANT
# ============================================================================

def init_proactive_assistant(ctx: typer.Context, quiet: bool) -> None:
    """Start ProactiveAssistant in a background daemon thread (non-blocking).

    Stores ``get_assistant`` (callable) and ``assistant_enabled`` (bool) in
    ``ctx.obj``.  The callable waits up to ``timeout`` seconds for the
    background thread to finish loading.

    Performance: skips entirely for scripting flags (--plain, --raw, --json, -q)
    to avoid loading AI dependencies on every automated invocation.
    """
    _skip_assistant = quiet or any(a in sys.argv for a in ("--plain", "--raw", "--json"))

    if _skip_assistant:
        ctx.obj["get_assistant"] = lambda timeout=0.5: None
        ctx.obj["assistant_enabled"] = False
        return

    _assistant_holder: dict = {"instance": None, "error": None}
    _assistant_ready = threading.Event()

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

    threading.Thread(target=_load_assistant_bg, daemon=True).start()

    def _get_assistant(timeout: float = 0.5):
        """Retrieve the ProactiveAssistant, waiting up to ``timeout`` seconds."""
        _assistant_ready.wait(timeout=timeout)
        return _assistant_holder.get("instance")

    ctx.obj["get_assistant"] = _get_assistant
    ctx.obj["assistant_enabled"] = True  # optimistic — set False if disabled


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

def _lazy_ch():
    """Return console_helper lazily (avoids rich.* import at module load time)."""
    from navig.lazy_loader import lazy_import
    return lazy_import("navig.console_helper")
