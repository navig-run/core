"""
navig.ui.theme — Console, styles, and runtime capability flags.

SAFE_MODE activates when:
  - NAVIG_SAFE_MODE=1 env var is set, OR
  - stdout encoding is not UTF-8 (Windows codepage 1252, etc.)

NERD_FONT_AVAILABLE is True when:
  - NAVIG_NERD_FONT=1 env var is set, OR
  - ~/.navig/terminal.json records nerd_font=true (written by navig init
    terminal-setup step or by install.ps1/install.sh), OR
  - A fast filesystem/registry probe finds a Nerd Font on this machine
  Set NAVIG_NERD_FONT=0 to forcibly disable (e.g. for screenshots).

Import `console` and style constants from here — never create
a new get_console() in command modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.console import Console

from navig.console_helper import get_console


# ── SAFE MODE detection ───────────────────────────────────────────────────
def _detect_safe_mode() -> bool:
    if os.getenv("NAVIG_SAFE_MODE", "0") == "1":
        return True
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return encoding not in ("utf-8", "utf8", "")


SAFE_MODE: bool = _detect_safe_mode()
RENDER_MODE: str = "safe" if SAFE_MODE else "rich"


# ── Nerd Font detection ───────────────────────────────────────────────────
def _detect_nerd_font() -> bool:
    """Return True when a Nerd Font is available for this terminal session.

    Priority order:
      1. NAVIG_NERD_FONT env var ("1" / "0") — explicit user override
      2. ~/.navig/terminal.json key ``nerd_font`` — persisted by onboarding
      3. Active filesystem / registry probe (fast, < 100 ms)

    Returns False in CI (CI=1) or when NO_COLOR is set, to avoid emitting
    Nerd Font codepoints in environments that cannot render them.
    """
    override = os.getenv("NAVIG_NERD_FONT")
    if override == "1":
        return True
    if override == "0":
        return False

    # Suppress in CI / non-colour environments
    if os.getenv("CI") or os.getenv("NO_COLOR"):
        return False

    # Read cached result from terminal.json
    try:
        from navig.platform.paths import config_dir
        from navig.ui._capabilities import read_terminal_json

        navig_home = os.getenv("NAVIG_HOME", "").strip()
        navig_dir = Path(navig_home) if navig_home else config_dir()
        data = read_terminal_json(navig_dir)
        if "nerd_font" in data:
            return bool(data["nerd_font"])
    except Exception:  # noqa: BLE001
        pass

    # Active probe (filesystem scan / registry / fc-list)
    try:
        from navig.ui._capabilities import probe_nerd_font

        return probe_nerd_font()
    except Exception:  # noqa: BLE001
        return False


NERD_FONT_AVAILABLE: bool = _detect_nerd_font()

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
