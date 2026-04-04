"""
navig.ui._capabilities — Terminal capability detection and persistence.

Provides fast, platform-appropriate probing for Nerd Font availability
and read/write access to ~/.navig/terminal.json (the capability cache).

Used by:
  - navig.ui.theme          (NERD_FONT_AVAILABLE at import time)
  - navig.onboarding.steps  (terminal-setup step)
  - install.ps1 / install.sh write an equivalent JSON payload
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_TERMINAL_JSON_NAME = "terminal.json"


# ── Persistent cache helpers ──────────────────────────────────────────────────

def read_terminal_json(navig_dir: Path) -> dict[str, Any]:
    """Read ~/.navig/terminal.json; returns {} on any error."""
    try:
        return json.loads((navig_dir / _TERMINAL_JSON_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def write_terminal_json(navig_dir: Path, **kwargs: Any) -> None:
    """Merge *kwargs* into terminal.json, stamping checked_at.

    Idempotent: re-running after a successful install updates the cache.
    """
    import datetime

    existing = read_terminal_json(navig_dir)
    existing.update(kwargs)
    existing["checked_at"] = (
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    navig_dir.mkdir(parents=True, exist_ok=True)
    (navig_dir / _TERMINAL_JSON_NAME).write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Active font probing ───────────────────────────────────────────────────────

def probe_nerd_font() -> bool:
    """Return True if any Nerd Font is installed on this machine.

    Checks are fast filesystem globs / registry reads — no subprocess unless
    on Linux (fc-list fallback, 3 s timeout).  Returns False on any error.
    """
    if sys.platform == "win32":
        return _probe_windows()
    if sys.platform == "darwin":
        return _probe_macos()
    return _probe_linux()


def _probe_windows() -> bool:
    # 1. HKLM fonts registry
    try:
        import winreg  # type: ignore[import]

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
        )
        i = 0
        while True:
            try:
                name, _, _ = winreg.EnumValue(key, i)
                nl = name.lower()
                if "nerd font" in nl or "nerdfont" in nl:
                    winreg.CloseKey(key)
                    return True
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:  # noqa: BLE001
        pass
    # 2. User-local fonts (Windows 10+)
    user_fonts = (
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    )
    return _any_nerd_font_in_dir(user_fonts)


def _probe_macos() -> bool:
    for d in [
        Path.home() / "Library" / "Fonts",
        Path("/Library/Fonts"),
    ]:
        if _any_nerd_font_in_dir(d):
            return True
    return False


def _probe_linux() -> bool:
    for d in [
        Path.home() / ".local" / "share" / "fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]:
        if _any_nerd_font_in_dir(d):
            return True
    # fc-list fallback (fast — list is already cached by fontconfig)
    try:
        out = subprocess.run(
            ["fc-list", ":", "family"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        text = out.stdout.lower()
        if "nerd font" in text or "nerdfont" in text:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _any_nerd_font_in_dir(d: Path) -> bool:
    if not d.exists():
        return False
    try:
        for f in d.rglob("*"):
            n = f.name.lower()
            if "nerdfont" in n or "nerd font" in n:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ── Automated installation helper ────────────────────────────────────────────

def try_install_nerd_font() -> bool:
    """Attempt a silent Nerd Font install.  Returns True on success.

    Windows: runs Install-NerdFont.ps1 via pwsh (if available).
    macOS/Linux: prints brew / manual instructions and returns False
    (font install requires a restart and sudo on some systems).
    """
    import shutil

    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            return False
        # Locate the bundled script; works in dev checkouts and editable installs
        script = _find_install_script()
        if script is None:
            return False
        try:
            result = subprocess.run(
                [pwsh, "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                timeout=120,
            )
            return result.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    # macOS / Linux: can't safely automate; return False and let the step
    # print the manual one-liner.
    return False


def _find_install_script() -> Path | None:
    """Find Install-NerdFont.ps1 relative to this package."""
    candidates = [
        # Dev checkout: navig/ui/_capabilities.py → ../../scripts/
        Path(__file__).parent.parent.parent / "scripts" / "Install-NerdFont.ps1",
        # Installed in a venv / site-packages (scripts bundled via package data)
        Path(sys.prefix) / "scripts" / "Install-NerdFont.ps1",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None
