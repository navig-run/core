"""Resource monitor — alert when disk / memory / CPU cross a threshold, into the
existing System category (``system_alert``).

Uses hysteresis so a value hovering on the line can't flap: it alerts once when a
metric rises above ``high``, and only re-arms (optionally announcing recovery)
once it falls back below ``low``. CPU additionally requires ``sustain`` consecutive
over-threshold reads so a momentary spike is ignored. Opt-in via
``monitors.resources.enabled`` (no-op if psutil is unavailable).

``ThresholdTracker`` is pure and unit-tested; only the loop touches psutil.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger("navig.notify")

POLL_S = 60.0


@dataclass
class Thresholds:
    high: float
    low: float
    sustain: int = 1  # consecutive over-high reads required before alerting


class ThresholdTracker:
    """Edge detector with hysteresis. ``update(value)`` → 'alert' | 'clear' | None."""

    def __init__(self, thresholds: Thresholds) -> None:
        self.t = thresholds
        self._over = 0
        self.alerted = False

    def update(self, value: float) -> str | None:
        if value >= self.t.high:
            self._over += 1
            if not self.alerted and self._over >= self.t.sustain:
                self.alerted = True
                return "alert"
        else:
            self._over = 0
            if self.alerted and value <= self.t.low:
                self.alerted = False
                return "clear"
        return None


def _band(high: float, sustain: int = 1) -> Thresholds:
    return Thresholds(high=high, low=max(0.0, high - 10.0), sustain=sustain)


async def run_resource_monitor(config: dict | None = None) -> None:
    """Background task: poll system resources and alert on threshold crossings."""
    try:
        import psutil  # noqa: F401
    except Exception:
        logger.info("resource monitor: psutil unavailable — idle")
        return

    cfg = config or {}
    disk_hi = float(cfg.get("disk_pct", 90))
    mem_hi = float(cfg.get("mem_pct", 90))
    cpu_hi = float(cfg.get("cpu_pct", 95))

    trackers = {
        "disk": ThresholdTracker(_band(disk_hi)),
        "mem": ThresholdTracker(_band(mem_hi)),
        "cpu": ThresholdTracker(_band(cpu_hi, sustain=3)),
    }
    labels = {"disk": "Disk", "mem": "Memory", "cpu": "CPU"}

    from navig.notify import dispatch

    logger.info("resource monitor started (disk≥%s mem≥%s cpu≥%s)", disk_hi, mem_hi, cpu_hi)
    try:
        while True:
            values = await asyncio.to_thread(_sample)
            for key, value in values.items():
                if value is None:
                    continue
                edge = trackers[key].update(value)
                if edge == "alert":
                    await dispatch(
                        "system_alert",
                        f"{labels[key]} high — {value:.0f}%",
                        f"{labels[key]} usage crossed {int(trackers[key].t.high)}% on this machine.",
                        priority="high",
                        data={"metric": key, "value": value},
                    )
                elif edge == "clear":
                    await dispatch(
                        "system_alert",
                        f"{labels[key]} back to normal — {value:.0f}%",
                        f"{labels[key]} usage recovered below {int(trackers[key].t.low)}%.",
                        priority="normal",
                        data={"metric": key, "value": value},
                    )
            await asyncio.sleep(POLL_S)
    except asyncio.CancelledError:
        logger.info("resource monitor stopped")
        raise


def _sample() -> dict[str, float | None]:
    import psutil

    out: dict[str, float | None] = {"disk": None, "mem": None, "cpu": None}
    try:
        # System drive: C:\ on Windows, / elsewhere.
        import os

        root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
        out["disk"] = float(psutil.disk_usage(root).percent)
    except Exception:
        logger.debug("disk sample failed", exc_info=True)
    try:
        out["mem"] = float(psutil.virtual_memory().percent)
    except Exception:
        logger.debug("mem sample failed", exc_info=True)
    try:
        out["cpu"] = float(psutil.cpu_percent(interval=None))
    except Exception:
        logger.debug("cpu sample failed", exc_info=True)
    return out
