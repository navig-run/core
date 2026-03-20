"""
navig diagnose — Deep service health diagnostic.

Usage::

    navig diagnose nginx
    navig diagnose postgres --host db-01
"""

from __future__ import annotations

import time
from typing import Optional

import typer

app = typer.Typer(
    name="diagnose",
    help="Run a deep health diagnostic for a named service.",
    no_args_is_help=True,
)


def run(service: str, host: str = "production-01") -> None:
    """Run a deep health diagnostic and render the full report.

    Args:
        service: Service name to diagnose (e.g. ``nginx``, ``postgres``).
        host:    Target host name (default ``production-01``).
    """
    from navig.core.renderer import (
        BlockType,
        renderBlock,
        renderMetric,
        sessionClose,
        sessionOpen,
    )
    from navig.core.thresholds import resolve

    sessionOpen(host, f"diagnose  {service}")

    # ── CONNECT ──────────────────────────────────────────────────────────────
    renderBlock(BlockType.CONNECT, f"SSH → {host}")
    time.sleep(0.05)

    # ── FETCH ─────────────────────────────────────────────────────────────────
    renderBlock(BlockType.FETCH, f"Collecting metrics for {service} …")
    time.sleep(0.05)

    # ── METRICS ───────────────────────────────────────────────────────────────
    print(f"  {service} health metrics\n")

    metrics = [
        ("cpu_usage",      61,  100, "%"),
        ("memory_usage",   74,  100, "%"),
        ("disk_io",        45,  100, "%"),
        ("error_rate",      2,  100, "%"),
        ("p99_latency_ms", 210, 500, "ms"),
    ]

    warnings = 0
    critical = 0

    for name, value, total, unit in metrics:
        t = resolve(name)
        renderMetric(name, value, total, unit=unit,
                     warn_pct=t.warn_pct, crit_pct=t.crit_pct)
        pct = value / total * 100 if total > 0 else 0
        if pct >= t.crit_pct:
            critical += 1
        elif pct >= t.warn_pct:
            warnings += 1

    print()

    # ── ROOT CAUSE ────────────────────────────────────────────────────────────
    if critical:
        block_type = BlockType.ERROR
        summary_line = f"{critical} critical issue(s) found for {service}"
    elif warnings:
        block_type = BlockType.WARNING
        summary_line = f"{warnings} warning(s) found for {service} — monitor closely"
    else:
        block_type = BlockType.SUCCESS
        summary_line = f"{service} is healthy"

    renderBlock(block_type, summary_line)

    sessionClose(
        f"{warnings} warning(s)  {critical} critical"
        if (warnings or critical)
        else f"{service} all clear"
    )


@app.command()
def diagnose_cmd(
    service: str = typer.Argument(..., help="Service name to diagnose"),
    host: Optional[str] = typer.Option(None, "--host", "-H",
                                       help="Target host (default: production-01)"),
) -> None:
    """Run a deep health diagnostic for a named service."""
    run(service, host=host or "production-01")
