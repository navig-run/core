"""
Structured Logging System with Subsystems

Provides enhanced logging capabilities:
- Per-subsystem loggers (e.g., 'auth', 'database', 'ssh')
- Automatic sensitive data redaction
- Structured JSON output option
- Rich console formatting

Usage:
    from navig.core.logging import get_logger

    logger = get_logger("database")
    logger.info("Connecting to DB", host="localhost")
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Import redaction helper from security module
try:
    from navig.core.security import redact_sensitive_text
except ImportError:
    # Fallback if security module not available
    def redact_sensitive_text(text: str) -> str:
        return text


# Map of subsystem names to loggers
_LOGGERS: dict[str, logging.Logger] = {}

# Default config
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class StructuredLogger(logging.Logger):
    """
    Extended Logger that supports structured data and auto-redaction.
    """

    def _log(
        self,
        level,
        msg,
        args,
        exc_info=None,
        extra=None,
        stack_info=False,
        stacklevel=1,
    ):
        """
        Override _log to redact sensitive data in messages.
        """
        if isinstance(msg, str):
            msg = redact_sensitive_text(msg)
        else:
            # Ensure handler formatters always receive a plain string message.
            msg = str(msg)

        # If args are provided, redact them too
        if args:
            new_args = []
            for arg in args:
                if isinstance(arg, str):
                    new_args.append(redact_sensitive_text(arg))
                else:
                    new_args.append(arg)
            args = tuple(new_args)

        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel)

    def structured(self, level: int, event: str, **kwargs):
        """
        Log a structured event (JSON).
        """
        data = {"event": event, "subsystem": self.name.replace("navig.", ""), **kwargs}

        # Redact values in the dictionary
        safe_data = {}
        for k, v in data.items():
            if isinstance(v, str):
                safe_data[k] = redact_sensitive_text(v)
            else:
                safe_data[k] = v

        self.log(level, json.dumps(safe_data))


def _configure_root_logger(log_file: Path | None = None, level: int = logging.INFO):
    """
    Configure the root logger with handlers.
    """
    root = logging.getLogger("navig")
    root.setLevel(level)
    root.propagate = False

    # Clear existing handlers
    root.handlers.clear()

    # 1. Console Handler — use stderr so JSON on stdout stays clean
    try:
        import sys

        if "pytest" in sys.modules:
            raise ImportError("pytest")
        from rich.logging import RichHandler

        console_handler = RichHandler(
            rich_tracebacks=True,
            show_time=False,
            show_path=False,
            markup=False,
            highlighter=None,
            console=__import__("rich.console", fromlist=["Console"]).Console(
                stderr=True
            ),
        )
    except ImportError:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # 2. File Handler (if path provided)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"  # 10MB
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        root.addHandler(file_handler)


def get_logger(subsystem: str = "core") -> StructuredLogger:
    """
    Get a logger for a specific subsystem.

    Args:
        subsystem: Name of the subsystem (e.g., 'auth', 'db')

    Returns:
        StructuredLogger instance
    """
    logger_name = f"navig.{subsystem}"

    if logger_name in _LOGGERS:
        return _LOGGERS[logger_name]

    # Ensure custom logger class is used
    original_class = logging.getLoggerClass()
    logging.setLoggerClass(StructuredLogger)

    logger = logging.getLogger(logger_name)

    # Restore original class
    logging.setLoggerClass(original_class)

    # Setup handlers if root not configured (lazy init)
    if not logging.getLogger("navig").handlers:
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            log_path = cm.base_dir / "navig.log"
            _configure_root_logger(log_path)
        except Exception:
            # Fallback if config manager not available
            _configure_root_logger()

    _LOGGERS[logger_name] = logger
    return logger
