"""
navig.ui.panels — Primary state, explanation, and metric panels.

Layout contract (4 layers):
  Layer 1: render_status_header    (status.py)
  Layer 2: render_primary_state    (this file) — what is happening
  Layer 3: render_explanation      (this file) — why it's happening
           render_metrics_panel    (this file) — numeric signals
  Layer 4: render_actions          (actions.py)
"""
from __future__ import annotations

import sys
from typing import List, Optional

from navig.ui.models import CauseScore, Metric
from navig.ui.theme import (
    SAFE_MODE,
    SEVERITY_STYLE,
    console,
)


def render_primary_state(
    label: str,
    state_icon: str,
    detail: str,
    style: str = "white",
    *,
    hint: Optional[str] = None,
) -> None:
    """Layer 2 — primary state line. Never raises."""
    try:
        ico = state_icon
        lines = [f"[{style}]{ico}  {label}[/{style}]"]
        if detail:
            lines.append(f"   [dim]{detail}[/dim]")
        if hint:
            lines.append(f"   [dim]{hint}[/dim]")
        console.print("\n".join(lines))
    except Exception:
        try:
            print(f"{label}: {detail}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_explanation(
    causes: List[CauseScore],
    *,
    title: str = "Why this is happening",
) -> None:
    """Layer 3a — cause analysis block. Never raises."""
    try:
        if not causes:
            return
        lines = [f"[bold]{title}[/bold]"]
        for c in causes:
            bar_len = max(1, c.confidence // 10)
            bar = ("█" if not SAFE_MODE else "#") * bar_len + ("░" if not SAFE_MODE else ".") * (10 - bar_len)
            sev_style = SEVERITY_STYLE.get(c.severity, "white")
            lines.append(
                f"  [{sev_style}]{bar}[/{sev_style}] "
                f"[{sev_style}]{c.confidence:3d}%[/{sev_style}]  "
                f"[white]{c.description}[/white]"
            )
        console.print("\n".join(lines))
    except Exception:
        try:
            for c in causes:
                print(f"  {c.confidence}%  {c.description}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def render_metrics_panel(
    metrics: List[Metric],
    *,
    title: str = "Metrics",
) -> None:
    """Layer 3b — metric bars panel. Never raises."""
    try:
        from navig.ui.bars import render_metric_bars
        render_metric_bars(metrics, title=title)
    except Exception:
        try:
            for m in metrics:
                print(f"  {m.label}: {m.value}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
