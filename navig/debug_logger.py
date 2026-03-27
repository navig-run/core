"""
Debug Logging System for NAVIG

Captures all CLI activity, SSH commands, and operation results to a structured
log file for troubleshooting and auditing purposes.

Log Format:
- ISO 8601 timestamps with milliseconds (YYYY-MM-DDTHH:MM:SS.sssZ)
- Structured sections with clear separators
- Sensitive data redaction (passwords, keys, tokens)
- RotatingFileHandler for log rotation
"""

import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class DebugLogger:
    """
    Comprehensive debug logger for NAVIG CLI operations.

    Features:
    - ISO 8601 timestamps with milliseconds
    - Rotating log files (default 10MB, 5 backups)
    - Sensitive data redaction
    - Structured log format with clear separators
    - Performance-optimized with buffered I/O
    """

    # Patterns for sensitive data redaction
    # Extended with patterns from navig.core.security module (Agent-inspired)
    SENSITIVE_PATTERNS = [
        # Original patterns (backward compatibility)
        (
            re.compile(r'(password["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE),
            r"\1***REDACTED***",
        ),
        (
            re.compile(
                r'(ssh_password["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE
            ),
            r"\1***REDACTED***",
        ),
        (
            re.compile(r'(api_key["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE),
            r"\1***REDACTED***",
        ),
        (
            re.compile(r'(token["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE),
            r"\1***REDACTED***",
        ),
        (
            re.compile(r'(secret["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE),
            r"\1***REDACTED***",
        ),
        (
            re.compile(r'(auth["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+', re.IGNORECASE),
            r"\1***REDACTED***",
        ),
        (
            re.compile(r"(-p\s+)[^\s]+", re.IGNORECASE),
            r"\1***REDACTED***",
        ),  # MySQL -p password
        (re.compile(r"(MYSQL_PWD=)[^\s]+", re.IGNORECASE), r"\1***REDACTED***"),
        (
            re.compile(r"(-----BEGIN[^-]+-----)[^-]+(-----END)", re.DOTALL),
            r"\1***REDACTED***\2",
        ),  # SSH keys
        (
            re.compile(r"(Authorization:\s*Bearer\s+)[^\s]+", re.IGNORECASE),
            r"\1***REDACTED***",
        ),  # Bearer tokens
        (
            re.compile(r"(Authorization:\s*Basic\s+)[^\s]+", re.IGNORECASE),
            r"\1***REDACTED***",
        ),  # Basic auth
        # Agent-inspired patterns for provider API keys
        (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"), r"sk-***REDACTED***"),  # OpenAI
        (
            re.compile(r"\b(sk-proj-[A-Za-z0-9_-]{8,})\b"),
            r"sk-proj-***REDACTED***",
        ),  # OpenAI project
        (
            re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{8,})\b"),
            r"sk-ant-***REDACTED***",
        ),  # Anthropic
        (
            re.compile(r"\b(ghp_[A-Za-z0-9]{20,})\b"),
            r"ghp_***REDACTED***",
        ),  # GitHub PAT
        (
            re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"),
            r"github_pat_***REDACTED***",
        ),  # GitHub fine-grained
        (
            re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
            r"xox*-***REDACTED***",
        ),  # Slack
        (re.compile(r"\b(gsk_[A-Za-z0-9_-]{10,})\b"), r"gsk_***REDACTED***"),  # Groq
        (re.compile(r"\b(AIza[0-9A-Za-z\-_]{20,})\b"), r"AIza***REDACTED***"),  # Google
        (
            re.compile(r"\b(pplx-[A-Za-z0-9_-]{10,})\b"),
            r"pplx-***REDACTED***",
        ),  # Perplexity
        # Connection strings
        (
            re.compile(r"(mysql://[^:]+:)([^@]+)(@)", re.IGNORECASE),
            r"\1***REDACTED***\3",
        ),
        (
            re.compile(r"(postgres://[^:]+:)([^@]+)(@)", re.IGNORECASE),
            r"\1***REDACTED***\3",
        ),
    ]

    SEPARATOR = "=" * 80

    def __init__(
        self,
        log_path: Path | None = None,
        max_size_mb: int = 10,
        max_files: int = 5,
        truncate_output_kb: int = 10,
    ):
        """
        Initialize the debug logger.

        Args:
            log_path: Path to log file. If None, uses app-specific or global default.
            max_size_mb: Maximum log file size in MB before rotation.
            max_files: Number of backup files to keep.
            truncate_output_kb: Maximum output size in KB before truncation.
        """
        # Convert string path to Path object if needed
        if log_path is not None and not isinstance(log_path, Path):
            log_path = Path(log_path)
        self.log_path = log_path
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self.truncate_output_bytes = truncate_output_kb * 1024
        self.logger: logging.Logger | None = None
        self._command_start_time: datetime | None = None

        self._setup_logger()

    def _setup_logger(self):
        """Configure the rotating file handler and logger."""
        if self.log_path is None:
            # Determine log path based on context
            from navig.config import get_config_manager

            config_manager = get_config_manager()
            self.log_path = config_manager.base_dir / "debug.log"

        # Ensure parent directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Create logger with unique name to avoid conflicts
        self.logger = logging.getLogger("navig.debug")
        self.logger.setLevel(logging.DEBUG)

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # Create rotating file handler
        handler = RotatingFileHandler(
            self.log_path,
            maxBytes=self.max_size_bytes,
            backupCount=self.max_files,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)

        # Simple formatter - we handle formatting in log methods
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)
        self._handler = handler  # Store reference for cleanup

        # Prevent propagation to root logger
        self.logger.propagate = False

    def close(self):
        """Close the log file handler and release resources."""
        if self.logger and hasattr(self, "_handler"):
            self._handler.close()
            self.logger.removeHandler(self._handler)

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format with milliseconds."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    def _redact_sensitive_data(self, text: str) -> str:
        """Redact sensitive information from text."""
        if not text:
            return text

        result = text
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    def _truncate_output(self, output: str) -> str:
        """Truncate output if it exceeds the configured limit."""
        if not output:
            return output

        output_bytes = output.encode("utf-8", errors="replace")
        if len(output_bytes) <= self.truncate_output_bytes:
            return output

        truncated = output_bytes[: self.truncate_output_bytes].decode(
            "utf-8", errors="replace"
        )
        return f"{truncated}\n... [OUTPUT TRUNCATED - {len(output_bytes)} bytes total]"

    def _log(self, message: str):
        """Write message to log file."""
        if self.logger:
            self.logger.debug(message)

    def log_command_start(self, command: str, args: dict[str, Any]):
        """
        Log the start of a CLI command.

        Args:
            command: The full command string (e.g., "navig host add myserver")
            args: Dictionary of command arguments
        """
        self._command_start_time = datetime.now(timezone.utc)
        timestamp = self._get_timestamp()

        # Redact sensitive args
        safe_args = {}
        for key, value in args.items():
            if isinstance(value, str):
                safe_args[key] = self._redact_sensitive_data(str(value))
            else:
                safe_args[key] = value

        lines = [
            self.SEPARATOR,
            f"[{timestamp}] COMMAND START",
            f"Command: {command}",
            f"Arguments: {safe_args}",
            f"Working Directory: {Path.cwd()}",
            f"Python: {sys.version.split()[0]}",
            f"Platform: {sys.platform}",
            self.SEPARATOR,
        ]
        self._log("\n".join(lines))

    def log_ssh_command(
        self, host: str, port: int, user: str, command: str, method: str = "subprocess"
    ):
        """
        Log an SSH command being executed.

        Args:
            host: Remote host address
            port: SSH port
            user: SSH username
            command: The command being executed
            method: SSH method (subprocess or paramiko)
        """
        timestamp = self._get_timestamp()
        safe_command = self._redact_sensitive_data(command)

        lines = [
            f"[{timestamp}] SSH COMMAND",
            f"Target: {user}@{host}:{port}",
            f"Method: {method}",
            f"Command: {safe_command}",
        ]
        self._log("\n".join(lines))

    def log_ssh_result(
        self, success: bool, output: str, error: str = "", duration_ms: float = 0
    ):
        """
        Log the result of an SSH command.

        Args:
            success: Whether the command succeeded
            output: Command stdout
            error: Command stderr
            duration_ms: Execution time in milliseconds
        """
        timestamp = self._get_timestamp()
        status = "SUCCESS" if success else "FAILED"

        # Truncate and redact output
        safe_output = self._truncate_output(self._redact_sensitive_data(output))
        safe_error = self._truncate_output(self._redact_sensitive_data(error))

        lines = [
            f"[{timestamp}] SSH RESULT: {status}",
            f"Duration: {duration_ms:.2f}ms",
        ]

        if safe_output:
            lines.append(f"Output:\n{safe_output}")
        if safe_error:
            lines.append(f"Error:\n{safe_error}")

        lines.append("-" * 40)
        self._log("\n".join(lines))

    def log_command_end(self, success: bool, message: str = ""):
        """
        Log the end of a CLI command.

        Args:
            success: Whether the command completed successfully
            message: Optional completion message
        """
        timestamp = self._get_timestamp()
        status = "SUCCESS" if success else "FAILED"

        # Calculate duration
        duration_str = ""
        if self._command_start_time:
            duration = datetime.now(timezone.utc) - self._command_start_time
            duration_ms = duration.total_seconds() * 1000
            duration_str = f"Duration: {duration_ms:.2f}ms"

        lines = [
            self.SEPARATOR,
            f"[{timestamp}] COMMAND END: {status}",
        ]
        if duration_str:
            lines.append(duration_str)
        if message:
            # Convert message to string if needed
            message_str = str(message) if not isinstance(message, str) else message
            lines.append(f"Message: {self._redact_sensitive_data(message_str)}")
        lines.append(self.SEPARATOR + "\n")

        self._log("\n".join(lines))
        self._command_start_time = None

    def log_error(self, error: Exception, context: str = ""):
        """
        Log an error with context.

        Args:
            error: The exception that occurred
            context: Description of what operation was being performed
        """
        timestamp = self._get_timestamp()

        lines = [
            f"[{timestamp}] ERROR",
            f"Type: {type(error).__name__}",
            f"Message: {self._redact_sensitive_data(str(error))}",
        ]
        if context:
            lines.append(f"Context: {context}")

        # Include traceback for debugging
        import traceback

        tb = traceback.format_exc()
        if tb and tb != "NoneType: None\n":
            lines.append(f"Traceback:\n{self._redact_sensitive_data(tb)}")

        lines.append("-" * 40)
        self._log("\n".join(lines))

    def log_operation(
        self, operation: str, details: dict[str, Any], success: bool = True
    ):
        """
        Log a general operation (file transfer, database query, etc.).

        Args:
            operation: Name of the operation
            details: Dictionary of operation details
            success: Whether the operation succeeded
        """
        timestamp = self._get_timestamp()
        status = "SUCCESS" if success else "FAILED"

        # Redact sensitive details
        safe_details = {}
        for key, value in details.items():
            if isinstance(value, str):
                safe_details[key] = self._redact_sensitive_data(str(value))
            else:
                safe_details[key] = value

        lines = [
            f"[{timestamp}] OPERATION: {operation} [{status}]",
        ]
        for key, value in safe_details.items():
            lines.append(f"  {key}: {value}")

        lines.append("-" * 40)
        self._log("\n".join(lines))


# Global logger instance
_global_logger: DebugLogger | None = None


def get_debug_logger() -> logging.Logger:
    """
    Get a standard Python logger using the new structured logging system.

    This function now delegates to navig.core.logging.get_logger
    """
    try:
        from navig.core.logging import get_logger

        return get_logger("gateway")
    except ImportError:
        # Fallback to legacy implementation if core.logging unavailable
        pass

    global _global_logger

    # Create a standard Python logger
    logger = logging.getLogger("navig.gateway")

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        # Try to use the NAVIG debug log path
        try:
            from navig.config import get_config_manager

            config_manager = get_config_manager()
            log_path = config_manager.base_dir / "debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Add rotating file handler
            handler = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8",
            )
            handler.setLevel(logging.DEBUG)

            # Format with timestamp
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)

            logger.addHandler(handler)
        except Exception:
            # Fallback to console if file logging fails
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter("%(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        # Prevent propagation to root
        logger.propagate = False

    return logger
