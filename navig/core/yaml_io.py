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

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

ATOMIC_REPLACE_RETRIES = 3
ATOMIC_REPLACE_BACKOFF_BASE_SECONDS = 0.05

# ─────────────────────────────────────────────────────────────────────────────
# Shadow Execution Logging
# ─────────────────────────────────────────────────────────────────────────────

_PERF_DIR = config_dir() / "perf"


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
# Safe YAML Loading
# ─────────────────────────────────────────────────────────────────────────────


def safe_load_yaml(filepath: str | Path) -> Any:
    """Read and parse a YAML file safely, returning *None* on any failure.

    Uses :func:`yaml.safe_load` (never ``yaml.load``) and returns *None*
    when the file does not exist, is empty, or contains invalid YAML.
    """
    filepath = Path(filepath)
    if not filepath.is_file():
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to parse YAML from %s", filepath, exc_info=True)
        return None


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
        for attempt in range(ATOMIC_REPLACE_RETRIES):
            try:
                os.replace(tmp_path, filepath)
                break
            except PermissionError:
                if attempt == (ATOMIC_REPLACE_RETRIES - 1) or sys.platform != "win32":
                    raise
                time.sleep(ATOMIC_REPLACE_BACKOFF_BASE_SECONDS * (attempt + 1))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort: skip on IO error
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Public API (backward compat aliases)
# ─────────────────────────────────────────────────────────────────────────────

# These underscore-prefixed names match existing usage in config.py
_atomic_write_yaml = atomic_write_yaml
_log_shadow_anomaly = log_shadow_anomaly  # Note: takes log_name as first arg now


# ─────────────────────────────────────────────────────────────────────────────
# YAML load with line numbers (absorbed from former yaml_utils.py)
# ─────────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass  # noqa: E402 — grouped with section

YamlPathItem = str | int
YamlPath = tuple[YamlPathItem, ...]


@dataclass(frozen=True)
class YamlDocument:
    """Parsed YAML with best-effort line-number mapping."""

    data: Any
    # Maps a path (tuple) to a 1-based line number.
    line_map: dict[YamlPath, int]


def _node_to_python(
    node: yaml.Node, path: YamlPath, line_map: dict[YamlPath, int]
) -> Any:
    if hasattr(node, "start_mark") and node.start_mark is not None:
        line_map.setdefault(path, int(node.start_mark.line) + 1)

    if isinstance(node, yaml.ScalarNode):
        return node.value

    if isinstance(node, yaml.SequenceNode):
        items: list[Any] = []
        for idx, child in enumerate(node.value):
            items.append(_node_to_python(child, path + (idx,), line_map))
        return items

    if isinstance(node, yaml.MappingNode):
        obj: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = _node_to_python(key_node, path + ("<key>",), line_map)
            if not isinstance(key, str):
                key = str(key)
            if hasattr(key_node, "start_mark") and key_node.start_mark is not None:
                line_map.setdefault(path + (key,), int(key_node.start_mark.line) + 1)
            obj[key] = _node_to_python(value_node, path + (key,), line_map)
        return obj

    return None


def load_yaml_with_lines(path: Path) -> YamlDocument:
    """Load YAML and capture a best-effort mapping of key paths -> line numbers.

    Uses PyYAML's composed node tree so we can include line numbers in
    validation errors without introducing an extra YAML dependency.
    """
    text = path.read_text(encoding="utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        return YamlDocument(data=None, line_map={(): 1})

    line_map: dict[YamlPath, int] = {}
    data = _node_to_python(node, (), line_map)
    return YamlDocument(data=data, line_map=line_map)
