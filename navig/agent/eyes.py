"""
Eyes - System Monitoring Component

The Eyes observe the system and environment:
- System metrics (CPU, memory, disk)
- Log file watching
- File system monitoring
- Service status tracking
- Anomaly detection
"""

from __future__ import annotations

import asyncio
import os
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.agent.component import Component
from navig.agent.config import EyesConfig
from navig.agent.nervous_system import EventPriority, EventType, NervousSystem


@dataclass
class SystemMetrics:
    """System resource metrics."""

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    load_average: tuple = (0.0, 0.0, 0.0)
    network_bytes_sent: int = 0
    network_bytes_recv: int = 0
    process_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "memory_used_mb": self.memory_used_mb,
            "disk_percent": self.disk_percent,
            "disk_used_gb": self.disk_used_gb,
            "load_average": self.load_average,
            "network_bytes_sent": self.network_bytes_sent,
            "network_bytes_recv": self.network_bytes_recv,
            "process_count": self.process_count,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Alert:
    """System alert."""

    level: str  # info, warning, critical
    category: str  # cpu, memory, disk, service, log
    message: str
    value: Any = None
    threshold: Any = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "category": self.category,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp.isoformat(),
        }


class Eyes(Component):
    """
    System monitoring component.

    The Eyes continuously observe:
    - System resource usage
    - Log files for patterns/errors
    - File system changes
    - Service health

    When thresholds are exceeded or anomalies detected,
    the Eyes emit alerts through the nervous system.
    """

    def __init__(
        self,
        config: EyesConfig,
        nervous_system: Optional[NervousSystem] = None,
    ):
        super().__init__("eyes", nervous_system)
        self.config = config

        # Monitoring tasks
        self._monitoring_task: Optional[asyncio.Task] = None
        self._log_watcher_task: Optional[asyncio.Task] = None
        self._file_watcher_task: Optional[asyncio.Task] = None

        # State
        self._last_metrics: Optional[SystemMetrics] = None
        self._alerts: List[Alert] = []
        self._max_alerts = 100
        self._watched_files: Dict[str, float] = {}  # path -> last_mtime

        # Try to import psutil
        self._psutil: Optional[Any] = None
        try:
            import psutil

            self._psutil = psutil
        except ImportError:
            pass  # optional dependency not installed; feature disabled

    async def _on_start(self) -> None:
        """Start monitoring tasks."""
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())

        if self.config.log_paths:
            self._log_watcher_task = asyncio.create_task(self._log_watcher_loop())

        if self.config.watch_paths:
            self._file_watcher_task = asyncio.create_task(self._file_watcher_loop())

    async def _on_stop(self) -> None:
        """Stop monitoring tasks."""
        for task in [
            self._monitoring_task,
            self._log_watcher_task,
            self._file_watcher_task,
        ]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown

    async def _on_health_check(self) -> Dict[str, Any]:
        """Health check for eyes."""
        return {
            "last_metrics": (
                self._last_metrics.to_dict() if self._last_metrics else None
            ),
            "alert_count": len(self._alerts),
            "watched_logs": len(self.config.log_paths),
            "watched_files": len(self.config.watch_paths),
            "psutil_available": self._psutil is not None,
        }

    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        while True:
            try:
                await asyncio.sleep(self.config.monitoring_interval)

                # Collect metrics
                metrics = await self.collect_metrics()
                self._last_metrics = metrics

                # Emit metrics event
                await self.emit(
                    EventType.METRIC_COLLECTED, {"metrics": metrics.to_dict()}
                )

                # Check thresholds and emit alerts
                await self._check_thresholds(metrics)

            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        metrics = SystemMetrics()

        if self._psutil:
            try:
                # CPU
                metrics.cpu_percent = self._psutil.cpu_percent(interval=0.1)

                # Memory
                mem = self._psutil.virtual_memory()
                metrics.memory_percent = mem.percent
                metrics.memory_used_mb = mem.used / (1024 * 1024)

                # Disk
                disk = self._psutil.disk_usage("/")
                metrics.disk_percent = disk.percent
                metrics.disk_used_gb = disk.used / (1024**3)

                # Load average (Unix only)
                if hasattr(os, "getloadavg"):
                    metrics.load_average = os.getloadavg()

                # Network
                net = self._psutil.net_io_counters()
                metrics.network_bytes_sent = net.bytes_sent
                metrics.network_bytes_recv = net.bytes_recv

                # Processes
                metrics.process_count = len(self._psutil.pids())

            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        metrics.timestamp = datetime.now()
        return metrics

    async def _check_thresholds(self, metrics: SystemMetrics) -> None:
        """Check metrics against thresholds and emit alerts."""
        alerts = []

        # CPU threshold
        if metrics.cpu_percent > self.config.cpu_threshold:
            alerts.append(
                Alert(
                    level="warning",
                    category="cpu",
                    message=f"CPU usage is {metrics.cpu_percent:.1f}%",
                    value=metrics.cpu_percent,
                    threshold=self.config.cpu_threshold,
                )
            )

        # Memory threshold
        if metrics.memory_percent > self.config.memory_threshold:
            alerts.append(
                Alert(
                    level="warning",
                    category="memory",
                    message=f"Memory usage is {metrics.memory_percent:.1f}%",
                    value=metrics.memory_percent,
                    threshold=self.config.memory_threshold,
                )
            )

        # Disk threshold
        if metrics.disk_percent > self.config.disk_threshold:
            alerts.append(
                Alert(
                    level="critical" if metrics.disk_percent > 95 else "warning",
                    category="disk",
                    message=f"Disk usage is {metrics.disk_percent:.1f}%",
                    value=metrics.disk_percent,
                    threshold=self.config.disk_threshold,
                )
            )

        # Emit alerts
        for alert in alerts:
            self._alerts.append(alert)

            await self.emit(
                EventType.ALERT_TRIGGERED,
                {"alert": alert.to_dict()},
                priority=(
                    EventPriority.HIGH
                    if alert.level == "critical"
                    else EventPriority.NORMAL
                ),
            )

        # Trim alerts
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts :]

    async def _log_watcher_loop(self) -> None:
        """Watch log files for important entries."""
        # Track file positions
        positions: Dict[str, int] = {}

        # Patterns to watch for
        error_patterns = ["error", "exception", "failed", "critical", "fatal"]

        while True:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                for log_path in self.config.log_paths:
                    path = Path(log_path).expanduser()
                    if not path.exists():
                        continue

                    try:
                        with open(path, "r", errors="ignore") as f:
                            # Seek to last known position
                            if str(path) in positions:
                                f.seek(positions[str(path)])
                            else:
                                # Start from end on first run
                                f.seek(0, 2)
                                positions[str(path)] = f.tell()
                                continue

                            # Read new lines
                            for line in f:
                                line_lower = line.lower()
                                for pattern in error_patterns:
                                    if pattern in line_lower:
                                        await self.emit(
                                            EventType.LOG_ENTRY,
                                            {
                                                "path": str(path),
                                                "line": line.strip()[:500],
                                                "pattern": pattern,
                                            },
                                        )
                                        break

                            positions[str(path)] = f.tell()
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical

            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def _file_watcher_loop(self) -> None:
        """Watch for file changes."""
        while True:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds

                for watch_path in self.config.watch_paths:
                    path = Path(watch_path).expanduser()

                    if path.is_file():
                        await self._check_file_change(path)
                    elif path.is_dir():
                        for file_path in path.rglob("*"):
                            if file_path.is_file():
                                await self._check_file_change(file_path)

            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def _check_file_change(self, path: Path) -> None:
        """Check if a file has changed."""
        try:
            mtime = path.stat().st_mtime
            path_str = str(path)

            if path_str in self._watched_files:
                if mtime != self._watched_files[path_str]:
                    await self.emit(
                        EventType.FILE_CHANGED,
                        {
                            "path": path_str,
                            "previous_mtime": self._watched_files[path_str],
                            "new_mtime": mtime,
                        },
                    )

            self._watched_files[path_str] = mtime
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def get_metrics(self) -> Optional[SystemMetrics]:
        """Get latest metrics."""
        return self._last_metrics

    def get_alerts(self, limit: int = 10) -> List[Alert]:
        """Get recent alerts."""
        return self._alerts[-limit:]

    def get_system_info(self) -> Dict[str, Any]:
        """Get static system information."""
        info = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "hostname": platform.node(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        }

        if self._psutil:
            try:
                info["cpu_count"] = self._psutil.cpu_count()
                info["cpu_count_logical"] = self._psutil.cpu_count(logical=True)
                mem = self._psutil.virtual_memory()
                info["memory_total_gb"] = mem.total / (1024**3)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        return info
