"""
navig.ui.status — Compact single-line status header chip row.

Renders a horizontal strip of colored status chips, e.g.:
  ◉ daemon  online   ·  ⬡ peers  3   ·  ⬛ host  prod
"""
from __future__ import annotations

import sys
from typing import List

from navig.ui.models import StatusChip
from navig.ui.theme import COLOR_STYLE, SAFE_MODE, console


def render_status_header(chips: List[StatusChip], *, sep: str = "  ·  ") -> None:
    """Print a compact chip row to stdout. Never raises."""
    try:
        parts: list[str] = []
        for chip in chips:
            ico = chip.icon_safe if SAFE_MODE else chip.icon
            color = COLOR_STYLE.get(chip.color, chip.color)
            if chip.value is not None:
                parts.append(
                    f"[{color}]{ico} {chip.label}[/{color}]"
                    f"  [{color}]{chip.value}[/{color}]"
                )
            else:
                parts.append(f"[{color}]{ico} {chip.label}[/{color}]")
        if parts:
            console.print(sep.join(parts))
    except Exception:
        try:
            for chip in chips:
                ico = chip.icon_safe
                val = f"  {chip.value}" if chip.value else ""
                print(f"{ico} {chip.label}{val}", file=sys.stdout)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
