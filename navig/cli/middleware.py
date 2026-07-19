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

# ---------------------------------------------------------------------------
# Terminal exit-code capture — the ledger-honesty seam.
#
# An operation record is COMPLETED in an atexit handler (below), because the
# ``@app.callback`` middleware runs BEFORE the subcommand and cannot wrap it.
# But atexit runs during interpreter shutdown — AFTER any SystemExit
# (``typer.Exit`` / ``sys.exit`` / a Click usage error) has already been
# consumed at the top level — so ``sys.exc_info()`` is ``(None, None, None)``
# there and cannot reveal a clean non-zero exit. (The completion also runs in a
# fresh worker thread, whose thread-local ``sys.exc_info()`` is empty regardless
# of what the main thread raised.) The old handler keyed success off
# ``sys.exc_info()`` and therefore recorded EVERY operation — including
# ``navig db tables --host missing`` (exit 2) — as SUCCESS, corrupting
# ``navig ledger show`` and making ``navig skill distill`` (T-069) file a failed
# command as a working step instead of a pitfall.
#
# Fix: ``navig.main.main()`` — the single process-exit chokepoint that already
# branches on the exit code — reports it here via ``note_exit_code()`` BEFORE
# re-raising, and the atexit handler reads this module global (shared across
# threads, unlike ``sys.exc_info()``).
# ---------------------------------------------------------------------------

_terminal_exit_code: object | None = None


def note_exit_code(code: object | None) -> None:
    """Record the process's terminal exit code for the operation ledger.

    Called from ``navig.main.main()`` at every point the process leaves through
    (its ``except SystemExit`` / ``KeyboardInterrupt`` / crash paths), BEFORE
    the process actually exits, so the atexit completion handler can map it to a
    truthful :class:`~navig.operation_recorder.OperationStatus`. ``code`` follows
    ``SystemExit.code`` semantics (``None``/``0`` = clean; an int is the code; a
    non-int message string counts as failure — exit 1, Python's own rule).
    """
    global _terminal_exit_code
    _terminal_exit_code = code


def _exit_code_to_int(code: object | None) -> int:
    """``SystemExit.code`` → the integer the OS sees (Python's own mapping):
    ``None`` → 0; an int stays (``bool`` normalised first); anything else
    (e.g. a string message) → 1."""
    if code is None:
        return 0
    if isinstance(code, bool):  # bool is an int subclass — normalise True→1/False→0
        return int(code)
    if isinstance(code, int):
        return code
    return 1


def _exit_was_success() -> bool:
    """True when the process is exiting cleanly (code 0 or a normal return)."""
    return _exit_code_to_int(_terminal_exit_code) == 0


def _status_for_exit_code(code: object | None):
    """Map a terminal exit code to an OperationStatus.

    ``0`` / ``None`` → SUCCESS; ``130`` (SIGINT / Ctrl-C) → CANCELLED — a cancel
    is neither a working step nor a "don't do this" pitfall, so distill treats it
    as neither; every other non-zero code → FAILED.
    """
    from navig.operation_recorder import OperationStatus

    n = _exit_code_to_int(code)
    if n == 0:
        return OperationStatus.SUCCESS
    if n == 130:
        return OperationStatus.CANCELLED
    return OperationStatus.FAILED


# Meta-commands whose invocation should skip operation recording / fact
# extraction (avoids polluting history with internal bookkeeping calls).
# "ledger " is here for the observer effect: `navig ledger show/verify` are
# pure reads of the ledger and must not append to the thing they inspect.
# "audit " likewise: `navig audit tail` is a pure read of the gateway audit
# log — an inspection view, not an operation worth a history line.
# `navig undo` is deliberately NOT here — an undo is a real operation and
# is recorded on the chain (T-068).
_SKIP_RECORD_KEYWORDS: frozenset[str] = frozenset([
    "history ", "help", "--help", "-h", "--version", "-v",
    "insights ", "dashboard", "suggest",
    "trigger test", "trigger history",
    "ledger ", "audit ",
])
_SKIP_FACT_CMDS: frozenset[str] = frozenset([
    "memory", "kg", "index", "history", "version", "help",
    "--help", "--version",
])

# ---------------------------------------------------------------------------
# Operation-type classification tables (used by _classify_operation_type).
#
# Values are OperationType *strings* on purpose: middleware must not import
# navig.operation_recorder at module load (lazy-import discipline — the enum
# is resolved inside the function). Classification keys on the RESOLVED
# command tokens (`navig <resource> <action>`), never on substrings of the
# raw argv — the old substring heuristic labeled `navig config get` a
# file_download and any `--output` flag a file_upload, labels that became
# user-visible in `navig ledger show` (T-068).
# ---------------------------------------------------------------------------

#: Short CLI aliases normalised to their canonical resource name.
_RESOURCE_ALIASES: dict[str, str] = {
    "h": "host",
    "f": "file",
    "t": "tunnel",
    "r": "run",
    "database": "db",
}

#: Resources whose second token is a free-form payload (a shell command),
#: never a subcommand — the read-verb rule must not apply to them
#: (`navig run list` executes `list` remotely; it is not a listing).
_FREE_ARG_RESOURCE_TYPES: dict[str, str] = {
    "run": "remote_command",
    "exec": "remote_command",
    "ssh": "remote_command",
    "local": "local_command",
}

#: Actions that are pure reads wherever they appear on the CLI surface.
_READ_ACTIONS: frozenset[str] = frozenset({
    "get", "show", "list", "ls", "status", "info", "search", "logs",
    "history", "settings", "tables", "doctor", "validate", "verify",
    "check", "ps", "help", "version",
})

#: Top-level commands that are pure reads on their own (`navig status`).
_READ_RESOURCES: frozenset[str] = frozenset({"status", "doctor", "version", "paths"})

#: Explicit (resource, action) → OperationType value. Wins over the generic
#: read-verb rule: `file get` downloads (a local copy now exists) even though
#: bare `get` is a read verb everywhere else.
_EXACT_OPERATION_TYPES: dict[tuple[str, str], str] = {
    ("file", "get"): "file_download",
    ("file", "add"): "file_upload",
    ("file", "edit"): "file_modify",
    ("file", "remove"): "file_delete",
    ("db", "dump"): "database_dump",
    ("tunnel", "run"): "tunnel_start",
    ("tunnel", "auto"): "tunnel_start",
    ("tunnel", "remove"): "tunnel_stop",
    ("host", "use"): "host_switch",
    ("config", "set"): "config_change",
    ("flow", "run"): "workflow_run",
}

#: Resource → default OperationType value when no action rule matched.
_RESOURCE_OPERATION_TYPES: dict[str, str] = {
    "db": "database_query",
    "docker": "docker_command",
    "service": "service_restart",
}


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

        op_type = _classify_operation_type(non_global)

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

            # Write an in-flight marker so a hard kill (SIGKILL / an un-caught
            # SIGTERM) that never reaches the atexit completer still leaves a
            # trace — `navig ledger reap` / `navig doctor` turn it into an honest
            # `interrupted` record instead of a silently-lost operation.
            recorder.mark_inflight(record)

    except Exception as exc:
        if verbose:
            ch.dim(f"→ Operation recording skipped: {exc}")

    if "_operation_record" in ctx.obj:
        _register_operation_complete_atexit(ctx)


def _classify_operation_type(tokens: list[str]):
    """Derive the OperationType from the resolved command tokens.

    *tokens* is the global-flag-stripped argv (``extract_non_global_tokens``),
    so ``tokens[0]``/``tokens[1]`` follow the ``navig <resource> <action>``
    contract — free-text arguments and option flags never leak into the match.
    Rules, in priority order:

    1. explicit (resource, action) pairs — `file get` is a download even
       though bare `get` is a read verb everywhere else;
    2. free-payload resources (`run`/`exec`/`ssh`/`local`) — their second
       token is a shell command, not a subcommand;
    3. read-only resources and read verbs → READ_QUERY (no side effect);
    4. resource defaults (db/docker/service);
    5. LOCAL_COMMAND — the honest "unknowable" fallback (red, T-068).
    """
    from navig.operation_recorder import OperationType

    if not tokens:
        return OperationType.LOCAL_COMMAND

    resource = _RESOURCE_ALIASES.get(tokens[0], tokens[0])
    action = tokens[1] if len(tokens) > 1 else ""

    exact = _EXACT_OPERATION_TYPES.get((resource, action))
    if exact is not None:
        return OperationType(exact)
    free_arg = _FREE_ARG_RESOURCE_TYPES.get(resource)
    if free_arg is not None:
        return OperationType(free_arg)
    if resource in _READ_RESOURCES or action in _READ_ACTIONS:
        return OperationType.READ_QUERY
    default = _RESOURCE_OPERATION_TYPES.get(resource)
    if default is not None:
        return OperationType(default)
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
                    from navig.operation_recorder import OperationStatus

                    duration_ms = (time.time() - start_time) * 1000
                    # The terminal exit code is captured by note_exit_code()
                    # from navig.main BEFORE the process exits — a module global
                    # readable from this worker thread, unlike sys.exc_info().
                    status = _status_for_exit_code(_terminal_exit_code)
                    exit_code = _exit_code_to_int(_terminal_exit_code)

                    recorder.complete_operation(
                        record=record,
                        success=status == OperationStatus.SUCCESS,
                        output="",
                        exit_code=exit_code,
                        duration_ms=duration_ms,
                        status=status,
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

        # Same seam as the operation ledger: sys.exc_info() is empty at atexit
        # (and in this handler's thread), so read the exit code captured by
        # note_exit_code() instead — otherwise a failed command's debug log
        # would claim success too.
        atexit.register(lambda: debug_logger.log_command_end(success=_exit_was_success()))

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
        # _global_config_loaded is a private attr — guard with hasattr
        # so future ConfigManager refactors don't cause a silent AttributeError.
        if getattr(cm, "_global_config_loaded", False):
            return bool(cm.global_config.get("debug_log", False)), cm.global_config
        # Fast path: read only the YAML file directly.
        import yaml

        gc_path = getattr(cm, "global_config_dir", None)
        if gc_path is not None:
            gc_file = Path(gc_path) / "config.yaml"
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
