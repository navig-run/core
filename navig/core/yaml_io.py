"""
navig.core.yaml_io — Atomic YAML I/O and shadow execution logging utilities.

Extracted from config.py for reuse across modules.

PR1 of config.py decomposition (Facade Pattern).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shadow Execution Logging
# ─────────────────────────────────────────────────────────────────────────────

_PERF_DIR = Path.home() / ".navig" / "perf"


def log_shadow_anomaly(log_name: str, event: str, data: dict) -> None:
    """
    Append a shadow-execution anomaly to a performance log.

    Used by QUANTUM VELOCITY K2 to track divergences between fast-path cache
    results and canonical slow-path parses.

    Args:
        log_name: Name of the log file (e.g., "shadow_config" → shadow_config.jsonl)
        event: Event type identifier
        data: Additional context data

    Note:
        Never raises - logging failures must not affect the main code path.
    """
    try:
        _PERF_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _PERF_DIR / f"{log_name}.jsonl"
        entry = {"ts": time.time(), "event": event, "data": data}
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass  # Logging failure must never affect the main code path


# ─────────────────────────────────────────────────────────────────────────────
# Atomic YAML Writing
# ─────────────────────────────────────────────────────────────────────────────


def atomic_write_yaml(data: Any, filepath: Path, allow_unicode: bool = False) -> None:
    """
    Safely write YAML data to disk atomically to prevent truncation during crashes.

    Uses a temporary file in the same directory followed by atomic rename.
    Handles Windows-specific issues like antivirus file locking.

    Args:
        data: Data structure to serialize as YAML
        filepath: Target file path
        allow_unicode: If True, allow non-ASCII characters in output

    Raises:
        PermissionError: If the file cannot be written after retries
        OSError: For other I/O errors
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Use a unique temp file in the same directory to avoid name collisions and
    # Windows Defender locking a stale config.tmp.yaml from a prior aborted run.
    fd, tmp_name = tempfile.mkstemp(dir=filepath.parent, prefix=".tmp_yaml_", suffix=".yaml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False, sort_keys=False, allow_unicode=allow_unicode
            )
        # On Windows, antivirus scanners can briefly lock a newly-written
        # file, causing os.replace() to raise PermissionError (WinError 5).
        # Retry up to 3 times with a short back-off before giving up.
        for attempt in range(3):
            try:
                os.replace(tmp_path, filepath)
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


# ─────────────────────────────────────────────────────────────────────────────
# Public API (backward compat aliases)
# ─────────────────────────────────────────────────────────────────────────────

# These underscore-prefixed names match existing usage in config.py
_atomic_write_yaml = atomic_write_yaml
_log_shadow_anomaly = log_shadow_anomaly  # Note: takes log_name as first arg now
