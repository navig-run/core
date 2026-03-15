"""
boot_anim.py — First-run matrix boot animation.

Cipher Hatch variant: hex rain columns converge to reveal the node identity.
Plays only on first run, only on real TTY terminals (≥60 cols, UTF-8 capable).

Public API:
    play_boot_animation(node_id, name="", born_date="")  ->  None
"""
from __future__ import annotations

import random
import sys
import time
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────
_HEX   = "0123456789ABCDEF"
_RUNIC = "ᚠᚢᚦᚨᚱᚲᚷᚹᚺᚾᛁᛃᛇᛈᛉᛊᛏᛒᛖᛗᛚᛜᛞᛟ"

_ROWS        = 11
_FPS         = 22
_TOTAL_SECS  = 2.6
_TOTAL_F     = int(_TOTAL_SECS * _FPS)    # ~57 frames
_ACT1_END    = int(_TOTAL_F * 0.25)       # sparse drift
_ACT2_END    = int(_TOTAL_F * 0.82)       # rain + reveal
# Act 3: hold / identity card (remaining frames)

_MIN_COLS    = 60
_MARGIN      = 0   # full-width — no padding


def play_boot_animation(
    node_id: str,
    name: str = "",
    born_date: str = "",
) -> None:
    """
    Play a 2.5-second matrix boot sequence, then return cleanly.

    Phase 1 — sparse hex drift  (signal warming up)
    Phase 2 — matrix rain descends; node_id is revealed center-screen
    Phase 3 — rain dissolves; identity card holds

    Silently skips on: non-TTY, narrow terminal, missing Rich.
    Uses Live(transient=True) — animation is erased before init steps appear.
    """
    # ── Guards ────────────────────────────────────────────────────────────
    if not sys.stdout.isatty():
        return
    try:
        import shutil as _sh
        term_cols = _sh.get_terminal_size().columns
        if term_cols < _MIN_COLS:
            return
    except Exception:
        return

    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text
    except ImportError:
        return

    draw_w = term_cols  # full terminal width, no cap

    # Stable seed from node_id so consistent per-machine but not random per run
    seed = int.from_bytes(node_id.encode()[:4], "big") if node_id else 42
    rng  = random.Random(seed)

    # ── Simulation state ──────────────────────────────────────────────────
    heads   = [rng.randint(0, _ROWS - 1) for _ in range(draw_w)]
    frozen: dict[int, list[str]] = {}   # col -> list[char per row]

    target  = f" {node_id} " if node_id else " NAVIG "
    trow    = _ROWS // 2
    tcol    = max(0, (draw_w - len(target)) // 2)

    sub_line  = ""
    if name or born_date:
        sub_line = f" {name} " + (f"· {born_date}" if born_date else "")
        sub_line = sub_line.strip()

    console = Console(highlight=False, width=term_cols)

    def _build(f: int) -> "Text":
        """Render frame f to a Rich Text object."""
        # cells: (row, col) -> (char, style)
        cells: dict[tuple[int, int], tuple[str, str]] = {}

        # ── Navig color palette ──────────────────────────────────────────
        # dim/deep:   #0d2e3f  (dark navy sea)
        # mid trail:  #1a5c7c  (sea blue)
        # head/live:  #2c8bb7  (navig primary)
        # bright:     #5bc4f0  (sky highlight)
        # frozen:     #0f3d52  (deep frozen)
        # node text:  bold #5bc4f0
        _C_DEEP    = "#0d2e3f"
        _C_MID     = "#1a5c7c"
        _C_PRIMARY = "#2c8bb7"
        _C_BRIGHT  = "#5bc4f0"
        _C_FROZEN  = "#0f3d52"
        _C_NODE    = "bold #5bc4f0"
        _C_SEP     = "#1a5c7c"
        _C_SUB     = "#2c8bb7"

        if f < _ACT1_END:
            # ── Phase 1: sparse drift ──────────────────────────────────
            density = 0.03 + (f / max(_ACT1_END, 1)) * 0.07
            for r in range(_ROWS):
                for c in range(draw_w):
                    if rng.random() < density:
                        cells[(r, c)] = (rng.choice(_HEX), _C_DEEP)

        elif f < _ACT2_END:
            # ── Phase 2: full matrix rain ──────────────────────────────
            prog = (f - _ACT1_END) / max(_ACT2_END - _ACT1_END, 1)

            # Freeze columns gradually (second half of act 2)
            n_freeze = 0
            if prog > 0.55:
                freeze_prog = (prog - 0.55) / 0.45
                n_freeze    = int(draw_w * freeze_prog * 0.7)
                freeze_cols = sorted(
                    range(draw_w),
                    key=lambda c: heads[c],
                )[:n_freeze]
                for c in freeze_cols:
                    if c not in frozen:
                        frozen[c] = [rng.choice(_RUNIC) for _ in range(_ROWS)]

            # Draw frozen runic columns
            for c, chars in frozen.items():
                for r, ch in enumerate(chars):
                    cells[(r, c)] = (ch, _C_FROZEN)

            # Advance + draw active rain columns
            for c in range(draw_w):
                if c in frozen:
                    continue
                heads[c] = (heads[c] + rng.randint(0, 2)) % _ROWS
                head = heads[c]
                for r in range(_ROWS):
                    dist = (head - r) % _ROWS
                    if dist == 0:
                        cells[(r, c)] = (rng.choice(_HEX), _C_BRIGHT)
                    elif dist < 4:
                        cells[(r, c)] = (rng.choice(_HEX), _C_PRIMARY)
                    elif dist < 10 and rng.random() < 0.35:
                        cells[(r, c)] = (rng.choice(_HEX), _C_MID)

            # Reveal node_id left → right
            reveal = int(len(target) * min(1.0, prog * 1.6))
            for i, ch in enumerate(target[:reveal]):
                c = tcol + i
                if 0 <= c < draw_w:
                    cells[(trow, c)] = (ch, _C_NODE)

        else:
            # ── Phase 3: identity card ─────────────────────────────────
            act3_p = (f - _ACT2_END) / max(_TOTAL_F - _ACT2_END, 1)

            # Fading rain background
            rain_fade = max(0.0, 1.0 - act3_p * 2.5)
            for c in range(draw_w):
                if c in frozen:
                    if rng.random() < rain_fade * 0.4:
                        r = rng.randint(0, _ROWS - 1)
                        cells[(r, c)] = (frozen[c][r], _C_FROZEN)
                elif rng.random() < rain_fade * 0.15:
                    r = rng.randint(0, _ROWS - 1)
                    cells[(r, c)] = (rng.choice(_HEX), _C_DEEP)

            # Full node_id line
            for i, ch in enumerate(target):
                c = tcol + i
                if 0 <= c < draw_w:
                    cells[(trow, c)] = (ch, _C_NODE)

            # Sub-line (name + born) fades in
            if sub_line and act3_p > 0.3:
                sub_c = max(0, (draw_w - len(sub_line)) // 2)
                sub_r = trow + 1
                if sub_r < _ROWS:
                    for i, ch in enumerate(sub_line):
                        cc = sub_c + i
                        if 0 <= cc < draw_w:
                            cells[(sub_r, cc)] = (ch, _C_SUB)

            # Top + bottom separator lines (thin, fades in at end)
            if act3_p > 0.5:
                sep_char = "─"
                sep_r_top = trow - 1
                sep_r_bot = trow + (2 if sub_line else 1)
                sep_start = max(0, tcol - 2)
                sep_end   = min(draw_w, tcol + len(target) + 2)
                for c in range(sep_start, sep_end):
                    if 0 <= sep_r_top < _ROWS:
                        cells[(sep_r_top, c)] = (sep_char, _C_SEP)
                    if 0 <= sep_r_bot < _ROWS:
                        cells[(sep_r_bot, c)] = (sep_char, _C_SEP)

        # ── Render cells dict → Rich Text ──────────────────────────────────
        t = Text()
        for r in range(_ROWS):
            t.append(" " * _MARGIN)
            for c in range(draw_w):
                if (r, c) in cells:
                    ch, style = cells[(r, c)]
                    t.append(ch, style=style)
                else:
                    t.append(" ")
            t.append("\n")
        return t

    # ── Main loop ─────────────────────────────────────────────────────────
    try:
        with Live(
            _build(0),
            console=console,
            refresh_per_second=_FPS,
            transient=True,
        ) as live:
            t0 = time.monotonic()
            for f in range(_TOTAL_F):
                target_t = (f + 1) / _FPS
                sleep_s  = target_t - (time.monotonic() - t0)
                if sleep_s > 0.001:
                    time.sleep(sleep_s)
                live.update(_build(f))
    except Exception:  # noqa: BLE001
        # Terminal resize, pipe breakage, etc. — just continue
        pass
