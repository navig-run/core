"""
NAVIG — Centralised metric threshold registry.

Every named metric has a ``warn_pct`` (yellow) and ``crit_pct`` (red)
threshold, expressed as percentages of the metric's scale maximum.

Usage::

    from navig.core.thresholds import resolve

    t = resolve("cpu_usage")
    bar = progress_bar(value, total, warn_pct=t.warn_pct, crit_pct=t.crit_pct)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Threshold:
    """Warn / critical percentage thresholds for a single metric."""

    warn_pct: float  # Yellow at or above this percentage
    crit_pct: float  # Red at or above this percentage


# Default thresholds applied when a metric name is not in REGISTRY.
DEFAULTS = Threshold(warn_pct=80.0, crit_pct=95.0)

# Named metric registry — extend as new metrics are introduced.
REGISTRY: dict[str, Threshold] = {
    # System resources
    "cpu_usage":          Threshold(warn_pct=75.0,  crit_pct=90.0),
    "memory_usage":       Threshold(warn_pct=80.0,  crit_pct=95.0),
    "disk_usage":         Threshold(warn_pct=85.0,  crit_pct=95.0),
    "disk_io":            Threshold(warn_pct=70.0,  crit_pct=90.0),

    # Application / network
    "error_rate":         Threshold(warn_pct=5.0,   crit_pct=15.0),
    "p99_latency_ms":     Threshold(warn_pct=60.0,  crit_pct=85.0),
    "request_queue":      Threshold(warn_pct=60.0,  crit_pct=85.0),
    "worker_connections": Threshold(warn_pct=70.0,  crit_pct=90.0),
}


def resolve(metric_name: str) -> Threshold:
    """Return the :class:`Threshold` for *metric_name*.

    Falls back to :data:`DEFAULTS` if the metric is not registered.

    Args:
        metric_name: Logical name of the metric (see :data:`REGISTRY`).

    Returns:
        A :class:`Threshold` with ``warn_pct`` and ``crit_pct`` attributes.
    """
    return REGISTRY.get(metric_name, DEFAULTS)
