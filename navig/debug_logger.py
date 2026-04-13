"""
Debug Logging System for NAVIG.

Captures CLI activity, SSH commands, and operation results to a structured
rotating log file for troubleshooting and auditing.

Log format:
- ISO 8601 timestamps with milliseconds (YYYY-MM-DDTHH:MM:SS.sssZ)
- Structured sections with clear separators
- Sensitive data redaction delegated to ``navig.core.security``
- RotatingFileHandler for log rotation

The module-level ``get_debug_logger()`` function is the primary entry point
for all other NAVIG modules that need a standard Python logger.  It delegates
to ``navig.core.logging.get_logger`` so there is only one logging subsystem.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from navig.core.security import redact_sensitive_text


# ---------------------------------------------------------------------------
# DebugLogger — structured audit log written to a dedicated rotating file
# ---------------------------------------------------------------------------


class DebugLogger:
    """Structured audit logger for NAVIG CLI operations.

    All messages are written to a rotating file (default 10 MB × 5 backups).
    Sensitive data is redacted before writing.  The logger does **not**
    propagate to the root logger — it is purely for local audit trails.
    """

    SEPARATOR = "=" * 80
    _REDACTED_SENTINEL = "NoneType: None\n"  # traceback.format_exc() placeholder

    def __init__(
        self,
        log_path: Path | None = None,
        max_size_mb: int = 10,
        max_files: int = 5,
        truncate_output_kb: int = 10,
    ) -> None:
        """
        Args:
            log_path:          Target log file.  ``None`` → resolved from platform paths.
            max_size_mb:       Rotation threshold in megabytes.
            max_files:         Number of rotated backup files to keep.
            truncate_output_kb: Maximum captured output size before truncation.
        """
        self.log_path: Path | None = (
            Path(log_path) if log_path is not None and not isinstance(log_path, Path)
            else log_path
        )
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self.truncate_output_bytes = truncate_output_kb * 1024

        self._logger: logging.Logger | None = None
        self._handler: RotatingFileHandler | None = None
        self._command_start_time: datetime | None = None

        self._setup_logger()

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def _setup_logger(self) -> None:
        """Configure the rotating file handler and attach it to the logger."""
        if self.log_path is None:
            try:
                from navig.platform import paths as _paths
                self.log_path = _paths.debug_log_path()
            except Exception:
                try:
                    from navig.config import get_config_manager
                    self.log_path = get_config_manager().base_dir / "debug.log"
                except Exception:
                    self.log_path = Path.home() / ".navig" / "debug.log"

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("navig.debug")
        logger.setLevel(logging.DEBUG)

        # Close and remove any existing handlers to prevent file descriptor leaks.
        for h in list(logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)

        handler = RotatingFileHandler(
            self.log_path,
            maxBytes=self.max_size_bytes,
            backupCount=self.max_files,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger.addHandler(handler)
        logger.propagate = False

        self._logger = logger
        self._handler = handler

    def close(self) -> None:
        """Close the file handler and release resources."""
        if self._handler is not None and self._logger is not None:
            self._handler.close()
            self._logger.removeHandler(self._handler)
            self._handler = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _timestamp() -> str:
        """Return current UTC time as ISO 8601 with milliseconds."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    def _redact(self, text: str) -> str:
        return redact_sensitive_text(text) if text else text

    def _redact_sensitive_data(self, text: str) -> str:
        """Public alias for ``_redact`` — used in tests and external consumers."""
        return self._redact(text)

    def _truncate(self, output: str) -> str:
        if not output:
            return output
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) <= self.truncate_output_bytes:
            return output
        truncated = encoded[: self.truncate_output_bytes].decode("utf-8", errors="replace")
        return f"{truncated}\n... [OUTPUT TRUNCATED — {len(encoded)} bytes total]"

    def _write(self, message: str) -> None:
        if self._logger is not None:
            self._logger.debug(message)

    # ------------------------------------------------------------------
    # Public logging methods
    # ------------------------------------------------------------------

    def log_command_start(self, command: str, args: dict[str, Any]) -> None:
        """Log the start of a CLI command invocation."""
        self._command_start_time = datetime.now(timezone.utc)
        ts = self._timestamp()

        safe_args = {
            k: self._redact(str(v)) if isinstance(v, str) else v
            for k, v in args.items()
        }

        self._write("\n".join([
            self.SEPARATOR,
            f"[{ts}] COMMAND START",
            f"Command: {command}",
            f"Arguments: {safe_args}",
            f"Working Directory: {Path.cwd()}",
            f"Python: {sys.version.split()[0]}",
            f"Platform: {sys.platform}",
            self.SEPARATOR,
        ]))

    def log_ssh_command(
        self,
        host: str,
        port: int,
        user: str,
        command: str,
        method: str = "subprocess",
    ) -> None:
        """Log an SSH command before it is executed."""
        ts = self._timestamp()
        self._write("\n".join([
            f"[{ts}] SSH COMMAND",
            f"Target: {user}@{host}:{port}",
            f"Method: {method}",
            f"Command: {self._redact(command)}",
        ]))

    def log_ssh_result(
        self,
        success: bool,
        output: str,
        error: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        """Log the result of an SSH command."""
        ts = self._timestamp()
        status = "SUCCESS" if success else "FAILED"

        safe_out = self._truncate(self._redact(output))
        safe_err = self._truncate(self._redact(error))

        lines = [
            f"[{ts}] SSH RESULT: {status}",
            f"Duration: {duration_ms:.2f}ms",
        ]
        if safe_out:
            lines.append(f"Output:\n{safe_out}")
        if safe_err:
            lines.append(f"Error:\n{safe_err}")
        lines.append("-" * 40)

        self._write("\n".join(lines))

    def log_command_end(self, success: bool, message: str = "") -> None:
        """Log the end of a CLI command invocation."""
        ts = self._timestamp()
        status = "SUCCESS" if success else "FAILED"

        lines = [self.SEPARATOR, f"[{ts}] COMMAND END: {status}"]

        if self._command_start_time is not None:
            elapsed = datetime.now(timezone.utc) - self._command_start_time
            lines.append(f"Duration: {elapsed.total_seconds() * 1000:.2f}ms")
            self._command_start_time = None

        if message:
            lines.append(f"Message: {self._redact(str(message))}")

        lines.append(self.SEPARATOR + "\n")
        self._write("\n".join(lines))

    def log_error(self, error: Exception, context: str = "") -> None:
        """Log an exception with its traceback."""
        import traceback

        ts = self._timestamp()
        lines = [
            f"[{ts}] ERROR",
            f"Type: {type(error).__name__}",
            f"Message: {self._redact(str(error))}",
        ]
        if context:
            lines.append(f"Context: {context}")

        tb = traceback.format_exc()
        if tb and tb.strip() != "NoneType: None":
            lines.append(f"Traceback:\n{self._redact(tb)}")

        lines.append("-" * 40)
        self._write("\n".join(lines))

    def log_operation(
        self,
        operation: str,
        details: dict[str, Any],
        success: bool = True,
    ) -> None:
        """Log a general operation (file transfer, database query, etc.)."""
        ts = self._timestamp()
        status = "SUCCESS" if success else "FAILED"

        safe_details = {
            k: self._redact(str(v)) if isinstance(v, str) else v
            for k, v in details.items()
        }

        lines = [f"[{ts}] OPERATION: {operation} [{status}]"]
        lines.extend(f"  {k}: {v}" for k, v in safe_details.items())
        lines.append("-" * 40)
        self._write("\n".join(lines))


# ---------------------------------------------------------------------------
# Module-level singleton / public factory
# ---------------------------------------------------------------------------

# Module-private instance kept only for callers that construct DebugLogger
# directly (legacy path).  Never exposed via get_debug_logger().
_global_debug_logger: DebugLogger | None = None


def get_debug_logger() -> logging.Logger:
    """Return a standard :class:`logging.Logger` for the calling module.

    Delegates to :func:`navig.core.logging.get_logger` so all NAVIG subsystems
    share a single, consistently configured logging hierarchy.

    Falls back to a plain ``navig.gateway`` logger if the core logging module
    is not yet available (e.g. during early boot).
    """
    try:
        from navig.core.logging import get_logger
        return get_logger("gateway")
    except ImportError:
        pass

    # Fallback: plain logger configured once.
    logger = logging.getLogger("navig.gateway")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        try:
            from navig.config import get_config_manager

            log_path = get_config_manager().base_dir / "debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(fh)
        except Exception:
            sh = logging.StreamHandler()
            sh.setLevel(logging.DEBUG)
            sh.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
            logger.addHandler(sh)

        logger.propagate = False

    return logger
