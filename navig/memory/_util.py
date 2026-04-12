"""
navig.memory._util — Shared utility functions for the memory subpackage.

Consolidates the ``_debug_log`` helper that was previously copy-pasted
across 9+ modules in this package.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically using temp-file + replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp_snapshot_", suffix=".jsonl")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        for attempt in range(3):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 2 or sys.platform != "win32":
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _debug_log(message: str) -> None:
    """Best-effort debug logging for memory operations.

    Creates a :class:`~navig.debug_logger.DebugLogger` lazily and logs
    under the ``"memory"`` category.  Never raises — logging failures
    must not interrupt memory operations.
    """
    try:
        from navig.debug_logger import DebugLogger

        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
