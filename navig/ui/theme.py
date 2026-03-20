"""
navig.ui.theme — Console, styles, and SAFE_MODE detection.

SAFE_MODE activates when:
  - NAVIG_SAFE_MODE=1 env var is set, OR
  - stdout encoding is not UTF-8 (Windows codepage 1252, etc.)

Import `console` and style constants from here — never create
a new Console() in command modules.
"""
from __future__ import annotations

import os
import sys

from rich.console import Console


# ── SAFE MODE detection ───────────────────────────────────────────────────
def _detect_safe_mode() -> bool:
    if os.getenv("NAVIG_SAFE_MODE", "0") == "1":
        return True
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return encoding not in ("utf-8", "utf8", "")


SAFE_MODE: bool = _detect_safe_mode()
RENDER_MODE: str = "safe" if SAFE_MODE else "rich"

# ── Shared console ────────────────────────────────────────────────────────
console = Console(highlight=False, markup=True)

# ── Semantic style constants ──────────────────────────────────────────────
STYLE_STATUS_OK = "bold green"
STYLE_STATUS_WARN = "bold yellow"
STYLE_STATUS_FAIL = "bold red"
STYLE_COMMAND = "bold cyan"
STYLE_DIM = "dim"
STYLE_AI = "bold magenta"
STYLE_HOST = "cyan"
STYLE_LABEL = "bold white"
STYLE_VALUE = "white"
STYLE_MUTED = "dim white"
STYLE_SECTION = "bold blue"

# ── Severity → style mapping ──────────────────────────────────────────────
SEVERITY_STYLE: dict[str, str] = {
    "ok": STYLE_STATUS_OK,
    "info": STYLE_COMMAND,
    "warn": STYLE_STATUS_WARN,
    "critical": STYLE_STATUS_FAIL,
}

# ── Color → style mapping ──────────────────────────────────────────────────
COLOR_STYLE: dict[str, str] = {
    "cyan": "cyan",
    "green": "green",
    "yellow": "yellow",
    "red": "red",
    "magenta": "magenta",
    "white": "white",
    "dim": "dim",
}
