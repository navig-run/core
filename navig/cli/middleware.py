"""
navig/cli/middleware.py
=======================

Cross-cutting middleware helpers that run on **every** CLI invocation via the
``@app.callback`` (main) handler in ``cli/__init__.py``.

Each function handles exactly one concern, making them independently testable
and keeping ``__init__.py`` from growing into a monolith.

Entry points (called from main() in cli/__init__.py):
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

import typer

from navig.cli.registration import extract_non_global_tokens

_log = logging.getLogger(__name__)

# Meta-commands whose invocation should skip operation recording / fact
# extraction (avoids polluting history with internal bookkeeping calls).
_SKIP_RECORD_KEYWORDS: frozenset[str] = frozenset([
    "history ", "help", "--help", "-h", "--version", "-v",
    "insights ", "dashboard", "suggest",
    "trigger test", "trigger history",
])
_SKIP_FACT_CMDS: frozenset[str] = frozenset([
    "memory", "kg", "index", "history", "version", "help",
    "--help", "--version",
])


# =============================================================================
# 1. OPERATION RECORDER
# =============================================================================


def init_operation_recorder(
    ctx: typer.Context,
    host: str | None,
    app: str | None,
    verbose: bool,
) -> None:
    """Start an operation record and register an atexit handler to complete it.

    Stores ``_operation_record``, ``_operation_start``, and
    ``_operation_recorder`` in ``ctx.obj`` if recording proceeds.
    Silently skips meta-commands (help, history, version, …).
    """
    ch = _lazy_ch()

    try:
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()
        command_str = " ".join(sys.argv[1:])

        # Use non-global-stripped tokens to avoid matching short flags like
        # "-h" inside "--host" or "-v" inside "--verbose".
        non_global = extract_non_global_tokens(sys.argv[1:])
        cmd_str_for_skip = " ".join(non_global)

        op_type = _classify_operation_type(command_str)

        skip = any(kw in cmd_str_for_skip for kw in _SKIP_RECORD_KEYWORDS)
        if not skip and command_str.strip():
            record = recorder.start_operation(
                command=f"navig {command_str}",
                operation_type=op_type,
                host=host,
                app=app,
            )
            ctx.obj["_operation_record"] = record
            ctx.obj["_operation_start"] = time.time()
            ctx.obj["_operation_recorder"] = recorder

    except Exception as exc:
        if verbose:
            ch.dim(f"→ Operation recording skipped: {exc}")

    if "_operation_record" in ctx.obj:
        _register_operation_complete_atexit(ctx)


def _classify_operation_type(command_str: str):
    """Derive the OperationType from the raw command string."""
    from navig.operation_recorder import OperationType

    if any(kw in command_str for kw in ("exec ", "ssh ", "tunnel ")):
        return OperationType.REMOTE_COMMAND
    if any(kw in command_str for kw in ("db ", "database ")):
        return OperationType.DATABASE_QUERY
    if "upload" in command_str or "put" in command_str:
        return OperationType.FILE_UPLOAD
    if any(kw in command_str for kw in ("download ", "get ")):
        return OperationType.FILE_DOWNLOAD
    if any(kw in command_str for kw in ("docker ", "container ")):
        return OperationType.DOCKER_COMMAND
    if "workflow run" in command_str:
        return OperationType.WORKFLOW_RUN
    if "host use" in command_str or "host switch" in command_str:
        return OperationType.HOST_SWITCH
    if "service" in command_str:
        return OperationType.SERVICE_RESTART
    return OperationType.LOCAL_COMMAND


def _register_operation_complete_atexit(ctx: typer.Context) -> None:
    """Register an atexit handler that persists the current operation record."""

    def _on_exit() -> None:
        def _write() -> None:
            try:
                record = ctx.obj.get("_operation_record")
                recorder = ctx.obj.get("_operation_recorder")
                start_time = ctx.obj.get("_operation_start", time.time())

                if record and recorder:
                    duration_ms = (time.time() - start_time) * 1000
                    exc_type, exc_val, _ = sys.exc_info()
                    if exc_type is None:
                        success = True
                    elif issubclass(exc_type, SystemExit):
                        code = getattr(exc_val, "code", 0)
                        success = code in (0, None)
                    else:
                        success = False

                    recorder.complete_operation(
                        record=record,
                        success=success,
                        output="",
                        duration_ms=duration_ms,
                    )
            except Exception as exc:
                _log.debug("operation recorder write failed: %s", exc)

        # daemon=False so the write completes before the process exits.
        # join(1.0) caps exit delay to ≤1 s even on a slow DB.
        t = threading.Thread(target=_write, daemon=False)
        t.start()
        t.join(timeout=1.0)

    atexit.register(_on_exit)


# =============================================================================
# 2. DEBUG LOGGER
# =============================================================================


def init_debug_logger(
    ctx: typer.Context,
    debug_log: bool,
    host: str | None,
    app: str | None,
    verbose: bool,
    quiet: bool,
    dry_run: bool,
) -> None:
    """Initialise :class:`~navig.debug_logger.DebugLogger` and store it in ``ctx.obj``.

    Reads the ``debug_log`` / ``debug_log_path`` / ``debug_log_max_size_mb``
    / ``debug_log_max_files`` / ``debug_log_truncate_output_kb`` keys from the
    global config YAML via a fast-path that avoids a full :class:`ConfigManager`
    load when the manager has not been initialised yet.
    """
    ch = _lazy_ch()
    ctx.obj["debug_logger"] = None  # Always present so subcommands can read safely

    debug_log_enabled, raw_cfg = _resolve_debug_log_flag(debug_log)
    if not debug_log_enabled:
        return

    try:
        from navig.debug_logger import DebugLogger

        cfg = raw_cfg or {}
        log_path_str = cfg.get("debug_log_path")
        debug_logger = DebugLogger(
            log_path=Path(log_path_str) if log_path_str else None,
            max_size_mb=cfg.get("debug_log_max_size_mb", 10),
            max_files=cfg.get("debug_log_max_files", 5),
            truncate_output_kb=cfg.get("debug_log_truncate_output_kb", 10),
        )
        ctx.obj["debug_logger"] = debug_logger

        debug_logger.log_command_start(
            " ".join(sys.argv),
            {
                "host": host,
                "app": app,
                "verbose": verbose,
                "quiet": quiet,
                "dry_run": dry_run,
            },
        )

        atexit.register(lambda: debug_logger.log_command_end(success=True))

        if verbose:
            ch.dim(f"→ Debug logging enabled: {debug_logger.log_path}")

    except Exception as exc:
        if verbose:
            ch.warning(f"Failed to initialise debug logger: {exc}")


def _resolve_debug_log_flag(flag: bool) -> tuple[bool, dict | None]:
    """Return ``(enabled, raw_config_dict)`` without triggering a full config load."""
    if flag:
        return True, None

    try:
        from navig.cli import _get_config_manager

        cm = _get_config_manager()
        if cm._global_config_loaded:
            return bool(cm.global_config.get("debug_log", False)), cm.global_config
        # Fast path: read only the YAML file directly.
        import yaml

        gc_file = cm.global_config_dir / "config.yaml"
        if gc_file.exists():
            raw = yaml.safe_load(gc_file.read_text(encoding="utf-8")) or {}
            return bool(raw.get("debug_log", False)), raw
    except Exception:
        pass

    return False, None


# =============================================================================
# 3. FACT EXTRACTION
# =============================================================================


def register_fact_extraction() -> None:
    """Register an atexit daemon thread that extracts CLI facts into memory.

    Skips meta-commands (memory, kg, index, history, version, help).
    Never surfaces errors to the user.
    """
    non_global = extract_non_global_tokens(sys.argv[1:])
    first_cmd = non_global[0] if non_global else ""

    if first_cmd in _SKIP_FACT_CMDS or not first_cmd:
        return

    def _on_exit() -> None:
        def _extract() -> None:
            try:
                command_str = " ".join(sys.argv[1:]).strip()
                if not command_str:
                    return
                # Re-check skip using the normalised token list (guards against
                # prefixed global flags hiding the actual command).
                tokens = extract_non_global_tokens(sys.argv[1:])
                if (tokens[0] if tokens else "") in _SKIP_FACT_CMDS:
                    return

                from navig.memory.manager import get_memory_manager

                mgr = get_memory_manager()
                if hasattr(mgr, "record_command"):
                    mgr.record_command(command_str)
                elif hasattr(mgr, "fact_extractor") and mgr.fact_extractor:
                    result = mgr.fact_extractor.extract_from_text(
                        f"User ran: {command_str}", source="cli"
                    )
                    if result and hasattr(mgr, "store_facts"):
                        mgr.store_facts(result)
            except Exception as exc:
                _log.debug("fact extraction failed: %s", exc)

        threading.Thread(target=_extract, daemon=True).start()

    atexit.register(_on_exit)


# =============================================================================
# 4. PROACTIVE ASSISTANT
# =============================================================================


def init_proactive_assistant(ctx: typer.Context, quiet: bool) -> None:
    """Start :class:`ProactiveAssistant` in a background daemon thread.

    Stores ``get_assistant`` (callable) and ``assistant_enabled`` (bool) in
    ``ctx.obj``.  The callable waits up to *timeout* seconds for the background
    thread to finish loading before returning ``None``.

    Skipped entirely for scripting flags (``--plain``, ``--raw``, ``--json``,
    ``-q``) to avoid loading AI dependencies on every automated invocation.
    """
    _SCRIPTING_FLAGS = ("--plain", "--raw", "--json")
    skip = quiet or any(flag in sys.argv for flag in _SCRIPTING_FLAGS)

    if skip:
        ctx.obj["get_assistant"] = lambda timeout=0.5: None
        ctx.obj["assistant_enabled"] = False
        return

    holder: dict[str, object] = {"instance": None, "error": None}
    ready = threading.Event()

    def _load() -> None:
        try:
            from navig.config import get_config_manager as _gcm
            from navig.proactive_assistant import ProactiveAssistant

            cfg = _gcm()
            holder["instance"] = ProactiveAssistant(cfg)
        except Exception as exc:
            holder["error"] = exc
        finally:
            ready.set()

    threading.Thread(target=_load, daemon=True).start()

    def _get_assistant(timeout: float = 0.5) -> object | None:
        """Wait up to *timeout* seconds then return the assistant or ``None``."""
        ready.wait(timeout=timeout)
        return holder.get("instance")

    ctx.obj["get_assistant"] = _get_assistant
    ctx.obj["assistant_enabled"] = True


# =============================================================================
# Private helpers
# =============================================================================


def _lazy_ch():
    """Return ``console_helper`` lazily to avoid rich.* import at module load."""
    from navig.lazy_loader import lazy_import

    return lazy_import("navig.console_helper")
