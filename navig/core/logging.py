"""
Structured logging subsystem for NAVIG.

Provides per-subsystem loggers with:
- Automatic sensitive-data redaction in messages *and* format args.
- RichHandler console output (gracefully falls back to StreamHandler).
- RotatingFileHandler file output.
- Structured JSON event helper.

Usage::

    from navig.core.logging import get_logger

    logger = get_logger("database")
    logger.info("Connecting to %s", host)
    logger.structured(logging.INFO, "query_complete", rows=42)
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from navig.core.security import redact_sensitive_text
except ImportError:
    def redact_sensitive_text(text: str) -> str:  # type: ignore[misc]
        return text

# Registry of already-created subsystem loggers.
_LOGGERS: dict[str, StructuredLogger] = {}

# Whether the "navig" root logger has been configured at least once.
_ROOT_CONFIGURED = False

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotating file handler config
_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_FILE_BACKUP_COUNT = 5


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
        # Always pass a plain string to the underlying machinery.
        if isinstance(msg, str):
            msg = redact_sensitive_text(msg)
        else:
            msg = str(msg)

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
            **kwargs: Arbitrary key/value pairs merged into the JSON payload.
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


def _configure_root_logger(
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Set up handlers on the ``navig`` root logger.

    Safe to call multiple times — handlers are cleared first.
    """
    root = logging.getLogger("navig")
    root.setLevel(level)
    root.propagate = False
    root.handlers.clear()

    # 1. Console handler — stderr keeps stdout clean for structured output.
    try:
        # Skip RichHandler during pytest to avoid interfering with capsys/capfd.
        if "pytest" in sys.modules:
            raise ImportError("pytest active")
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

    # 2. Optional rotating file handler.
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=_FILE_MAX_BYTES,
            backupCount=_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        )
        # Always capture DEBUG to file so post-mortem analysis is possible.
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)


def get_logger(subsystem: str = "core") -> StructuredLogger:
    """Return a :class:`StructuredLogger` for *subsystem*.

    The first call performs lazy root-logger configuration so startup cost is
    zero when logging is not actually used.

    Args:
        subsystem: Short name such as ``'auth'``, ``'db'``, or ``'ssh'``.

    Returns:
        A :class:`StructuredLogger` instance named ``navig.<subsystem>``.
    """
    global _ROOT_CONFIGURED

    logger_name = f"navig.{subsystem}"
    cached = _LOGGERS.get(logger_name)
    if cached is not None:
        return cached

    # Install our custom class only for the duration of this lookup so we do
    # not permanently replace the global logger class.
    original_class = logging.getLoggerClass()
    logging.setLoggerClass(StructuredLogger)
    try:
        logger = logging.getLogger(logger_name)
    finally:
        logging.setLoggerClass(original_class)

    # Configure root lazily on first use.
    if not _ROOT_CONFIGURED:
        _ROOT_CONFIGURED = True
        try:
            from navig.config import get_config_manager

            log_path = get_config_manager().base_dir / "navig.log"
            _configure_root_logger(log_path)
        except Exception:
            _configure_root_logger()

    # The logger retrieved above may be a plain Logger if logging already had
    # a non-StructuredLogger cached for this name. Ensure correct type.
    if not isinstance(logger, StructuredLogger):
        # Replace the cached instance with a properly typed one.
        logging.Logger.manager.loggerDict.pop(logger_name, None)
        logging.setLoggerClass(StructuredLogger)
        try:
            logger = logging.getLogger(logger_name)
        finally:
            logging.setLoggerClass(original_class)

    assert isinstance(logger, StructuredLogger), (
        f"Expected StructuredLogger, got {type(logger).__name__}"
    )

    _LOGGERS[logger_name] = logger
    return logger
