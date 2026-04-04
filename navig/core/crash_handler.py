"""
Crash Handler for NAVIG CLI.

Handles uncaught exceptions by:
1. Logging full details to a local crash log file.
2. Printing a user-friendly error message (unless --debug is on).
3. Providing an export mechanism for bug reporting.

No telemetry or automatic reporting is performed.
"""

import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# Environment variable to force debug mode
ENV_DEBUG_VAR = "NAVIG_DEBUG"


class CrashHandler:
    def __init__(self):
        self._debug_mode = os.environ.get(ENV_DEBUG_VAR, "0") == "1"
        self._log_dir: Path | None = None

    def enable_debug(self):
        """Enable debug mode programmatically."""
        self._debug_mode = True
        os.environ[ENV_DEBUG_VAR] = "1"

    @property
    def is_debug(self) -> bool:
        return self._debug_mode

    def _get_log_dir(self) -> Path:
        """Resolve log directory without crashing if config is broken."""
        if self._log_dir:
            return self._log_dir

        try:
            # Try to get from config manager
            from navig.config import get_config_manager

            cm = get_config_manager()
            log_dir = cm.base_dir / "logs"
        except Exception:
            # Fallback to local .navig/logs or user home
            log_dir = Path.home() / ".navig" / "logs"
            if Path(".navig").exists():
                log_dir = Path(".navig") / "logs"

        # Ensure it exists
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # Can't create log dir, maybe permissions?

        self._log_dir = log_dir
        return log_dir

    def handle_exception(self, exc: Exception) -> None:
        """
        Main entry point for handling CLI exceptions.
        """
        # Always log the crash to file first
        log_path = self._log_crash_to_file(exc)

        # Print output to user
        if self.is_debug:
            # Print full traceback to stderr
            traceback.print_exc()
        else:
            # Friendly message
            self._print_friendly_error(exc, log_path)

        # Exit with non-zero status
        sys.exit(1)

    def _log_crash_to_file(self, exc: Exception) -> Path | None:
        """Write crash details to a JSON log file."""
        try:
            timestamp = datetime.now().isoformat()
            log_dir = self._get_log_dir()
            filename = f"crash-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            log_path = log_dir / filename

            # Gather system info — use sys.platform instead of platform.platform()
            # because platform.platform() calls platform.uname() which on Windows
            # Python 3.12+ triggers a WMI query that can hang indefinitely.
            sys_info = {
                "platform": sys.platform,
                "python": platform.python_version(),  # pure string, no WMI
                "argv": sys.argv,
                "cwd": str(Path.cwd()),
                "env_debug": os.environ.get(ENV_DEBUG_VAR),
            }

            # Try to get version
            try:
                from navig import __version__

                sys_info["version"] = __version__
            except ImportError:
                sys_info["version"] = "unknown"

            # Get traceback
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

            crash_data = {
                "timestamp": timestamp,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": tb,
                "system": sys_info,
            }

            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(crash_data, f, indent=2)

            # Clean up old logs (keep last 10)
            self._cleanup_old_logs(log_dir)

            return log_path

        except Exception as e:
            # Use basic stderr if logging fails
            sys.stderr.write(f"Failed to write crash log: {e}\n")
            return None

    def _cleanup_old_logs(self, log_dir: Path):
        """Keep only the last 10 crash logs."""
        try:
            logs = sorted(log_dir.glob("crash-*.json"), key=os.path.getmtime)
            while len(logs) > 10:
                oldest = logs.pop(0)
                os.remove(oldest)
        except Exception:
            pass  # Ignore cleanup errors

    def _print_friendly_error(self, exc: Exception, log_path: Path | None):
        """Print a nice error message to stderr."""
        # Check for specific known errors to be helpful
        msg = str(exc)
        err_type = type(exc).__name__

        # Use rich if available, else plain text
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

            console.print("[yellow]Tip:[/yellow] Run with [bold]--debug[/bold] for full details.")
            console.print(
                "     Or run [bold]navig crash export[/bold] to Create a report for GitHub."
            )
            console.print()

        except ImportError:
            sys.stderr.write(f"\n💥 Navig encountered an unexpected error ({err_type})\n")
            sys.stderr.write(f"   {msg}\n\n")
            if log_path:
                sys.stderr.write(f"Crash details saved to: {log_path}\n")
            sys.stderr.write("Tip: Run with --debug for full details.\n")
            sys.stderr.write("     Or run 'navig crash export' to create a report for GitHub.\n\n")

    def get_latest_crash_report(self) -> dict[str, Any] | None:
        """Retrieve the content of the most recent crash log."""
        try:
            log_dir = self._get_log_dir()
            logs = sorted(log_dir.glob("crash-*.json"), key=os.path.getmtime, reverse=True)

            if not logs:
                return None

            latest = logs[0]
            with open(latest, encoding="utf-8") as f:
                return json.load(f)

        except Exception:
            return None


# Global instance
crash_handler = CrashHandler()
