"""
Crash Handler for NAVIG CLI.

Handles uncaught exceptions by:
1. Logging full details to a local crash log file.
2. Printing a user-friendly error message (unless --debug is on).
3. Providing an export mechanism for bug reporting.

No telemetry or automatic reporting is performed.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

# Environment variable to force debug mode
ENV_DEBUG_VAR = "NAVIG_DEBUG"

# Maximum number of crash logs to retain
_MAX_CRASH_LOGS = 10


class CrashHandler:
    def __init__(self) -> None:
        self._debug_mode = os.environ.get(ENV_DEBUG_VAR, "0") == "1"
        self._log_dir: Path | None = None

    def enable_debug(self) -> None:
        """Enable debug mode programmatically."""
        self._debug_mode = True
        os.environ[ENV_DEBUG_VAR] = "1"

    @property
    def is_debug(self) -> bool:
        return self._debug_mode

    def _get_log_dir(self) -> Path:
        """Resolve the log directory without crashing if config is broken."""
        if self._log_dir is not None:
            return self._log_dir

        try:
            from navig.config import get_config_manager

            log_dir = get_config_manager().base_dir / "logs"
        except Exception:
            log_dir = config_dir() / "logs"

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # Directory creation failed (permissions, read-only FS, etc.)

        self._log_dir = log_dir
        return log_dir

    def handle_exception(self, exc: Exception) -> None:
        """Main entry point for handling CLI exceptions."""
        log_path = self._log_crash_to_file(exc)

        if self.is_debug:
            traceback.print_exc()
        else:
            self._print_friendly_error(exc, log_path)

        sys.exit(1)

    def _log_crash_to_file(self, exc: Exception) -> Path | None:
        """Write crash details to a JSON log file. Returns the log path or None."""
        try:
            now = datetime.now()
            log_dir = self._get_log_dir()
            log_path = log_dir / f"crash-{now.strftime('%Y%m%d-%H%M%S')}.json"

            sys_info: dict[str, Any] = {
                "platform": sys.platform,
                # python_version() is a pure string format — no WMI calls on Windows.
                "python": platform.python_version(),
                "argv": sys.argv,
                "cwd": str(Path.cwd()),
                "env_debug": os.environ.get(ENV_DEBUG_VAR),
            }

            try:
                from navig import __version__

                sys_info["version"] = __version__
            except ImportError:
                sys_info["version"] = "unknown"

            crash_data: dict[str, Any] = {
                "timestamp": now.isoformat(),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
                "system": sys_info,
            }

            from navig.core.yaml_io import atomic_write_text

            atomic_write_text(log_path, json.dumps(crash_data, indent=2))

            self._cleanup_old_logs(log_dir)
            return log_path

        except Exception as write_exc:
            sys.stderr.write(f"Failed to write crash log: {write_exc}\n")
            return None

    def _cleanup_old_logs(self, log_dir: Path) -> None:
        """Keep only the _MAX_CRASH_LOGS most recent crash logs."""
        try:
            logs = sorted(
                log_dir.glob("crash-*.json"), key=lambda p: p.stat().st_mtime
            )
            for oldest in logs[:-_MAX_CRASH_LOGS]:
                oldest.unlink(missing_ok=True)
        except OSError:
            pass  # Ignore cleanup failures silently

    def _print_friendly_error(self, exc: Exception, log_path: Path | None) -> None:
        """Print a user-friendly error to stderr."""
        err_type = type(exc).__name__
        msg = str(exc)

        try:
            from rich.console import Console

            console = Console(stderr=True)
            console.print()
            console.print(
                f"[bold red]💥 Navig encountered an unexpected error ({err_type})[/bold red]"
            )
            console.print(f"   [red]{msg}[/red]")
            console.print()
            if log_path:
                console.print(f"[dim]Crash details saved to: {log_path}[/dim]")
            console.print(
                "[yellow]Tip:[/yellow] Run with [bold]--debug[/bold] for full details."
            )
            console.print(
                "     Or run [bold]navig crash export[/bold] to create a report for GitHub."
            )
            console.print()
        except ImportError:
            sys.stderr.write(
                f"\n💥 Navig encountered an unexpected error ({err_type})\n"
                f"   {msg}\n\n"
            )
            if log_path:
                sys.stderr.write(f"Crash details saved to: {log_path}\n")
            sys.stderr.write(
                "Tip: Run with --debug for full details.\n"
                "     Or run 'navig crash export' to create a report for GitHub.\n\n"
            )

    def get_latest_crash_report(self) -> dict[str, Any] | None:
        """Return the content of the most recent crash log, or None."""
        try:
            log_dir = self._get_log_dir()
            logs = sorted(
                log_dir.glob("crash-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not logs:
                return None
            return json.loads(logs[0].read_text(encoding="utf-8"))
        except Exception:
            return None


# Module-level singleton
crash_handler = CrashHandler()
