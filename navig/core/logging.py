"""
Structured logging subsystem for NAVIG.

Provides per-subsystem loggers with:
- Automatic sensitive-data redaction in messages *and* format args.
- Optional session-tag correlation injected via a log-record factory.
- RichHandler console output (gracefully falls back to StreamHandler).
- RotatingFileHandler file output with RedactingFormatter.
- Component-prefix filters for fine-grained output routing.
- Structured JSON event helper.

Usage::

    from navig.core.logging import get_logger, set_session_context

    set_session_context("abc123")
    logger = get_logger("database")
    logger.info("Connecting to %s", host)
    logger.structured(logging.INFO, "query_complete", rows=42)
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Redaction + RedactingFormatter — sourced from security module when available
# ---------------------------------------------------------------------------

try:
    from navig.core.security import RedactingFormatter, redact_sensitive_text
except ImportError:
    def redact_sensitive_text(text: str) -> str:  # type: ignore[misc]
        return text

    class RedactingFormatter(logging.Formatter):  # type: ignore[no-redef]
        """No-op fallback when the security module is unavailable."""

        def format(self, record: logging.LogRecord) -> str:  # noqa: A003
            return super().format(record)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Registry of already-created subsystem loggers (logger_name → StructuredLogger).
_LOGGERS: dict[str, StructuredLogger] = {}
_LOGGERS_LOCK = threading.Lock()

# Guard against repeated root-logger configuration.
_ROOT_CONFIGURED = False
_ROOT_CONFIG_LOCK = threading.Lock()

# Log format including the optional session tag injected by the record factory.
LOG_FORMAT = "%(asctime)s [%(name)s]%(session_tag)s %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotating file-handler defaults.
_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_FILE_BACKUP_COUNT = 5


# ---------------------------------------------------------------------------
# Console formatting — clean, aligned, color-coded terminal output
# ---------------------------------------------------------------------------

def _supports_color(stream) -> bool:
    """Return True if *stream* is a TTY that should receive ANSI color.

    Honors the NO_COLOR convention (https://no-color.org) and FORCE_COLOR.
    On Windows, enables virtual-terminal processing so modern terminals
    (Windows Terminal, VS Code) render ANSI sequences.
    """
    import os

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004 on STD_ERROR_HANDLE (-12)
            handle = kernel32.GetStdHandle(-12)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return True


class NavigConsoleFormatter(logging.Formatter):
    """Aligned, color-coded console formatter.

    Layout:  ``HH:MM:SS  LEVEL  component       message``

    - timestamp dimmed, level color-coded, component (subsystem) dimmed and
      width-padded, message in default weight.
    - Degrades to plain text when color is unavailable.
    """

    _RESET = "\x1b[0m"
    # (ansi_code, 4-char label) per level.
    _LEVELS = {
        "DEBUG":    ("38;5;245", "DBUG"),
        "INFO":     ("38;5;39",  "INFO"),
        "WARNING":  ("38;5;214", "WARN"),
        "ERROR":    ("38;5;203", "ERR "),
        "CRITICAL": ("48;5;203;38;5;231", "CRIT"),
    }
    _TS_STYLE = "38;5;240"
    _COMP_STYLE = "38;5;245"
    _COMP_WIDTH = 13

    def __init__(self, use_color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.use_color = use_color

    def _paint(self, code: str, text: str) -> str:
        if not self.use_color:
            return text
        return f"\x1b[{code}m{text}{self._RESET}"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts = self.formatTime(record, self.datefmt)
        color, label = self._LEVELS.get(record.levelname, ("0", record.levelname[:4].upper()))

        # Use the leaf subsystem segment — most informative and always short.
        # navig.gateway.server → server, navig.connectors.auth → auth.
        comp = record.name.removeprefix("navig.").split(".")[-1]
        if len(comp) > self._COMP_WIDTH:
            comp = comp[: self._COMP_WIDTH - 1] + "…"
        comp_padded = comp.ljust(self._COMP_WIDTH)

        session = getattr(record, "session_tag", "") or ""
        msg = record.getMessage()

        line = (
            f"{self._paint(self._TS_STYLE, ts)}  "
            f"{self._paint(color, label)}  "
            f"{self._paint(self._COMP_STYLE, comp_padded)}  "
            f"{msg}{session}"
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


# ---------------------------------------------------------------------------
# Session-correlated logging
# ---------------------------------------------------------------------------

_session_context: threading.local = threading.local()


def set_session_context(session_id: str | None) -> None:
    """Associate the current thread with *session_id* for log correlation.

    After calling this, every log record emitted from the same thread carries
    a ``session_tag`` attribute (e.g. ``" [abc123ef]"``) that appears in the
    formatted log line.  Call with ``None`` to clear.
    """
    _session_context.session_id = session_id


def clear_session_context() -> None:
    """Remove the session identifier from the current thread."""
    _session_context.session_id = None


def _install_session_record_factory() -> None:
    """Wrap the log-record factory to inject ``session_tag`` on every record.

    Idempotent — detected by a sentinel attribute on the factory so the chain
    is never doubled even if this module is imported multiple times (e.g. in
    tests that reload modules).
    """
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_navig_session_injector", False):
        return

    def _factory(*args, **kwargs) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        sid = getattr(_session_context, "session_id", None)
        record.session_tag = f" [{sid}]" if sid else ""  # type: ignore[attr-defined]
        return record

    _factory._navig_session_injector = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_factory)


# ---------------------------------------------------------------------------
# Component-prefix filter
# ---------------------------------------------------------------------------

COMPONENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "gateway": ("navig.gateway",),
    "commands": ("navig.commands",),
    "memory": ("navig.memory",),
    "ai": ("navig.ai", "navig.llm"),
    "core": ("navig.core",),
    "platform": ("navig.platform",),
}


class _ComponentFilter(logging.Filter):
    """Pass only records whose logger name starts with one of *prefixes*."""

    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return record.name.startswith(self._prefixes)


# ---------------------------------------------------------------------------
# StructuredLogger
# ---------------------------------------------------------------------------


class StructuredLogger(logging.Logger):
    """Logger subclass with automatic redaction and a structured-event helper."""

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple,
        exc_info=None,
        extra=None,
        stack_info: bool = False,
        stacklevel: int = 1,
    ) -> None:
        """Redact sensitive data from the message and any string format args."""
        msg = redact_sensitive_text(msg) if isinstance(msg, str) else str(msg)

        if args:
            args = tuple(
                redact_sensitive_text(a) if isinstance(a, str) else a for a in args
            )

        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel)

    def structured(self, level: int, event: str, **kwargs: object) -> None:
        """Emit a JSON-encoded structured event.

        Args:
            level: Logging level (e.g. ``logging.INFO``).
            event: Short event identifier.
            **kwargs: Arbitrary payload merged into the JSON object.
        """
        data: dict[str, object] = {
            "event": event,
            "subsystem": self.name.removeprefix("navig."),
            **kwargs,
        }
        safe: dict[str, object] = {
            k: redact_sensitive_text(v) if isinstance(v, str) else v
            for k, v in data.items()
        }
        self.log(level, json.dumps(safe))


# ---------------------------------------------------------------------------
# Root-logger configuration
# ---------------------------------------------------------------------------


def _configure_root_logger(
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure handlers on the ``navig`` root logger.

    Safe to call multiple times — handlers are cleared before adding new ones.
    """
    root = logging.getLogger("navig")
    root.setLevel(level)
    root.propagate = False
    root.handlers.clear()

    # 1. Console handler on stderr (keeps stdout clean for structured output).
    #    Clean, aligned, color-coded NavigConsoleFormatter. Rich (if present)
    #    is used only to pretty-print tracebacks — regular log lines use our
    #    own formatter so the layout is consistent and uncluttered.
    console_handler = logging.StreamHandler(sys.stderr)
    use_color = _supports_color(sys.stderr) and "pytest" not in sys.modules
    console_handler.setFormatter(NavigConsoleFormatter(use_color=use_color))
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    if "pytest" not in sys.modules:
        try:
            from rich.traceback import install as _install_rich_tb

            _install_rich_tb(show_locals=False, width=None, suppress=[])
        except Exception:
            pass

    # 2. Optional rotating file handler with secret redaction.
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=_FILE_MAX_BYTES,
            backupCount=_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        # File handler always captures DEBUG so post-mortem analysis is possible.
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def get_logger(subsystem: str = "core") -> StructuredLogger:
    """Return a :class:`StructuredLogger` for *subsystem*.

    Lazy-configures the root logger on first use so import-time cost is zero
    when logging is not used.

    Args:
        subsystem: Short subsystem name, e.g. ``'auth'``, ``'db'``, ``'ssh'``.

    Returns:
        A :class:`StructuredLogger` named ``navig.<subsystem>``.
    """
    global _ROOT_CONFIGURED

    logger_name = f"navig.{subsystem}"

    with _LOGGERS_LOCK:
        cached = _LOGGERS.get(logger_name)
        if cached is not None:
            return cached

    # Lazy root-logger configuration — protected by its own lock so only one
    # thread ever calls _configure_root_logger, even under concurrent imports.
    with _ROOT_CONFIG_LOCK:
        if not _ROOT_CONFIGURED:
            _ROOT_CONFIGURED = True
            try:
                from navig.config import get_config_manager
                log_path = get_config_manager().base_dir / "navig.log"
                _configure_root_logger(log_path)
            except Exception:
                _configure_root_logger()

    # Get or create a StructuredLogger for this name.
    # Temporarily swap the logger class so getLogger() returns the right type.
    original_class = logging.getLoggerClass()
    logging.setLoggerClass(StructuredLogger)
    try:
        logger = logging.getLogger(logger_name)
    finally:
        logging.setLoggerClass(original_class)

    # If Python's manager returned a pre-existing plain Logger (e.g. created
    # before this call by a third-party library), replace it safely by removing
    # the old entry from the manager dict and re-requesting it.
    if not isinstance(logger, StructuredLogger):
        # logging.Manager.loggerDict is an implementation detail but there is
        # no public API to replace an existing logger.  We guard with the
        # manager lock to be thread-safe.
        manager = logging.Logger.manager
        with manager.loggerDict.get(logger_name, None) and threading.Lock() or threading.Lock():
            manager.loggerDict.pop(logger_name, None)
        logging.setLoggerClass(StructuredLogger)
        try:
            logger = logging.getLogger(logger_name)
        finally:
            logging.setLoggerClass(original_class)

    if not isinstance(logger, StructuredLogger):
        # Absolute fallback: wrap a new StructuredLogger manually.
        logger = StructuredLogger(logger_name)
        logger.parent = logging.getLogger("navig")  # type: ignore[assignment]
        logger.propagate = True

    with _LOGGERS_LOCK:
        _LOGGERS[logger_name] = logger  # type: ignore[assignment]

    return logger  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Module-level boot
# ---------------------------------------------------------------------------

# Install the session-tag record factory at import time so every module that
# imports navig.core.logging automatically benefits from session correlation.
_install_session_record_factory()
