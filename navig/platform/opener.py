"""Cross-platform "open a path or URL in the OS default handler".

Centralizes the Windows/macOS/Linux branch so callers don't hand-roll (and
half-guard) it. ``os.startfile`` is Windows-only and throws elsewhere; ``open``
is macOS-only (it is not a file-opener on Linux); ``xdg-open`` is the Linux one.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def open_path(target: str | Path) -> bool:
    """Open *target* (a file, folder, or URL) in the OS default handler.

    Windows → ``os.startfile``; macOS → ``open``; Linux/other → ``xdg-open``.
    Best-effort UX: never raises. Returns ``True`` if the open was dispatched,
    ``False`` on failure (missing opener, bad path, unsupported environment).
    """
    target_str = str(target)
    try:
        if sys.platform == "win32":
            os.startfile(target_str)  # type: ignore[attr-defined]  # Windows-only, guarded
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target_str])
        else:
            subprocess.Popen(["xdg-open", target_str])
        return True
    except Exception:  # noqa: BLE001 — opening is best-effort; never crash the caller
        logger.debug("open_path: could not open %s", target_str, exc_info=True)
        return False
