"""
Module 4: AI Copilot Integration

Generate comprehensive context summaries for external AI assistants:
- Enhanced context building from multiple sources
- Structured JSON output schema
- Export commands for clipboard/file
"""

import json
import platform
from datetime import datetime, timedelta
from typing import Any


class ContextGenerator:
    """
    Generate comprehensive context for AI copilot integration.
    """

    def __init__(self, assistant):
        """
        Initialize context generator module.

        Args:
            assistant: ProactiveAssistant instance
        """
        self.assistant = assistant
        self.ai_context_dir = assistant.ai_context_dir
        self.config = assistant.assistant_config

    def generate_context_summary(
        self, config_manager, remote_ops=None
    ) -> dict[str, Any]:
        """
        Generate comprehensive context summary for AI assistants.

        Args:
            config_manager: ConfigManager instance
            remote_ops: Optional RemoteOperations instance for live data

        Returns:
            Structured context dictionary
        """
        context = {
            "generated_at": datetime.now().isoformat(),
            "navig_version": self._get_navig_version(),
            "client_platform": platform.system(),
        }

        # Server information
        server_config = None
        try:
            server_name = config_manager.get_active_server()
            if server_name:
                server_config = config_manager.load_server_config(server_name)
                context["server"] = self._build_server_context(
                    server_config, remote_ops
                )
        except Exception as e:
            context["server"] = {"error": str(e)}

        # Services status
        if remote_ops and server_config:
            context["services"] = self._get_services_status(remote_ops, server_config)

        # Resource usage
        if remote_ops and server_config:
            context["resource_usage"] = self._get_resource_usage(
                remote_ops, server_config
            )

        # Recent operations
        context["recent_operations"] = self._get_recent_operations(limit=20)

        # Active issues
        context["active_issues"] = self._get_active_issues()

        # Recent errors
        # void: we feed the AI our failures. it learns. we hope.
        context["recent_errors"] = self._get_recent_errors(hours=24)

        # Context summary (human-readable)
        context["context_summary"] = self._generate_summary(context)

        return context

    def _get_navig_version(self) -> str:
        """Get NAVIG version."""
        try:
            from navig import __version__

            return __version__
        except (ImportError, AttributeError):
            return "unknown"

    def _build_server_context(
        self, server_config: dict[str, Any], remote_ops=None
    ) -> dict[str, Any]:
        """Build server context information."""
        server_ctx = {
            "name": server_config.get("name"),
            "host": server_config.get("host"),
            "user": server_config.get("user"),
            "environment": server_config.get("environment", "unknown"),
        }

        # Get live data if remote_ops available
        if remote_ops:
            try:
                # OS version
                os_result = remote_ops.execute_command(
                    "cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2",
                    server_config,
                )
                if os_result.returncode == 0:
                    server_ctx["os"] = os_result.stdout.strip()

                # Uptime
                uptime_result = remote_ops.execute_command("uptime -p", server_config)
                if uptime_result.returncode == 0:
                    server_ctx["uptime"] = uptime_result.stdout.strip()

                # Last backup (if available)
                # This would integrate with backup system
                server_ctx["last_backup"] = "unknown"

            except Exception as e:
                server_ctx["live_data_error"] = str(e)

        return server_ctx

    def _get_services_status(
        self, remote_ops, server_config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Get status of common services."""
        services = ["nginx", "mysql", "php-fpm", "redis", "postgresql"]
        services_status = []

        for service in services:
            try:
                result = remote_ops.execute_command(
                    f"systemctl is-active {service}", server_config
                )
                if result.returncode == 0 and result.stdout.strip() == "active":
                    # Get uptime
                    uptime_result = remote_ops.execute_command(
                        f"systemctl show {service} --property=ActiveEnterTimestamp --value",
                        server_config,
                    )

                    services_status.append(
                        {
                            "name": service,
                            "status": "running",
                            "uptime": (
                                uptime_result.stdout.strip()
                                if uptime_result.returncode == 0
                                else "unknown"
                            ),
                        }
                    )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        return services_status

    def _get_resource_usage(
        self, remote_ops, server_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Get current resource usage."""
        try:
            # Use auto_detection module if available
            if hasattr(self.assistant, "auto_detection"):
                metrics = self.assistant.auto_detection.collect_performance_metrics(
                    remote_ops, server_config
                )
                return metrics
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return {"status": "unavailable"}

    def _get_recent_operations(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent command operations."""
        history_file = self.ai_context_dir / "command_history.json"

        try:
            if not history_file.exists():
                return []

            with open(history_file) as f:
                history = json.load(f)

            # Return last N entries
            return history[-limit:] if len(history) > limit else history

        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _get_active_issues(self) -> list[dict[str, Any]]:
        """Get active detected issues."""
        issues_file = self.ai_context_dir / "detected_issues.json"

        try:
            if not issues_file.exists():
                return []

            with open(issues_file) as f:
                issues = json.load(f)

            # Filter for active issues from last 24 hours
            cutoff = datetime.now() - timedelta(hours=24)
            active = [
                i
                for i in issues
                if i.get("status") == "active"
                and datetime.fromisoformat(i["timestamp"]) >= cutoff
            ]

            return active

        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return []

    def _get_recent_errors(self, hours: int = 24) -> list[dict[str, Any]]:
        """Get recent errors."""
        # Use error_resolution module if available
        if hasattr(self.assistant, "error_resolution"):
            stats = self.assistant.error_resolution.get_error_statistics(hours=hours)
            return stats.get("recent_errors", [])

        return []

    def _generate_summary(self, context: dict[str, Any]) -> str:
        """Generate human-readable context summary."""
        lines = []

        # Server info
        if "server" in context and "name" in context["server"]:
            server = context["server"]
            lines.append(
                f"Managing server: {server.get('name')} ({server.get('host')})"
            )
            if "os" in server:
                lines.append(f"OS: {server['os']}")

        # Services
        if "services" in context and context["services"]:
            running_services = [
                s["name"] for s in context["services"] if s.get("status") == "running"
            ]
            if running_services:
                lines.append(f"Running services: {', '.join(running_services)}")

        # Resource status
        if "resource_usage" in context:
            usage = context["resource_usage"]
            if "status" in usage and usage["status"] != "unavailable":
                lines.append(f"Resource status: {usage.get('status', 'unknown')}")

        # Issues
        if "active_issues" in context and context["active_issues"]:
            lines.append(f"Active issues: {len(context['active_issues'])}")

        # Recent activity
        if "recent_operations" in context:
            lines.append(f"Recent operations: {len(context['recent_operations'])}")

        return ". ".join(lines) if lines else "No context available"
