"""
Operations Insights & Analytics for NAVIG

Provides intelligent analytics on operations patterns:
- Usage patterns and trends
- Error analysis and detection
- Host health scoring
- Command efficiency metrics
- Time-based analysis
- Personalized recommendations
- Anomaly detection

Leverages the history system to provide actionable insights.
"""

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig import console_helper as ch

# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================


class InsightType(str, Enum):
    """Types of insights."""

    USAGE = "usage"  # Usage patterns
    ERROR = "error"  # Error analysis
    PERFORMANCE = "performance"  # Performance metrics
    HEALTH = "health"  # Host health
    RECOMMENDATION = "recommendation"  # Suggestions
    ANOMALY = "anomaly"  # Unusual patterns


class Severity(str, Enum):
    """Insight severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class TimeRange(str, Enum):
    """Predefined time ranges for analysis."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    ALL = "all"


@dataclass
class Insight:
    """A single insight from analysis."""

    type: InsightType
    title: str
    description: str
    severity: Severity = Severity.INFO
    data: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class HostScore:
    """Health score for a host."""

    host: str
    score: int  # 0-100
    success_rate: float
    avg_latency_ms: int
    error_count: int
    last_success: str
    last_error: str
    trend: str  # improving, stable, declining


@dataclass
class CommandStats:
    """Statistics for a command."""

    command: str
    count: int
    success_rate: float
    avg_duration_ms: int
    last_used: str
    hosts_used: List[str]


@dataclass
class TimePattern:
    """Time-based usage pattern."""

    hour: int
    day_of_week: int
    count: int
    success_rate: float
    most_common_commands: List[str]


@dataclass
class AnalyticsReport:
    """Complete analytics report."""

    generated_at: str
    time_range: str
    total_operations: int
    unique_hosts: int
    unique_commands: int
    overall_success_rate: float
    insights: List[Insight]
    host_scores: List[HostScore]
    top_commands: List[CommandStats]
    time_patterns: List[TimePattern]
    recommendations: List[str]


# ============================================================================
# INSIGHTS ENGINE
# ============================================================================


class InsightsEngine:
    """
    Analytics engine for NAVIG operations.

    Analyzes history data to provide insights on:
    - Command usage patterns
    - Error frequencies and causes
    - Host health and reliability
    - Performance metrics
    - Anomaly detection
    """

    def __init__(self, config_manager=None):
        from navig.config import get_config_manager

        self.config_manager = config_manager or get_config_manager()

        # History file location
        self.history_dir = Path(self.config_manager.global_config_dir) / "history"
        self.history_file = self.history_dir / "operations.jsonl"

        # Cache for loaded operations
        self._operations: List[Dict[str, Any]] = []
        self._loaded = False

    def _load_history(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> List[Dict[str, Any]]:
        """Load operations from history file."""
        operations = []

        if not self.history_file.exists():
            return operations

        # Determine cutoff date
        cutoff = None
        now = datetime.now()
        if time_range == TimeRange.TODAY:
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif time_range == TimeRange.WEEK:
            cutoff = now - timedelta(days=7)
        elif time_range == TimeRange.MONTH:
            cutoff = now - timedelta(days=30)

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            op = json.loads(line)

                            # Filter by time range
                            if cutoff:
                                op_time = datetime.fromisoformat(
                                    op.get("timestamp", "")
                                )
                                if op_time < cutoff:
                                    continue

                            operations.append(op)
                        except (json.JSONDecodeError, ValueError):
                            continue
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        self._operations = operations
        self._loaded = True
        return operations

    # ========================================================================
    # ANALYSIS METHODS
    # ========================================================================

    def get_usage_stats(self, time_range: TimeRange = TimeRange.WEEK) -> Dict[str, Any]:
        """Get overall usage statistics."""
        ops = self._load_history(time_range)

        if not ops:
            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "success_rate": 0.0,
                "unique_commands": 0,
                "unique_hosts": 0,
            }

        success_count = sum(1 for op in ops if op.get("status") == "success")
        commands = set(
            op.get("command", "").split()[0] for op in ops if op.get("command")
        )
        hosts = set(op.get("host", "") for op in ops if op.get("host"))

        return {
            "total": len(ops),
            "success": success_count,
            "failed": len(ops) - success_count,
            "success_rate": round(success_count / len(ops) * 100, 1) if ops else 0,
            "unique_commands": len(commands),
            "unique_hosts": len(hosts),
        }

    def get_top_commands(
        self, limit: int = 10, time_range: TimeRange = TimeRange.WEEK
    ) -> List[CommandStats]:
        """Get most frequently used commands."""
        ops = self._load_history(time_range)

        # Group by command
        command_data: Dict[str, Dict] = defaultdict(
            lambda: {
                "count": 0,
                "success": 0,
                "durations": [],
                "last_used": "",
                "hosts": set(),
            }
        )

        for op in ops:
            cmd = op.get("command", "")
            if not cmd:
                continue

            # Use first word as command identifier
            cmd_key = cmd.split()[0] if cmd else "unknown"

            data = command_data[cmd_key]
            data["count"] += 1
            if op.get("status") == "success":
                data["success"] += 1
            if op.get("duration_ms"):
                data["durations"].append(op["duration_ms"])
            data["last_used"] = op.get("timestamp", "")
            if op.get("host"):
                data["hosts"].add(op["host"])

        # Convert to CommandStats
        stats = []
        for cmd, data in command_data.items():
            avg_duration = (
                int(statistics.mean(data["durations"])) if data["durations"] else 0
            )
            success_rate = (
                round(data["success"] / data["count"] * 100, 1)
                if data["count"] > 0
                else 0
            )

            stats.append(
                CommandStats(
                    command=cmd,
                    count=data["count"],
                    success_rate=success_rate,
                    avg_duration_ms=avg_duration,
                    last_used=data["last_used"],
                    hosts_used=list(data["hosts"]),
                )
            )

        # Sort by count
        stats.sort(key=lambda x: x.count, reverse=True)
        return stats[:limit]

    def get_host_scores(
        self, time_range: TimeRange = TimeRange.WEEK
    ) -> List[HostScore]:
        """Calculate health scores for each host."""
        ops = self._load_history(time_range)

        # Group by host
        host_data: Dict[str, Dict] = defaultdict(
            lambda: {
                "success": 0,
                "failed": 0,
                "latencies": [],
                "last_success": "",
                "last_error": "",
                "errors": [],
            }
        )

        for op in ops:
            host = op.get("host", "")
            if not host:
                continue

            data = host_data[host]
            if op.get("status") == "success":
                data["success"] += 1
                data["last_success"] = op.get("timestamp", "")
            else:
                data["failed"] += 1
                data["last_error"] = op.get("timestamp", "")
                if op.get("error"):
                    data["errors"].append(op["error"])

            if op.get("duration_ms"):
                data["latencies"].append(op["duration_ms"])

        # Calculate scores
        scores = []
        for host, data in host_data.items():
            total = data["success"] + data["failed"]
            if total == 0:
                continue

            success_rate = data["success"] / total
            avg_latency = (
                int(statistics.mean(data["latencies"])) if data["latencies"] else 0
            )

            # Score calculation (0-100)
            # 60% based on success rate, 40% on latency
            latency_score = max(0, 100 - (avg_latency / 50))  # Penalize latency > 5s
            score = int(success_rate * 60 + (latency_score / 100) * 40)

            # Determine trend (simplified - compare first half vs second half)
            mid = len(data["latencies"]) // 2
            if mid > 0:
                first_half = (
                    statistics.mean(data["latencies"][:mid])
                    if data["latencies"][:mid]
                    else 0
                )
                second_half = (
                    statistics.mean(data["latencies"][mid:])
                    if data["latencies"][mid:]
                    else 0
                )
                if second_half < first_half * 0.9:
                    trend = "improving"
                elif second_half > first_half * 1.1:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            scores.append(
                HostScore(
                    host=host,
                    score=score,
                    success_rate=round(success_rate * 100, 1),
                    avg_latency_ms=avg_latency,
                    error_count=data["failed"],
                    last_success=data["last_success"],
                    last_error=data["last_error"],
                    trend=trend,
                )
            )

        # Sort by score
        scores.sort(key=lambda x: x.score, reverse=True)
        return scores

    def get_time_patterns(
        self, time_range: TimeRange = TimeRange.WEEK
    ) -> List[TimePattern]:
        """Analyze time-based usage patterns."""
        ops = self._load_history(time_range)

        # Group by hour
        hour_data: Dict[int, Dict] = defaultdict(
            lambda: {"count": 0, "success": 0, "commands": []}
        )

        for op in ops:
            try:
                ts = datetime.fromisoformat(op.get("timestamp", ""))
                hour = ts.hour

                data = hour_data[hour]
                data["count"] += 1
                if op.get("status") == "success":
                    data["success"] += 1
                if op.get("command"):
                    data["commands"].append(op["command"].split()[0])
            except ValueError:
                continue

        patterns = []
        for hour, data in sorted(hour_data.items()):
            success_rate = (
                round(data["success"] / data["count"] * 100, 1)
                if data["count"] > 0
                else 0
            )

            # Get most common commands
            cmd_counts = Counter(data["commands"])
            top_cmds = [cmd for cmd, _ in cmd_counts.most_common(3)]

            patterns.append(
                TimePattern(
                    hour=hour,
                    day_of_week=0,  # Simplified
                    count=data["count"],
                    success_rate=success_rate,
                    most_common_commands=top_cmds,
                )
            )

        return patterns

    def detect_anomalies(self, time_range: TimeRange = TimeRange.WEEK) -> List[Insight]:
        """Detect unusual patterns or anomalies."""
        ops = self._load_history(time_range)
        anomalies = []

        if len(ops) < 10:
            return anomalies

        # Check for sudden error spike
        recent_ops = ops[-50:] if len(ops) >= 50 else ops
        older_ops = ops[:-50] if len(ops) >= 50 else []

        if older_ops:
            recent_error_rate = sum(
                1 for op in recent_ops if op.get("status") != "success"
            ) / len(recent_ops)
            older_error_rate = sum(
                1 for op in older_ops if op.get("status") != "success"
            ) / len(older_ops)

            if recent_error_rate > older_error_rate * 2 and recent_error_rate > 0.1:
                anomalies.append(
                    Insight(
                        type=InsightType.ANOMALY,
                        title="Error Rate Spike Detected",
                        description=f"Recent error rate ({recent_error_rate:.1%}) is significantly higher than historical ({older_error_rate:.1%})",
                        severity=Severity.WARNING,
                        data={
                            "recent_rate": recent_error_rate,
                            "historical_rate": older_error_rate,
                        },
                        recommendations=[
                            "Check recent host connectivity",
                            "Review error logs with: navig history --status failed",
                            "Run health checks with: navig heartbeat trigger",
                        ],
                    )
                )

        # Check for unusual command patterns
        cmd_counts = Counter(
            op.get("command", "").split()[0] for op in ops if op.get("command")
        )
        if cmd_counts:
            avg_count = statistics.mean(cmd_counts.values())
            std_count = (
                statistics.stdev(cmd_counts.values()) if len(cmd_counts) > 1 else 0
            )

            for cmd, count in cmd_counts.items():
                if count > avg_count + 2 * std_count and count > 10:
                    anomalies.append(
                        Insight(
                            type=InsightType.ANOMALY,
                            title=f"Unusual Command Frequency: {cmd}",
                            description=f"Command '{cmd}' was used {count} times, significantly more than average ({avg_count:.0f})",
                            severity=Severity.INFO,
                            data={"command": cmd, "count": count, "average": avg_count},
                        )
                    )

        # Check for host going silent
        host_last_seen = {}
        for op in ops:
            host = op.get("host", "")
            if host:
                host_last_seen[host] = op.get("timestamp", "")

        now = datetime.now()
        for host, last_seen in host_last_seen.items():
            try:
                last_dt = datetime.fromisoformat(last_seen)
                days_ago = (now - last_dt).days
                if days_ago > 3:
                    anomalies.append(
                        Insight(
                            type=InsightType.ANOMALY,
                            title=f"Host Inactive: {host}",
                            description=f"No operations on '{host}' for {days_ago} days",
                            severity=(
                                Severity.WARNING if days_ago > 7 else Severity.INFO
                            ),
                            data={
                                "host": host,
                                "last_seen": last_seen,
                                "days_inactive": days_ago,
                            },
                            recommendations=[
                                f"Test connectivity: navig host test {host}",
                                f"Check host status: navig host show {host}",
                            ],
                        )
                    )
            except ValueError:
                continue

        return anomalies

    def get_error_analysis(
        self, time_range: TimeRange = TimeRange.WEEK
    ) -> List[Insight]:
        """Analyze error patterns."""
        ops = self._load_history(time_range)
        insights = []

        # Group errors by type
        error_types: Dict[str, List[Dict]] = defaultdict(list)
        for op in ops:
            if op.get("status") != "success":
                error = op.get("error", "Unknown error")
                # Categorize error
                if "connection" in error.lower() or "ssh" in error.lower():
                    error_types["Connection"].append(op)
                elif "permission" in error.lower() or "denied" in error.lower():
                    error_types["Permission"].append(op)
                elif "timeout" in error.lower():
                    error_types["Timeout"].append(op)
                elif "not found" in error.lower():
                    error_types["Not Found"].append(op)
                else:
                    error_types["Other"].append(op)

        for error_type, ops_list in error_types.items():
            if len(ops_list) >= 3:
                hosts_affected = set(
                    op.get("host", "") for op in ops_list if op.get("host")
                )

                recommendations = []
                if error_type == "Connection":
                    recommendations = [
                        "Check network connectivity",
                        "Verify SSH key configuration",
                        "Test with: navig host test <hostname>",
                    ]
                elif error_type == "Permission":
                    recommendations = [
                        "Check user permissions on remote host",
                        "Verify sudo configuration",
                        "Review command requiring elevated privileges",
                    ]
                elif error_type == "Timeout":
                    recommendations = [
                        "Check remote host load",
                        "Consider increasing timeout settings",
                        "Test network latency",
                    ]

                insights.append(
                    Insight(
                        type=InsightType.ERROR,
                        title=f"Recurring {error_type} Errors",
                        description=f"{len(ops_list)} {error_type.lower()} errors across {len(hosts_affected)} host(s)",
                        severity=(
                            Severity.WARNING if len(ops_list) > 5 else Severity.INFO
                        ),
                        data={
                            "error_type": error_type,
                            "count": len(ops_list),
                            "hosts": list(hosts_affected),
                        },
                        recommendations=recommendations,
                    )
                )

        return insights

    def generate_recommendations(
        self, time_range: TimeRange = TimeRange.WEEK
    ) -> List[str]:
        """Generate personalized recommendations."""
        ops = self._load_history(time_range)
        recommendations = []

        if not ops:
            recommendations.append(
                "Start using NAVIG commands to build your operations history"
            )
            return recommendations

        # Check for hosts without recent health checks
        hosts = set(op.get("host", "") for op in ops if op.get("host"))
        for host in hosts:
            host_ops = [op for op in ops if op.get("host") == host]
            has_health_check = any(
                "health" in op.get("command", "").lower()
                or "heartbeat" in op.get("command", "").lower()
                for op in host_ops
            )
            if not has_health_check:
                recommendations.append(
                    f"Consider setting up health checks for '{host}': navig heartbeat configure"
                )

        # Check for manual repetitive tasks
        cmd_counts = Counter(op.get("command", "") for op in ops if op.get("command"))
        for cmd, count in cmd_counts.most_common(5):
            if count >= 5 and len(cmd.split()) > 2:
                recommendations.append(
                    f'Frequently used command could be a quick action: navig quick add myaction "{cmd}"'
                )
                break

        # Check for missing triggers
        error_ops = [op for op in ops if op.get("status") != "success"]
        if len(error_ops) > 5:
            recommendations.append(
                "Set up auto-remediation triggers: navig trigger add --type health"
            )

        # Check for workflow opportunities
        if len(ops) > 50:
            recommendations.append(
                "Consider creating workflows for complex operations: navig flow add"
            )

        # Check dashboard usage
        has_dashboard = any("dashboard" in op.get("command", "").lower() for op in ops)
        if not has_dashboard:
            recommendations.append(
                "Try the operations dashboard for real-time monitoring: navig dashboard"
            )

        return recommendations[:5]  # Limit to 5 recommendations

    def generate_report(
        self, time_range: TimeRange = TimeRange.WEEK
    ) -> AnalyticsReport:
        """Generate a complete analytics report."""
        ops = self._load_history(time_range)

        usage_stats = self.get_usage_stats(time_range)
        host_scores = self.get_host_scores(time_range)
        top_commands = self.get_top_commands(10, time_range)
        time_patterns = self.get_time_patterns(time_range)

        # Collect all insights
        insights = []
        insights.extend(self.detect_anomalies(time_range))
        insights.extend(self.get_error_analysis(time_range))

        # Generate recommendations
        recommendations = self.generate_recommendations(time_range)

        return AnalyticsReport(
            generated_at=datetime.now().isoformat(),
            time_range=time_range.value,
            total_operations=usage_stats["total"],
            unique_hosts=usage_stats["unique_hosts"],
            unique_commands=usage_stats["unique_commands"],
            overall_success_rate=usage_stats["success_rate"],
            insights=insights,
            host_scores=host_scores,
            top_commands=top_commands,
            time_patterns=time_patterns,
            recommendations=recommendations,
        )


# ============================================================================
# CLI DISPLAY FUNCTIONS
# ============================================================================


def show_insights_summary(
    time_range: str = "week", plain: bool = False, json_out: bool = False
):
    """Show insights summary."""
    from rich.panel import Panel

    engine = InsightsEngine()
    tr = TimeRange(time_range)

    report = engine.generate_report(tr)

    if json_out:
        import json as json_module

        data = {
            "generated_at": report.generated_at,
            "time_range": report.time_range,
            "total_operations": report.total_operations,
            "success_rate": report.overall_success_rate,
            "unique_hosts": report.unique_hosts,
            "unique_commands": report.unique_commands,
            "insights": [
                {"type": i.type.value, "title": i.title, "severity": i.severity.value}
                for i in report.insights
            ],
            "recommendations": report.recommendations,
        }
        print(json_module.dumps(data, indent=2))
        return

    if report.total_operations == 0:
        ch.warning("No operations in history for analysis.")
        ch.info("Use NAVIG commands to build your operations history.")
        return

    if plain:
        print(f"Time Range: {report.time_range}")
        print(f"Total Operations: {report.total_operations}")
        print(f"Success Rate: {report.overall_success_rate}%")
        print(f"Unique Hosts: {report.unique_hosts}")
        print(f"Unique Commands: {report.unique_commands}")
        print(f"Insights: {len(report.insights)}")
        return

    ch.header(f"Operations Insights ({tr.value})")

    # Overview panel
    overview = f"""
[bold]Total Operations:[/bold] {report.total_operations}
[bold]Success Rate:[/bold] {report.overall_success_rate}%
[bold]Unique Hosts:[/bold] {report.unique_hosts}
[bold]Unique Commands:[/bold] {report.unique_commands}
"""
    ch.console.print(Panel(overview.strip(), title="Overview", border_style="blue"))

    # Insights
    if report.insights:
        ch.console.print("\n[bold]Insights & Alerts[/bold]")
        for insight in report.insights:
            icon = (
                "!"
                if insight.severity == Severity.WARNING
                else "!!" if insight.severity == Severity.CRITICAL else "i"
            )
            color = (
                "yellow"
                if insight.severity == Severity.WARNING
                else "red" if insight.severity == Severity.CRITICAL else "blue"
            )
            ch.console.print(f"  [{color}][{icon}][/{color}] {insight.title}")
            ch.console.print(f"      [dim]{insight.description}[/dim]")

    # Recommendations
    if report.recommendations:
        ch.console.print("\n[bold]Recommendations[/bold]")
        for i, rec in enumerate(report.recommendations, 1):
            ch.console.print(f"  {i}. {rec}")


def show_host_health(
    time_range: str = "week", plain: bool = False, json_out: bool = False
):
    """Show host health scores."""
    from rich.table import Table

    engine = InsightsEngine()
    tr = TimeRange(time_range)

    scores = engine.get_host_scores(tr)

    if not scores:
        ch.warning("No host data available for analysis.")
        return

    if json_out:
        import json as json_module

        data = [
            {
                "host": s.host,
                "score": s.score,
                "success_rate": s.success_rate,
                "avg_latency_ms": s.avg_latency_ms,
                "error_count": s.error_count,
                "trend": s.trend,
            }
            for s in scores
        ]
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        for s in scores:
            print(f"{s.host}\t{s.score}\t{s.success_rate}%\t{s.trend}")
        return

    table = Table(title=f"Host Health Scores ({tr.value})")
    table.add_column("Host", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Avg Latency", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Trend")

    for s in scores:
        # Color code score
        if s.score >= 80:
            score_str = f"[green]{s.score}[/green]"
        elif s.score >= 60:
            score_str = f"[yellow]{s.score}[/yellow]"
        else:
            score_str = f"[red]{s.score}[/red]"

        # Trend indicator
        if s.trend == "improving":
            trend_str = "[green]^ Improving[/green]"
        elif s.trend == "declining":
            trend_str = "[red]v Declining[/red]"
        else:
            trend_str = "[dim]- Stable[/dim]"

        table.add_row(
            s.host,
            score_str,
            f"{s.success_rate}%",
            f"{s.avg_latency_ms}ms",
            str(s.error_count),
            trend_str,
        )

    ch.console.print(table)


def show_top_commands(
    limit: int = 10,
    time_range: str = "week",
    plain: bool = False,
    json_out: bool = False,
):
    """Show most used commands."""
    from rich.table import Table

    engine = InsightsEngine()
    tr = TimeRange(time_range)

    stats = engine.get_top_commands(limit, tr)

    if not stats:
        ch.warning("No command data available for analysis.")
        return

    if json_out:
        import json as json_module

        data = [
            {
                "command": s.command,
                "count": s.count,
                "success_rate": s.success_rate,
                "avg_duration_ms": s.avg_duration_ms,
            }
            for s in stats
        ]
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        for s in stats:
            print(f"{s.count}\t{s.command}\t{s.success_rate}%")
        return

    table = Table(title=f"Top Commands ({tr.value})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Command", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Avg Duration", justify="right")
    table.add_column("Hosts", justify="right")

    for i, s in enumerate(stats, 1):
        table.add_row(
            str(i),
            s.command,
            str(s.count),
            f"{s.success_rate}%",
            f"{s.avg_duration_ms}ms",
            str(len(s.hosts_used)),
        )

    ch.console.print(table)


def show_time_patterns(
    time_range: str = "week", plain: bool = False, json_out: bool = False
):
    """Show time-based usage patterns."""

    engine = InsightsEngine()
    tr = TimeRange(time_range)

    patterns = engine.get_time_patterns(tr)

    if not patterns:
        ch.warning("No time pattern data available.")
        return

    if json_out:
        import json as json_module

        data = [
            {
                "hour": p.hour,
                "count": p.count,
                "success_rate": p.success_rate,
                "top_commands": p.most_common_commands,
            }
            for p in patterns
        ]
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        for p in patterns:
            print(f"{p.hour:02d}:00\t{p.count}\t{p.success_rate}%")
        return

    ch.header(f"Usage by Hour ({tr.value})")

    # Find max for scaling
    max_count = max(p.count for p in patterns) if patterns else 1

    # Simple bar chart
    for p in patterns:
        bar_len = int((p.count / max_count) * 30)
        bar = "#" * bar_len
        cmds = ", ".join(p.most_common_commands[:2]) if p.most_common_commands else ""
        ch.console.print(
            f"  {p.hour:02d}:00 |[cyan]{bar}[/cyan] {p.count} ({p.success_rate}%) [dim]{cmds}[/dim]"
        )


def show_anomalies(
    time_range: str = "week", plain: bool = False, json_out: bool = False
):
    """Show detected anomalies."""
    engine = InsightsEngine()
    tr = TimeRange(time_range)

    anomalies = engine.detect_anomalies(tr)

    if not anomalies:
        ch.success("No anomalies detected!")
        return

    if json_out:
        import json as json_module

        data = [
            {
                "type": a.type.value,
                "title": a.title,
                "description": a.description,
                "severity": a.severity.value,
                "recommendations": a.recommendations,
            }
            for a in anomalies
        ]
        print(json_module.dumps(data, indent=2))
        return

    if plain:
        for a in anomalies:
            print(f"{a.severity.value}\t{a.title}")
        return

    ch.header(f"Anomalies Detected ({tr.value})")

    for a in anomalies:
        if a.severity == Severity.CRITICAL:
            color = "red"
            icon = "!!"
        elif a.severity == Severity.WARNING:
            color = "yellow"
            icon = "!"
        else:
            color = "blue"
            icon = "i"

        ch.console.print(f"\n[{color}][{icon}] {a.title}[/{color}]")
        ch.console.print(f"   {a.description}")

        if a.recommendations:
            ch.console.print("   [bold]Recommendations:[/bold]")
            for rec in a.recommendations:
                ch.console.print(f"     - {rec}")


def show_recommendations(
    time_range: str = "week", plain: bool = False, json_out: bool = False
):
    """Show personalized recommendations."""
    engine = InsightsEngine()
    tr = TimeRange(time_range)

    recommendations = engine.generate_recommendations(tr)

    if not recommendations:
        ch.success("No specific recommendations at this time!")
        return

    if json_out:
        import json as json_module

        print(json_module.dumps({"recommendations": recommendations}, indent=2))
        return

    if plain:
        for rec in recommendations:
            print(rec)
        return

    ch.header("Personalized Recommendations")

    for i, rec in enumerate(recommendations, 1):
        ch.console.print(f"  {i}. {rec}")


def generate_report(
    time_range: str = "week",
    output_file: Optional[str] = None,
    json_out: bool = False,
):
    """Generate a full analytics report."""
    engine = InsightsEngine()
    tr = TimeRange(time_range)

    report = engine.generate_report(tr)

    if json_out or output_file:
        import json as json_module

        data = {
            "generated_at": report.generated_at,
            "time_range": report.time_range,
            "summary": {
                "total_operations": report.total_operations,
                "unique_hosts": report.unique_hosts,
                "unique_commands": report.unique_commands,
                "success_rate": report.overall_success_rate,
            },
            "host_scores": [
                {
                    "host": s.host,
                    "score": s.score,
                    "success_rate": s.success_rate,
                    "trend": s.trend,
                }
                for s in report.host_scores
            ],
            "top_commands": [
                {
                    "command": c.command,
                    "count": c.count,
                    "success_rate": c.success_rate,
                }
                for c in report.top_commands
            ],
            "insights": [
                {
                    "type": i.type.value,
                    "title": i.title,
                    "description": i.description,
                    "severity": i.severity.value,
                    "recommendations": i.recommendations,
                }
                for i in report.insights
            ],
            "recommendations": report.recommendations,
        }

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                json_module.dump(data, f, indent=2)
            ch.success(f"Report saved to: {output_file}")
        else:
            print(json_module.dumps(data, indent=2))
        return

    # Full display
    show_insights_summary(time_range)
    ch.console.print("")
    show_host_health(time_range)
    ch.console.print("")
    show_top_commands(5, time_range)
    ch.console.print("")
    show_anomalies(time_range)
