"""
navig.identity.genesis — Animated Genesis sequence.

Two-act boot animation played on first `navig onboard`:
  ACT I   — Signal emergence: NAVIG-blue noise burst (brand, ~0.9 s)
  ACT II  — Sigil assembles row-by-row, entity color materialises (~0.8 s)
  FINAL   — Full identity card via renderer.render_sigil_card()

Color language:
  noise phase  → NAVIG oceanic brand (blues/cyan)  — always consistent
  sigil phase  → entity's palette primary + accent — unique per machine

Note: Subsystem status boot lines have moved to BootScreen (onboard.py).
"""
from __future__ import annotations

import asyncio
import random
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.identity.entity import NaviEntity

# NAVIG brand spectrum used for the noise burst (never entity colours)
_NAVIG_SPECTRUM = ["#0057FF", "#00A8E8", "#003DA5", "#00D4FF", "#1B5EAF", "#2271D0"]


# ── Entry points ──────────────────────────────────────────────────────────────

async def play_genesis_animation(entity: "NaviEntity") -> None:
    """Async entry point — call from asyncio.run() or existing event loop."""
    from navig.identity.entity import PALETTES

    palette         = PALETTES[entity.palette_key]
    primary, accent = palette[1], palette[2]

    await _act_noise()
    await _act_sigil_assembly(entity, primary, accent)

    from navig.identity.renderer import render_sigil_card
    render_sigil_card(entity)


def play_genesis_animation_sync(entity: "NaviEntity") -> None:
    """Sync wrapper — safe to call from non-async contexts."""
    try:
        asyncio.run(play_genesis_animation(entity))
    except RuntimeError:
        import threading
        done = threading.Event()

        def _run() -> None:
            asyncio.run(play_genesis_animation(entity))
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        done.wait(timeout=15)


# ── ACT I — Signal emergence (NAVIG brand noise) ─────────────────────────────

async def _act_noise() -> None:
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text
    except ImportError:
        return

    width   = min(shutil.get_terminal_size(fallback=(80, 24)).columns - 4, 76)
    NOISE   = list("░▒▓⣿⣶⣤⣀⠿⠶ ")
    console = Console()
    rng     = random.Random()   # noise phase is intentionally non-deterministic

    with Live(console=console, refresh_per_second=24, transient=True) as live:
        for frame in range(20):
            t = Text()
            # Gradually calm from wide noise → narrow centre stripe
            active = max(12, width - frame * 3)
            pad    = (width - active) // 2
            col_a  = _NAVIG_SPECTRUM[frame % len(_NAVIG_SPECTRUM)]
            col_b  = _NAVIG_SPECTRUM[(frame + 2) % len(_NAVIG_SPECTRUM)]
            for row in range(5):
                line = " " * pad
                line += "".join(rng.choice(NOISE) for _ in range(active))
                line += " " * pad
                t.append(line + "\n", style=f"bold {col_a if row % 2 == 0 else col_b}")
            live.update(t)
            await asyncio.sleep(0.045)


# ── ACT II — Sigil assembly (entity colour materialises) ─────────────────────

async def _act_sigil_assembly(entity: "NaviEntity", primary: str, accent: str) -> None:
    try:
        from rich.align import Align
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        from navig.identity.renderer import _glyph_style, sigil_fits
    except ImportError:
        return

    matrix  = entity.sigil_matrix if sigil_fits(entity.sigil_matrix) else entity.sigil_compact
    console = Console()

    with Live(console=console, refresh_per_second=14, transient=True) as live:
        for reveal in range(1, len(matrix) + 1):
            partial = Text(justify="center")
            partial.append("\n")
            for r in range(reveal):
                partial.append("  ")
                for glyph in matrix[r]:
                    partial.append(glyph, style=_glyph_style(glyph, primary, accent))
                partial.append("\n")
            live.update(Align.center(partial))
            await asyncio.sleep(0.09)




