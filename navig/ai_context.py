"""AI Context Aggregation for MCP Integration

Provides context gathering and error aggregation for AI assistants.
Helps AI understand system state, recent failures, and suggest fixes.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from navig import console_helper as ch
from navig.platform import paths


class ErrorLog:
    """Represents a logged error with context."""

    def __init__(
        self,
        timestamp: datetime,
        category: str,
        command: str,
        error: str,
        context: dict[str, Any],
    ):
        """Initialize error log entry.

        Args:
            timestamp: When error occurred
            category: Error category (tunnel, database, file, network, config)
            command: Command that failed
            error: Error message
            context: Additional context (server, path, params, etc.)
        """
        self.timestamp = timestamp
        self.category = category
        self.command = command
        self.error = error
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "category": self.category,
            "command": self.command,
            "error": self.error,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ErrorLog":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            category=data["category"],
            command=data["command"],
            error=data["error"],
            context=data.get("context", {}),
        )


class AIContextManager:
    """Manages AI context including error logs and system state."""

    MAX_ERROR_LOGS = 100  # Keep last 100 errors

    def __init__(self, config_dir: Path | None = None):
        """Initialize AI context manager.

        Args:
            config_dir: Configuration directory (default: ~/.navig/)
        """
        if config_dir is None:
            config_dir = paths.config_dir()

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.error_log_file = self.config_dir / "error_log.json"
        self.error_logs: list[ErrorLog] = []

        self._load_error_logs()

    def _load_error_logs(self):
        """Load error logs from file."""
        if not self.error_log_file.exists():
            self.error_logs = []
            return

        try:
            with open(self.error_log_file, encoding='utf-8') as f:
                data = json.load(f)

            self.error_logs = [ErrorLog.from_dict(entry) for entry in data]

            # Trim to max size
            if len(self.error_logs) > self.MAX_ERROR_LOGS:
                self.error_logs = self.error_logs[-self.MAX_ERROR_LOGS :]
                self._save_error_logs()

        except Exception as e:
            ch.dim(f"Could not load error logs: {e}")
            self.error_logs = []

    def _save_error_logs(self):
        """Save error logs to file."""
        try:
            data = [log.to_dict() for log in self.error_logs]

            with open(self.error_log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            ch.dim(f"Could not save error logs: {e}")

    def log_error(
        self,
        category: str,
        command: str,
        error: str,
        context: dict[str, Any] | None = None,
    ):
        """Log an error for AI context.

        Args:
            category: Error category (tunnel, database, file, network, config)
            command: Command that failed
            error: Error message
            context: Additional context dict
        """
        if context is None:
            context = {}

        error_log = ErrorLog(
            timestamp=datetime.now(),
            category=category,
            command=command,
            error=error,
            context=context,
        )

        self.error_logs.append(error_log)

        # Trim to max size
        if len(self.error_logs) > self.MAX_ERROR_LOGS:
            self.error_logs = self.error_logs[-self.MAX_ERROR_LOGS :]

        self._save_error_logs()

    def get_recent_errors(
        self, hours: int = 24, category: str | None = None, limit: int = 50
    ) -> list[ErrorLog]:
        """Get recent errors for AI context.

        Args:
            hours: Look back this many hours
            category: Filter by category (None = all)
            limit: Maximum number of errors to return

        Returns:
            List of ErrorLog instances
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        # Filter by time
        recent = [log for log in self.error_logs if log.timestamp >= cutoff]

        # Filter by category if specified
        if category:
            recent = [log for log in recent if log.category == category]

        # Sort by timestamp (newest first)
        recent.sort(key=lambda x: x.timestamp, reverse=True)

        # Limit results
        return recent[:limit]

    def get_error_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get error summary for AI context.

        Args:
            hours: Look back this many hours

        Returns:
            Summary dict with counts, categories, common errors
        """
        recent = self.get_recent_errors(hours=hours, limit=1000)

        if not recent:
            return {
                "total_errors": 0,
                "time_range_hours": hours,
                "categories": {},
                "common_errors": [],
                "recent_errors": [],
            }

        # Count by category
        categories = {}
        for log in recent:
            cat = log.category
            if cat not in categories:
                categories[cat] = 0
            categories[cat] += 1

        # Find common error patterns
        error_counts = {}
        for log in recent:
            # Normalize error message (remove specifics like paths, ports)
            error_key = log.error[:100]  # First 100 chars
            if error_key not in error_counts:
                error_counts[error_key] = {
                    "count": 0,
                    "example": log.error,
                    "category": log.category,
                }
            error_counts[error_key]["count"] += 1

        # Sort by frequency
        common_errors = sorted(error_counts.values(), key=lambda x: x["count"], reverse=True)[:10]

        return {
            "total_errors": len(recent),
            "time_range_hours": hours,
            "categories": categories,
            "common_errors": common_errors,
            "recent_errors": [log.to_dict() for log in recent[:10]],
        }

    def get_command_suggestions(self, failed_command: str, error: str) -> list[str]:
        """Suggest fixes based on failed command and error.

        Args:
            failed_command: Command that failed
            error: Error message

        Returns:
            List of suggestion strings
        """
        suggestions = []
        error_lower = error.lower()

        # Tunnel-related suggestions
        if "tunnel" in failed_command.lower() or "tunnel" in error_lower:
            if "connection refused" in error_lower or "could not connect" in error_lower:
                suggestions.extend(
                    [
                        "Check if SSH server is running: navig run 'systemctl status sshd'",
                        "Verify server credentials: navig server current",
                        "Test SSH connection: navig run 'echo test'",
                        "Check firewall: navig run 'sudo ufw status'",
                    ]
                )

            if "port" in error_lower or "address already in use" in error_lower:
                suggestions.extend(
                    [
                        "Check tunnel status: navig tunnel status",
                        "Stop existing tunnel: navig tunnel stop",
                        "List processes on port: navig run 'lsof -i :3306'",
                        "Restart tunnel (auto-increments port): navig tunnel restart",
                    ]
                )

            if "timeout" in error_lower:
                suggestions.extend(
                    [
                        "Check network connectivity: ping <server_ip>",
                        "Verify SSH port is accessible: telnet <server_ip> 22",
                        "Check server firewall rules",
                        "Increase timeout: navig --verbose tunnel start",
                    ]
                )

        # Database-related suggestions
        if any(cmd in failed_command.lower() for cmd in ["sql", "backup", "restore", "database"]):
            if "access denied" in error_lower or "authentication" in error_lower:
                suggestions.extend(
                    [
                        "Verify database credentials in server config",
                        "Test database connection: navig sql 'SELECT 1'",
                        "Check MySQL user permissions: navig sql 'SHOW GRANTS'",
                        "Reset database password in config: ~/.navig/apps/<server>.yaml",
                    ]
                )

            if "tunnel" in error_lower or "connection" in error_lower:
                suggestions.extend(
                    [
                        "Start tunnel first: navig tunnel start",
                        "Check tunnel status: navig tunnel status",
                        "Verify tunnel port: navig tunnel status --json | jq .local_port",
                    ]
                )

            if "disk" in error_lower or "space" in error_lower:
                suggestions.extend(
                    [
                        "Check disk space: navig run 'df -h'",
                        "Clean old backups: navig run 'du -sh ~/.navig/backups/*'",
                        "Check database size: navig sql 'SELECT table_schema, SUM(data_length + index_length) FROM information_schema.tables GROUP BY table_schema'",
                    ]
                )

        # File operation suggestions
        if any(cmd in failed_command.lower() for cmd in ["upload", "download", "list", "delete"]):
            if "permission denied" in error_lower:
                suggestions.extend(
                    [
                        "Check file permissions: navig run 'ls -la <path>'",
                        "Check file ownership: navig run 'stat <path>'",
                        "Change ownership: navig chown www-data:www-data <path>",
                        "Change permissions: navig chmod 755 <path>",
                    ]
                )

            if "no such file" in error_lower or "not found" in error_lower:
                suggestions.extend(
                    [
                        "List directory: navig list <parent_dir>",
                        "Check web root: navig run 'ls -la /var/www/html'",
                        "Verify path in server config: navig server current",
                    ]
                )

            if "connection" in error_lower:
                suggestions.extend(
                    [
                        "Test SSH connection: navig run 'pwd'",
                        "Restart SSH service: navig restart sshd",
                        "Check server status: navig health",
                    ]
                )

        # Config/server suggestions
        if "config" in error_lower or "server" in error_lower:
            suggestions.extend(
                [
                    "List available servers: navig server list",
                    "Check active server: navig server current",
                    "Inspect server config: cat ~/.navig/apps/<server>.yaml",
                    "Validate server setup: navig server inspect",
                ]
            )

        # Generic suggestions if no specific matches
        if not suggestions:
            suggestions.extend(
                [
                    "Check server health: navig health",
                    "Review recent logs: navig logs nginx --tail",
                    "Enable verbose mode: navig --verbose <command>",
                    "Check for recent errors: Review ~/.navig/error_log.json",
                ]
            )

        return suggestions[:5]  # Return top 5 suggestions

    def clear_old_errors(self, days: int = 30):
        """Clear errors older than specified days.

        Args:
            days: Remove errors older than this many days
        """
        cutoff = datetime.now() - timedelta(days=days)

        original_count = len(self.error_logs)
        self.error_logs = [log for log in self.error_logs if log.timestamp >= cutoff]
        removed_count = original_count - len(self.error_logs)

        if removed_count > 0:
            self._save_error_logs()
            ch.success(f"✓ Cleared {removed_count} old error(s)")
        else:
            ch.info("No old errors to clear")


# Global instance for easy access
_ai_context_manager = None


def get_ai_context_manager() -> AIContextManager:
    """Get global AI context manager instance."""
    global _ai_context_manager
    if _ai_context_manager is None:
        _ai_context_manager = AIContextManager()
    return _ai_context_manager


def log_error(category: str, command: str, error: str, context: dict[str, Any] | None = None):
    """Convenience function to log error.

    Args:
        category: Error category (tunnel, database, file, network, config)
        command: Command that failed
        error: Error message
        context: Additional context dict
    """
    manager = get_ai_context_manager()
    manager.log_error(category, command, error, context)
