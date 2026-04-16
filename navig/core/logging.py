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

# Guard against repeated root-logger configuration.
_ROOT_CONFIGURED = False

# Log format including the optional session tag injected by the record factory.
LOG_FORMAT = "%(asctime)s [%(name)s]%(session_tag)s %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotating file-handler defaults.
_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_FILE_BACKUP_COUNT = 5


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
    try:
        # Suppress RichHandler during pytest — it interferes with capsys/capfd.
        if "pytest" in sys.modules:
            raise ImportError("pytest active — using plain StreamHandler")
        from rich.console import Console
        from rich.logging import RichHandler

        console_handler: logging.Handler = RichHandler(
            rich_tracebacks=True,
            show_time=False,
            show_path=False,
            markup=False,
            highlighter=None,  # type: ignore[arg-type]
            console=Console(stderr=True),
        )
    except ImportError:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        )

    console_handler.setLevel(level)
    root.addHandler(console_handler)

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
    cached = _LOGGERS.get(logger_name)
    if cached is not None:
        return cached

    # Swap in our custom class only for this lookup; restore immediately after.
    original_class = logging.getLoggerClass()
    logging.setLoggerClass(StructuredLogger)
    try:
        logger = logging.getLogger(logger_name)
    finally:
        logging.setLoggerClass(original_class)

    # Lazy root-logger configuration on first ever get_logger() call.
    if not _ROOT_CONFIGURED:
        _ROOT_CONFIGURED = True
        try:
            from navig.config import get_config_manager

            log_path = get_config_manager().base_dir / "navig.log"
            _configure_root_logger(log_path)
        except Exception:
            _configure_root_logger()

    # logging.Manager may have returned a pre-existing plain Logger for this
    # name.  Replace it to guarantee a StructuredLogger.
    if not isinstance(logger, StructuredLogger):
        logging.Logger.manager.loggerDict.pop(logger_name, None)
        logging.setLoggerClass(StructuredLogger)
        try:
            logger = logging.getLogger(logger_name)
        finally:
            logging.setLoggerClass(original_class)

    assert isinstance(logger, StructuredLogger), (  # noqa: S101
        f"Expected StructuredLogger for '{logger_name}', got {type(logger).__name__}"
    )

    _LOGGERS[logger_name] = logger
    return logger


# ---------------------------------------------------------------------------
# Module-level boot
# ---------------------------------------------------------------------------

# Install the session-tag record factory at import time so every module that
# imports navig.core.logging automatically benefits from session correlation.
_install_session_record_factory()
