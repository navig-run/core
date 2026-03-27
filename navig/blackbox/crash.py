"""NAVIG Blackbox Crash Recorder — capture and persist crash reports.

Crash reports are written to ~/.navig/blackbox/crashes/ as JSON files.
``install_crash_handler()`` installs a global ``sys.excepthook`` so any
unhandled exception is automatically captured before the process exits.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["CrashReport", "record_crash", "install_crash_handler", "list_crashes"]


@dataclass
class CrashReport:
    """A single crash record."""

    timestamp: str
    signal_name: str
    exception_type: str
    exception_msg: str
    traceback_str: str
    daemon_pid: int
    memory_mb: float
    navig_version: str
    recent_commands: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CrashReport:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def record_crash(
    exc: BaseException | None = None,
    signal_name: str = "exception",
    context: dict | None = None,
    blackbox_dir: Path | None = None,
) -> CrashReport:
    """Record a crash report to disk.

    Parameters
    ----------
    exc          : The exception to record (uses ``sys.exc_info()`` if None).
    signal_name  : Signal that triggered the crash (e.g. "SIGABRT", "exception").
    context      : Extra key-value context to attach.
    blackbox_dir : Override storage directory.

    Returns
    -------
    CrashReport
    """
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()

    crash_dir = blackbox_dir / "crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)

    # Exception info
    if exc is None:
        exc_type, exc_val, exc_tb = sys.exc_info()
    else:
        exc_type = type(exc)
        exc_val = exc
        exc_tb = exc.__traceback__

    tb_str = "".join(traceback.format_exception(exc_type, exc_val, exc_tb)) if exc_type else ""

    # Memory usage
    mem_mb = 0.0
    try:
        import psutil  # noqa: PLC0415

        mem_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # NAVIG version
    try:
        from navig import __version__  # type: ignore[attr-defined]
    except Exception:
        __version__ = "unknown"  # type: ignore[assignment]

    # Recent commands from recorder
    recent: list[str] = []
    try:
        from .recorder import get_recorder
        from .types import EventType

        events = get_recorder().read_events(limit=10, event_type=EventType.COMMAND)
        recent = [e.payload.get("command", "") for e in events if e.payload.get("command")]
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    report = CrashReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        signal_name=signal_name,
        exception_type=exc_type.__name__ if exc_type else "Unknown",
        exception_msg=str(exc_val) if exc_val else "",
        traceback_str=tb_str,
        daemon_pid=os.getpid(),
        memory_mb=round(mem_mb, 2),
        navig_version=__version__,
        recent_commands=recent,
        context=context or {},
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = crash_dir / f"crash-{ts}-{os.getpid()}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    return report


def install_crash_handler(blackbox_dir: Path | None = None) -> None:
    """Install a global exception hook that records crashes to blackbox.

    Call this once at daemon/CLI startup.
    """

    def _hook(
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb,
    ) -> None:
        record_crash(exc=exc_val, blackbox_dir=blackbox_dir)
        # Call original hook (shows traceback to terminal)
        sys.__excepthook__(exc_type, exc_val, exc_tb)

    sys.excepthook = _hook

    # POSIX signal for daemon use
    try:
        import signal

        def _sigterm(signum, frame):  # noqa: ANN001
            record_crash(
                signal_name=f"SIG{signum}",
                context={"signum": signum},
                blackbox_dir=blackbox_dir,
            )

        signal.signal(signal.SIGTERM, _sigterm)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


def list_crashes(blackbox_dir: Path | None = None) -> list[CrashReport]:
    """Return all crash reports sorted newest first."""
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()

    crash_dir = blackbox_dir / "crashes"
    if not crash_dir.exists():
        return []

    reports: list[CrashReport] = []
    for path in sorted(crash_dir.glob("crash-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            reports.append(CrashReport.from_dict(data))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    return reports
