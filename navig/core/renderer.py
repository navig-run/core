"""
NAVIG Terminal UI — Rich CLI Rendering System.

Single source of truth for all CLI output: ANSI colours, block frames,
metric bars, session envelopes.  Supports a ``--plain`` escape hatch
that strips every ANSI code so the output is safe for pipes and logs.

Plain-mode detection (checked once at import time, no CLI dependency):
  • ``--plain`` or ``--raw`` in ``sys.argv``
  • ``NAVIG_PLAIN=1`` environment variable
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Plain-mode gate (resolved once at import time)
# ---------------------------------------------------------------------------

_PLAIN_MODE: bool = (
    "--plain" in sys.argv or "--raw" in sys.argv or os.getenv("NAVIG_PLAIN", "0") == "1"
)


# ---------------------------------------------------------------------------
# ANSI colour constants
# ---------------------------------------------------------------------------


class _C:  # noqa: N801
    RESET = "" if _PLAIN_MODE else "\033[0m"
    BOLD = "" if _PLAIN_MODE else "\033[1m"
    DIM = "" if _PLAIN_MODE else "\033[2m"

    # Foreground
    WHITE = "" if _PLAIN_MODE else "\033[97m"
    GREY = "" if _PLAIN_MODE else "\033[37m"
    CYAN = "" if _PLAIN_MODE else "\033[36m"
    BLUE = "" if _PLAIN_MODE else "\033[34m"
    GREEN = "" if _PLAIN_MODE else "\033[32m"
    YELLOW = "" if _PLAIN_MODE else "\033[33m"
    RED = "" if _PLAIN_MODE else "\033[31m"
    MAGENTA = "" if _PLAIN_MODE else "\033[35m"

    # Background (used for progress bar fill)
    BG_GREEN = "" if _PLAIN_MODE else "\033[42m"
    BG_YELLOW = "" if _PLAIN_MODE else "\033[43m"
    BG_RED = "" if _PLAIN_MODE else "\033[41m"
    BG_BLUE = "" if _PLAIN_MODE else "\033[44m"
    BG_GREY = "" if _PLAIN_MODE else "\033[100m"


# ---------------------------------------------------------------------------
# Block type catalogue
# ---------------------------------------------------------------------------


class BlockType(str, Enum):
    CONNECT = "CONNECT"
    FETCH = "FETCH"
    METRICS = "METRICS"
    ROOT_CAUSE = "ROOT_CAUSE"
    FIX = "FIX"
    ACTION = "ACTION"
    CONFIRM = "CONFIRM"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


# ---------------------------------------------------------------------------
# Per-block style: accent colour + label text
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BlockStyle:
    label: str
    accent: str  # ANSI escape sequence (empty in plain mode)
    icon: str  # single char / emoji — omitted in plain mode


_STYLES: dict[BlockType, _BlockStyle] = {
    BlockType.CONNECT: _BlockStyle("CONNECT", _C.CYAN, "○"),
    BlockType.FETCH: _BlockStyle("FETCH", _C.BLUE, "↓"),
    BlockType.METRICS: _BlockStyle("METRICS", _C.CYAN, "▤"),
    BlockType.ROOT_CAUSE: _BlockStyle("ROOT_CAUSE", _C.YELLOW, "⊕"),
    BlockType.FIX: _BlockStyle("FIX", _C.GREEN, "✦"),
    BlockType.ACTION: _BlockStyle("ACTION", _C.MAGENTA, "▶"),
    BlockType.CONFIRM: _BlockStyle("CONFIRM", _C.GREEN, "✓"),
    BlockType.INFO: _BlockStyle("INFO", _C.CYAN, "ℹ"),
    BlockType.WARNING: _BlockStyle("WARNING", _C.YELLOW, "⚠"),
    BlockType.ERROR: _BlockStyle("ERROR", _C.RED, "✗"),
    BlockType.SUCCESS: _BlockStyle("SUCCESS", _C.GREEN, "✓"),
}

# A reusable horizontal rule
DIVIDER: str = f"{_C.DIM}{'─' * 60}{_C.RESET}" if not _PLAIN_MODE else "-" * 60


# ---------------------------------------------------------------------------
# Progress / metric bar
# ---------------------------------------------------------------------------


def progress_bar(
    value: float,
    total: float,
    *,
    width: int = 20,
    warn_pct: float = 80.0,
    crit_pct: float = 95.0,
) -> str:
    """Return a coloured ASCII progress bar.

    Args:
        value: Current value.
        total: Maximum value (must be > 0).
        width: Number of bar characters.
        warn_pct: Percentage threshold for yellow colouring.
        crit_pct: Percentage threshold for red colouring.

    Returns:
        A string like ``[████████░░░░░░░░░░░░] 40.0%``
    """
    if total <= 0:
        pct = 0.0
    else:
        pct = min(value / total * 100, 100.0)

    filled = int(width * pct / 100)
    empty = width - filled

    if _PLAIN_MODE:
        bar = "[" + "#" * filled + "." * empty + "]"
        return f"{bar} {pct:.1f}%"

    if pct >= crit_pct:
        colour = _C.BG_RED
    elif pct >= warn_pct:
        colour = _C.BG_YELLOW
    else:
        colour = _C.BG_GREEN

    bar = f"{colour}{' ' * filled}{_C.BG_GREY}{' ' * empty}{_C.RESET}"
    pct_str = f"{pct:.1f}%"
    return f"[{bar}] {_C.BOLD}{pct_str}{_C.RESET}"


# ---------------------------------------------------------------------------
# Core render primitives
# ---------------------------------------------------------------------------


def renderBlock(
    block_type: BlockType,
    title: str,
    body: str | None = None,
) -> None:
    """Print a labelled block to stdout.

    In plain mode every ANSI escape is suppressed and the icon is omitted.

    Args:
        block_type: One of the :class:`BlockType` variants.
        title:      Short summary line shown on the header row.
        body:       Optional multi-line body text (indented 4 spaces).
    """
    style = _STYLES[block_type]

    if _PLAIN_MODE:
        header = f"[{style.label}] {title}"
    else:
        header = (
            f"{style.accent}{_C.BOLD}{style.icon}  {style.label}{_C.RESET}"
            f"  {_C.WHITE}{title}{_C.RESET}"
        )

    print(header)
    if body:
        for line in body.splitlines():
            print(f"    {line}")
    print()


def renderMetric(
    name: str,
    value: float,
    total: float,
    *,
    unit: str = "",
    warn_pct: float = 80.0,
    crit_pct: float = 95.0,
    indent: int = 2,
) -> None:
    """Print a single metric row with an inline progress bar.

    Args:
        name:     Metric label (left-aligned, padded to 24 chars).
        value:    Current value.
        total:    Scale maximum.
        unit:     Optional suffix (e.g. ``"ms"``, ``"%"``).
        warn_pct: Yellow threshold percentage.
        crit_pct: Red threshold percentage.
        indent:   Leading spaces.
    """
    pad = " " * indent
    bar = progress_bar(value, total, warn_pct=warn_pct, crit_pct=crit_pct)
    pct = value / total * 100 if total > 0 else 0.0

    if _PLAIN_MODE:
        label = f"{name:<24}"
        val_str = f"{value:.0f}/{total:.0f}{unit}"
        print(f"{pad}{label}  {val_str}  {bar}")
        return

    # Colour the name based on severity
    if pct >= crit_pct:
        name_colour = _C.RED
    elif pct >= warn_pct:
        name_colour = _C.YELLOW
    else:
        name_colour = _C.GREY

    label = f"{name_colour}{name:<24}{_C.RESET}"
    val_str = f"{_C.DIM}{value:.0f}/{total:.0f}{unit}{_C.RESET}"
    print(f"{pad}{label}  {val_str}  {bar}")


# ---------------------------------------------------------------------------
# Session envelope
# ---------------------------------------------------------------------------


def sessionOpen(host: str, command: str) -> None:
    """Print the session header banner.

    Args:
        host:    Target host name shown in the banner.
        command: The navig command being executed.
    """
    if _PLAIN_MODE:
        print(f"=== NAVIG {command} @ {host} ===")
        print()
        return

    width = 62
    title = f" NAVIG {command} "
    pad = "─" * ((width - len(title)) // 2)
    print()
    print(f"{_C.CYAN}{_C.BOLD}{pad}{title}{pad}{_C.RESET}")
    print(f"{_C.DIM}  host: {host}{_C.RESET}")
    print()


def sessionClose(summary: str | None = None) -> None:
    """Print the session footer.

    Args:
        summary: Optional one-line summary (e.g. ``"3 warnings, 0 errors"``).
    """
    if _PLAIN_MODE:
        if summary:
            print(f"--- {summary} ---")
        print("=== done ===")
        return

    print(DIVIDER)
    if summary:
        print(f"{_C.DIM}  {summary}{_C.RESET}")
    print(f"{_C.DIM}  done{_C.RESET}")
    print()


# ---------------------------------------------------------------------------
# Abort helper
# ---------------------------------------------------------------------------


def abortOnFailure(message: str, *, exit_code: int = 1) -> None:  # noqa: N802
    """Print an ERROR block and exit with *exit_code*.

    Args:
        message:   Human-readable error description.
        exit_code: Process exit code (default 1).
    """
    renderBlock(BlockType.ERROR, message)
    sys.exit(exit_code)
