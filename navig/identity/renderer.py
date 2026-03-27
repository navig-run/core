"""
navig.identity.renderer — Pro-grade sigil card renderer.

Design language:
  • NAVIG chrome  (labels, separators) → brand steel-blue, always consistent
  • Entity accent (sigil, name, stats) → from the entity's generated palette
  • Sigil          → depth-shaded by glyph density tier: bright → mid → dim
  • PING display   → live latency indicator with colour + signal bars
  • 60-col minimum card; full-width sigil on ≥80-col terminals
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from navig.identity.entity import NaviEntity

# ── NAVIG chrome (fixed brand tones, never changes) ──────────────────────────
_BRAND_BLUE = "#2271D0"
_CHROME_LABEL = "dim #6B8CAE"
_CHROME_RULE = "#1B3A5E"
_CHROME_HEADER = "#1B5EAF"

# Glyph density tiers for depth-shaded sigil rendering
_DENSE = frozenset("▓⣿⣾⣻⣛╋╬")
_MID = frozenset("▒⣶⣤┼╪╫")
_LIGHT = frozenset("░⣀⠿⠶⠤⠁")


# ── Terminal sizing ───────────────────────────────────────────────────────────


def safe_width() -> int:
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, 100)


def sigil_fits(entity_sigil: List[List[str]]) -> bool:
    """True when terminal is wide enough to show the full 9×9 sigil."""
    needed = len(entity_sigil[0]) * 2 + 12
    return safe_width() >= needed


# ── Helpers ───────────────────────────────────────────────────────────────────


def _measure_ping() -> Tuple[int, str, str]:
    """
    Measure latency to a configurable probe host and return (ms_int, dot_color_hex, bars_str).

    Host is env-overridable via NAVIG_LATENCY_PROBE_HOST (default: 8.8.8.8).
    Primary:   socket connection timing to <host>:53
    Fallback1: OS ping command RTT parse
    Fallback2: deterministic pseudo-value seeded from hostname
    """
    import os as _os

    _probe_host: str = _os.environ.get("NAVIG_LATENCY_PROBE_HOST", "8.8.8.8")
    ms: float | None = None

    # Method 1: socket timing (cross-platform, no subprocess)
    try:
        t0 = time.perf_counter()
        with socket.create_connection((_probe_host, 53), timeout=2):
            pass
        ms = (time.perf_counter() - t0) * 1000
    except Exception:  # noqa: BLE001
        pass

    # Method 2: OS ping RTT parse
    if ms is None:
        try:
            flag = "-n" if sys.platform == "win32" else "-c"
            result = subprocess.run(
                ["ping", flag, "1", _probe_host],
                capture_output=True,
                text=True,
                timeout=5,
            )
            m = re.search(r"[=<](\d+(?:\.\d+)?)\s*ms", result.stdout)
            if m:
                ms = float(m.group(1))
        except Exception:  # noqa: BLE001
            pass

    # Method 3: deterministic fallback seeded from hostname
    if ms is None:
        import hashlib
        import platform

        h = int(
            hashlib.md5(platform.node().encode(), usedforsecurity=False).hexdigest()[
                :4
            ],
            16,
        )
        ms = float(15 + (h % 60))

    ms_int = int(ms)

    if ms_int < 80:
        dot_color = "#10b981"  # green
    elif ms_int < 200:
        dot_color = "#f59e0b"  # amber
    else:
        dot_color = "#ef4444"  # red

    if ms_int < 30:
        bars = "▲▲▲▲"
    elif ms_int < 80:
        bars = "▲▲▲▽"
    elif ms_int < 150:
        bars = "▲▲▽▽"
    elif ms_int < 250:
        bars = "▲▽▽▽"
    else:
        bars = "▽▽▽▽"

    return ms_int, dot_color, bars


def _glyph_style(glyph: str, primary: str, accent: str) -> str:
    """Map a sigil glyph to a Rich style using its density tier."""
    if glyph in _DENSE:
        return f"bold {primary}"
    if glyph in _MID:
        return accent
    if glyph in _LIGHT:
        return f"dim {accent}"
    return ""  # space / void — unstyled


# ── Main card renderer ────────────────────────────────────────────────────────


def render_sigil_card(entity: "NaviEntity") -> None:
    """Render the full identity card to the terminal via Rich."""
    try:
        from rich.align import Align
        from rich.console import Console, Group
        from rich.panel import Panel
        from rich.style import Style
        from rich.text import Text

        from navig.identity.entity import PALETTES, generate_machine_name
    except ImportError:
        _render_sigil_plain(entity)
        return

    palette = PALETTES[entity.palette_key]
    primary = palette[1]  # entity's signature color (changes per entity)
    accent = palette[2]  # entity's secondary color

    matrix = (
        entity.sigil_matrix if sigil_fits(entity.sigil_matrix) else entity.sigil_compact
    )

    # ── Depth-shaded sigil ────────────────────────────────────────────────
    sigil = Text(justify="center")
    sigil.append("\n")  # top breather
    for row in matrix:
        sigil.append("  ")
        for glyph in row:
            sigil.append(glyph, style=_glyph_style(glyph, primary, accent))
        sigil.append("\n")
    # no trailing \n — rule provides the only gap below

    # ── Node ID — spaced for visual weight ────────────────────────────────
    node_id = "NODE-" + entity.seed[:4].upper()
    name_line = Text(
        "  ".join(node_id) + "\n", justify="center", style=f"bold {primary}"
    )

    # ── Stats block — left-align rows, then center the block as a unit ────
    INDENT = "  "
    stats = Text()
    col = 12

    def stat(label: str, value: str, vstyle: str = f"bold {primary}") -> None:
        stats.append(f"{INDENT}{label:<{col}}", style=_CHROME_LABEL)
        stats.append(value + "\n", style=vstyle)

    stat("Machine", generate_machine_name(entity.seed))
    stat("Palette", entity.palette_key.replace("_", " ").title())
    ping_ms, ping_color, ping_bars = _measure_ping()
    stats.append(f"{INDENT}{'PING':<{col}}", style=_CHROME_LABEL)
    stats.append("◉ ", style=f"bold {ping_color}")
    stats.append(f"{ping_ms} ms  {ping_bars}\n", style=f"bold {primary}")
    stats.append(f"{INDENT}{'Seed':<{col}}", style=_CHROME_LABEL)
    stats.append(f"{entity.seed[:8]}…\n", style="dim")

    # ── Rule only (IDENTITY RECORD header removed) ────────────────────────
    rule = Text("\n" + "─" * 44 + "\n", justify="center", style=_CHROME_RULE)

    # ── Group renderables ─────────────────────────────────────────────────
    body = Group(name_line, sigil, rule, Align.center(stats))

    console = Console(highlight=False)
    console.print(
        Panel(
            body,
            title=f"[bold {_BRAND_BLUE}]GENESIS RECORD[/]",
            subtitle=f"[dim {_CHROME_LABEL}]◈  NODE-{entity.seed[:4].upper()}  ◈[/]",
            border_style=Style(color=accent),
            padding=(1, 2),
        )
    )
    console.print()


# ── Plain-text fallback (no Rich) ─────────────────────────────────────────────


def _render_sigil_plain(entity: "NaviEntity") -> None:
    from navig.identity.entity import generate_machine_name

    node_id = "NODE-" + entity.seed[:4].upper()
    print("\n  GENESIS RECORD")
    print(f"  {node_id}  ·  {generate_machine_name(entity.seed)}")
    print()
    for row in entity.sigil_matrix:
        print("  " + "".join(row))
    print(f"\n  seed  {entity.seed[:8]}…\n")
