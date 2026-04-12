"""
Module 1: Auto-Detection & Analysis

Background monitoring system that detects issues before they become critical:
- Command execution monitoring
- Log file analysis
- Performance baseline tracking
- Configuration analysis
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from navig import console_helper as ch


class AutoDetection:
    """
    Automatic detection and analysis of system issues.
    """

    def __init__(self, assistant):
        """
        Initialize auto-detection module.

        Args:
            assistant: ProactiveAssistant instance
        """
        self.assistant = assistant
        self.ai_context_dir = assistant.ai_context_dir
        self.config = assistant.assistant_config

    def log_command_execution(
        self,
        command: str,
        exit_code: int,
        stderr: str = "",
        stdout: str = "",
        duration: float = 0.0,
        context: dict[str, Any] | None = None,
    ):
        """
        Log command execution for monitoring and analysis.

        Args:
            command: Command that was executed
            exit_code: Exit code (0 = success)
            stderr: Standard error output
            stdout: Standard output
            duration: Execution duration in seconds
            context: Additional context (server, user, etc.)
        """
        history_file = self.ai_context_dir / "command_history.json"

        try:
            # Load existing history
            if history_file.exists():
                with open(history_file) as f:
                    history = json.load(f)
            else:
                history = []

            # Create entry
            entry = {
                "timestamp": datetime.now().isoformat(),
                "command": command,
                "exit_code": exit_code,
                "duration_seconds": duration,
                "success": exit_code == 0,
                "context": context or {},
            }

            # Add stderr/stdout only if there was an error or in verbose mode
            # void: we log failures. we learn from them. or we repeat them.
            if exit_code != 0:
                entry["stderr"] = stderr[:500]  # Limit size
                entry["stdout"] = stdout[:500]

            # Append entry
            history.append(entry)

            # Rotate if exceeds max entries
            max_entries = self.config.get("max_history_entries", 1000)
            if len(history) > max_entries:
                history = history[-max_entries:]

            # Save
            _tmp_path: Path | None = None
            try:
                _fd, _tmp = tempfile.mkstemp(dir=history_file.parent, suffix=".tmp")
                _tmp_path = Path(_tmp)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    json.dump(history, _fh, indent=2)
                os.replace(_tmp_path, history_file)
                _tmp_path = None
            finally:
                if _tmp_path is not None:
                    _tmp_path.unlink(missing_ok=True)

            # Trigger analysis if command failed
            if exit_code != 0 and self.assistant.should_auto_analyze():
                self._analyze_failure(command, exit_code, stderr, context)

        except Exception as e:
            ch.dim(f"Could not log command execution: {e}")

    def _analyze_failure(
        self,
        command: str,
        exit_code: int,
        stderr: str,
        context: dict[str, Any] | None,
    ):
        """
        Analyze command failure and detect issues.

        Args:
            command: Failed command
            exit_code: Exit code
            stderr: Error output
            context: Execution context
        """
        # Detect error category
        category = self._categorize_error(stderr)

        # Check if this is a known pattern
        patterns = self._load_error_patterns()
        matched_pattern = None

        for pattern in patterns:
            if re.search(pattern["pattern"], stderr, re.IGNORECASE):
                matched_pattern = pattern
                break

        # Log detected issue
        if matched_pattern:
            severity = matched_pattern.get("severity", "medium")
            self._log_detected_issue(
                category=category,
                severity=severity,
                description=f"Command failed: {command}",
                error_message=stderr[:200],
                context=context,
            )

    def _categorize_error(self, error_message: str) -> str:
        """
        Categorize error based on message content.

        Returns:
            Category: permission, network, configuration, resource_exhaustion,
                     dependency_missing, syntax, unknown
        """
        error_lower = error_message.lower()

        if any(kw in error_lower for kw in ["permission denied", "access denied", "forbidden"]):
            return "permission"
        elif any(
            kw in error_lower for kw in ["connection refused", "timeout", "network", "unreachable"]
        ):
            return "network"
        elif any(kw in error_lower for kw in ["disk full", "no space", "out of memory", "oom"]):
            return "resource_exhaustion"
        elif any(kw in error_lower for kw in ["not found", "no such file", "missing"]):
            return "dependency_missing"
        elif any(kw in error_lower for kw in ["syntax error", "parse error", "invalid syntax"]):
            return "syntax"
        elif any(kw in error_lower for kw in ["config", "configuration"]):
            return "configuration"
        else:
            return "unknown"

    def _load_error_patterns(self) -> list[dict[str, Any]]:
        """Load error patterns from JSON file."""
        patterns_file = self.ai_context_dir / "error_patterns.json"

        try:
            if patterns_file.exists():
                with open(patterns_file) as f:
                    return json.load(f)
        except Exception:  # noqa: BLE001
            ch.dim("auto_detection: failed to load error_patterns.json")  # best-effort; failure is non-critical

        return []

    def _log_detected_issue(
        self,
        category: str,
        severity: str,
        description: str,
        error_message: str,
        context: dict[str, Any] | None,
    ):
        """Log a detected issue."""
        issues_file = self.ai_context_dir / "detected_issues.json"

        try:
            # Load existing issues
            if issues_file.exists():
                with open(issues_file) as f:
                    issues = json.load(f)
            else:
                issues = []

            # Create issue entry
            issue = {
                "timestamp": datetime.now().isoformat(),
                "category": category,
                "severity": severity,
                "description": description,
                "error_message": error_message,
                "context": context or {},
                "status": "active",
            }

            issues.append(issue)

            # Save
            _tmp_path2: Path | None = None
            try:
                _fd2, _tmp2 = tempfile.mkstemp(dir=issues_file.parent, suffix=".tmp")
                _tmp_path2 = Path(_tmp2)
                with os.fdopen(_fd2, "w", encoding="utf-8") as _fh2:
                    json.dump(issues, _fh2, indent=2)
                os.replace(_tmp_path2, issues_file)
                _tmp_path2 = None
            finally:
                if _tmp_path2 is not None:
                    _tmp_path2.unlink(missing_ok=True)

        except Exception as e:
            ch.dim(f"Could not log detected issue: {e}")

    def collect_performance_metrics(
        self, remote_ops, server_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Collect current performance metrics from remote server.

        Args:
            remote_ops: RemoteOperations instance
            server_config: Server configuration dictionary

        Returns:
            Dict with CPU, memory, disk, network metrics
        """
        try:
            # Get CPU usage
            cpu_result = remote_ops.execute_command(
                "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1",
                server_config,
            )
            cpu_percent = float(cpu_result.stdout.strip()) if cpu_result.returncode == 0 else 0.0

            # Get memory usage
            mem_result = remote_ops.execute_command(
                "free | grep Mem | awk '{print ($3/$2) * 100.0}'", server_config
            )
            memory_percent = float(mem_result.stdout.strip()) if mem_result.returncode == 0 else 0.0

            # Get disk usage
            disk_result = remote_ops.execute_command(
                "df -h / | tail -1 | awk '{print $5}' | cut -d'%' -f1", server_config
            )
            disk_percent = float(disk_result.stdout.strip()) if disk_result.returncode == 0 else 0.0

            metrics = {
                "timestamp": datetime.now().isoformat(),
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "disk_percent": disk_percent,
                "status": "normal",
            }

            # Check thresholds
            # void: thresholds are arbitrary. but they give us early warnings. better than nothing.
            thresholds = self.config.get("thresholds", {})
            alerts = []

            if cpu_percent >= thresholds.get("cpu_critical", 95):
                alerts.append(f"CPU usage critical: {cpu_percent}%")
                metrics["status"] = "critical"
            elif cpu_percent >= thresholds.get("cpu_warning", 80):
                alerts.append(f"CPU usage high: {cpu_percent}%")
                if metrics["status"] == "normal":
                    metrics["status"] = "warning"

            if memory_percent >= thresholds.get("memory_critical", 95):
                alerts.append(f"Memory usage critical: {memory_percent}%")
                metrics["status"] = "critical"
            elif memory_percent >= thresholds.get("memory_warning", 80):
                alerts.append(f"Memory usage high: {memory_percent}%")
                if metrics["status"] == "normal":
                    metrics["status"] = "warning"

            if disk_percent >= thresholds.get("disk_critical", 90):
                alerts.append(f"Disk usage critical: {disk_percent}%")
                metrics["status"] = "critical"
            elif disk_percent >= thresholds.get("disk_warning", 80):
                alerts.append(f"Disk usage high: {disk_percent}%")
                if metrics["status"] == "normal":
                    metrics["status"] = "warning"

            metrics["alerts"] = alerts

            return metrics

        except Exception as e:
            ch.dim(f"Could not collect performance metrics: {e}")
            return {
                "timestamp": datetime.now().isoformat(),
                "status": "error",
                "error": str(e),
            }

    def update_performance_baseline(self, server_name: str, metrics: dict[str, Any]):
        """
        Update performance baseline for a server.

        Args:
            server_name: Server name
            metrics: Current metrics
        """
        baselines_dir = self.assistant.navig_dir / "baselines"
        baseline_file = baselines_dir / f"{server_name}.json"

        try:
            # Load existing baseline
            if baseline_file.exists():
                with open(baseline_file) as f:
                    baseline = json.load(f)
            else:
                baseline = {
                    "server": server_name,
                    "created_at": datetime.now().isoformat(),
                    "metrics_history": [],
                }

            # Add current metrics
            baseline["metrics_history"].append(metrics)
            baseline["updated_at"] = datetime.now().isoformat()

            # Keep only last 2016 entries (7 days at 5-minute intervals)
            if len(baseline["metrics_history"]) > 2016:
                baseline["metrics_history"] = baseline["metrics_history"][-2016:]

            # Calculate rolling averages
            baseline["averages"] = self._calculate_averages(baseline["metrics_history"])

            # Save
            _tmp_path3: Path | None = None
            try:
                _fd3, _tmp3 = tempfile.mkstemp(dir=baseline_file.parent, suffix=".tmp")
                _tmp_path3 = Path(_tmp3)
                with os.fdopen(_fd3, "w", encoding="utf-8") as _fh3:
                    json.dump(baseline, _fh3, indent=2)
                os.replace(_tmp_path3, baseline_file)
                _tmp_path3 = None
            finally:
                if _tmp_path3 is not None:
                    _tmp_path3.unlink(missing_ok=True)

        except Exception as e:
            ch.dim(f"Could not update performance baseline: {e}")

    def _calculate_averages(self, metrics_history: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate rolling averages from metrics history."""
        if not metrics_history:
            return {}

        now = datetime.now()

        # Filter for different time windows
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)
        one_week_ago = now - timedelta(days=7)

        def avg_for_window(start_time):
            window_metrics = [
                m for m in metrics_history if datetime.fromisoformat(m["timestamp"]) >= start_time
            ]
            if not window_metrics:
                return None

            return {
                "cpu": sum(m.get("cpu_percent", 0) for m in window_metrics) / len(window_metrics),
                "memory": sum(m.get("memory_percent", 0) for m in window_metrics)
                / len(window_metrics),
                "disk": sum(m.get("disk_percent", 0) for m in window_metrics) / len(window_metrics),
            }

        return {
            "1_hour": avg_for_window(one_hour_ago),
            "24_hours": avg_for_window(one_day_ago),
            "7_days": avg_for_window(one_week_ago),
        }
