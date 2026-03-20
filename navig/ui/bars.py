"""
navig.ui.bars — Metric bars and sparklines using block characters.

Characters: █ (fill) ░ (empty) — ASCII fallback: # .
"""
from __future__ import annotations

import sys
from typing import List

from navig.ui.models import Metric
from navig.ui.theme import COLOR_STYLE, SAFE_MODE, console

_BAR_WIDTH = 20
_FILL_RICH = "█"
_EMPTY_RICH = "░"
_FILL_SAFE = "#"
_EMPTY_SAFE = "."


def _make_bar(fill: float, width: int = _BAR_WIDTH) -> tuple[str, str]:
    """Return (filled_part, empty_part) strings."""
    fill = max(0.0, min(1.0, fill))
    n = round(fill * width)
    if SAFE_MODE:
        return _FILL_SAFE * n, _EMPTY_SAFE * (width - n)
    return _FILL_RICH * n, _EMPTY_RICH * (width - n)


def render_metric_bars(
    metrics: List[Metric],
    *,
    title: str = "Metrics",
    label_width: int = 18,
) -> None:
    """Render a list of metrics with horizontal fill bars. Never raises."""
    try:
        if not metrics:
            return
        console.print(f"[bold]{title}[/bold]")
        for m in metrics:
            color = COLOR_STYLE.get(m.color, m.color)
            filled, empty = _make_bar(m.bar_fill)
            label = m.label.ljust(label_width)
            value_str = m.value.rjust(8)
            sparkline = f" {m.sparkline}" if m.sparkline else ""
            console.print(
                f"  [dim]{label}[/dim] "
                f"[{color}]{filled}[/{color}][dim]{empty}[/dim] "
                f"[{color}]{value_str}[/{color}]"
                f"[dim]{sparkline}[/dim]"
            )
    except Exception:
        try:
            for m in metrics:
                filled, empty = _make_bar(m.bar_fill)
                print(f"  {m.label}: {filled}{empty} {m.value}", file=sys.stdout)
        except Exception:
            pass


def render_sparklines(
    metrics: List[Metric],
    *,
    title: str = "Trend",
) -> None:
    """Render metrics that have sparkline data. Never raises."""
    try:
        has_spark = [m for m in metrics if m.sparkline]
        if not has_spark:
            return
        console.print(f"[bold]{title}[/bold]")
        for m in has_spark:
            color = COLOR_STYLE.get(m.color, m.color)
            console.print(
                f"  [dim]{m.label.ljust(18)}[/dim] [{color}]{m.sparkline}[/{color}]"
            )
    except Exception:
        try:
            for m in metrics:
                if m.sparkline:
                    print(f"  {m.label}: {m.sparkline}", file=sys.stdout)
        except Exception:
            pass
