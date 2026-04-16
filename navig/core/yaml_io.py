"""
navig.core.yaml_io — Atomic YAML / text I/O and shadow-execution logging.

Extracted from config.py for reuse across modules (Facade Pattern).

Public surface:
    atomic_write_yaml   — write any Python object as YAML atomically
    atomic_write_text   — write a plain string atomically
    safe_load_yaml      — read a YAML file without raising
    load_yaml_with_lines — read YAML and capture key → line-number mapping
    log_shadow_anomaly  — append a JSONL event for performance/divergence tracking
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# Retry/back-off constants for Windows file-lock contention.
_ATOMIC_REPLACE_RETRIES = 3
_ATOMIC_REPLACE_BACKOFF_BASE = 0.05  # seconds

# Back-compat aliases (used by config.py internals).
ATOMIC_REPLACE_RETRIES = _ATOMIC_REPLACE_RETRIES
ATOMIC_REPLACE_BACKOFF_BASE_SECONDS = _ATOMIC_REPLACE_BACKOFF_BASE


# ─────────────────────────────────────────────────────────────────────────────
# Shadow Execution Logging
# ─────────────────────────────────────────────────────────────────────────────

# Resolved once at import time; callers never pay the config_dir() cost again.
_PERF_DIR: Path = config_dir() / "perf"


def log_shadow_anomaly(log_name: str, event: str, data: dict[str, Any]) -> None:
    """Append a shadow-execution anomaly event to a JSONL performance log.

    Used by performance-tracking subsystems to record divergences between
    fast-path cache results and canonical slow-path parses.

    Args:
        log_name: Base name for the log file (without extension).
        event:    Event-type identifier string.
        data:     Arbitrary context payload.

    Note:
        Never raises — logging failures must never affect the calling code path.
    """
    try:
        _PERF_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _PERF_DIR / f"{log_name}.jsonl"
        entry = {"ts": time.time(), "event": event, "data": data}
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass  # Logging failures must never propagate


# ─────────────────────────────────────────────────────────────────────────────
# Safe YAML Loading
# ─────────────────────────────────────────────────────────────────────────────


def safe_load_yaml(filepath: str | Path) -> Any:
    """Read and parse a YAML file, returning ``None`` on any failure.

    Uses :func:`yaml.safe_load` exclusively (never ``yaml.load``).
    Returns ``None`` when the file does not exist, is empty, or is invalid YAML.
    """
    fp = Path(filepath)
    if not fp.is_file():
        return None
    try:
        return yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to parse YAML from %s", fp, exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Atomic YAML Writing
# ─────────────────────────────────────────────────────────────────────────────


def atomic_write_yaml(
    data: Any, filepath: Path | str, allow_unicode: bool = False
) -> None:
    """Write *data* as YAML to *filepath* atomically.

    Writes to a temporary file in the same directory, then renames it over the
    target.  On Windows, retries up to ``_ATOMIC_REPLACE_RETRIES`` times with
    exponential back-off to survive transient antivirus file-locking.

    Args:
        data:          Python object to serialise.
        filepath:      Destination path.
        allow_unicode: Passed to :func:`yaml.dump`.

    Raises:
        PermissionError: If the rename fails after all retries.
        OSError:         For other unrecoverable I/O errors.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=filepath.parent, prefix=".tmp_yaml_", suffix=".yaml"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.dump(
                data,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=allow_unicode,
            )
        _atomic_replace(tmp_path, filepath)
        tmp_path = None  # ownership transferred
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Atomic Plain-Text Writing
# ─────────────────────────────────────────────────────────────────────────────


def atomic_write_text(
    path: Path | str, content: str, *, encoding: str = "utf-8"
) -> None:
    """Write *content* to *path* atomically.

    On POSIX, ``os.replace`` is atomic at the filesystem level.  On Windows it
    is best-effort atomic (Win32 ``MoveFileEx``).  The temporary file is always
    created in the same directory as *path* to guarantee the rename stays on
    the same filesystem.

    Args:
        path:     Destination path.  Parent directories are created as needed.
        content:  Text to write.
        encoding: Character encoding (default ``utf-8``).

    Raises:
        OSError: If all retry attempts fail.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    last_exc: Exception | None = None
    for attempt in range(_ATOMIC_REPLACE_RETRIES):
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.tmp",
            suffix=".navig~",
        )
        tmp_path: Path | None = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            _atomic_replace(tmp_path, path)
            return
        except PermissionError as exc:
            # Windows: transient lock by antivirus / backup agent.
            last_exc = exc
            tmp_path.unlink(missing_ok=True)
            tmp_path = None
            backoff = _ATOMIC_REPLACE_BACKOFF_BASE * (2 ** attempt)
            time.sleep(backoff)
        except Exception:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise

    raise last_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Internal: atomic rename with Windows retry
# ─────────────────────────────────────────────────────────────────────────────


def _atomic_replace(src: Path, dst: Path) -> None:
    """Rename *src* to *dst* with Windows antivirus-lock retry."""
    import sys

    for attempt in range(_ATOMIC_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _ATOMIC_REPLACE_RETRIES - 1 or sys.platform != "win32":
                raise
            time.sleep(_ATOMIC_REPLACE_BACKOFF_BASE * (attempt + 1))


# ─────────────────────────────────────────────────────────────────────────────
# YAML load with line-number mapping
# ─────────────────────────────────────────────────────────────────────────────

YamlPathItem = str | int
YamlPath = tuple[YamlPathItem, ...]


@dataclass(frozen=True)
class YamlDocument:
    """Parsed YAML data with a best-effort key → 1-based line-number map."""

    data: Any
    line_map: dict[YamlPath, int]


def _node_to_python(
    node: yaml.Node, path: YamlPath, line_map: dict[YamlPath, int]
) -> Any:
    """Recursively convert a PyYAML node tree to Python objects, recording line numbers."""
    if hasattr(node, "start_mark") and node.start_mark is not None:
        line_map.setdefault(path, int(node.start_mark.line) + 1)

    if isinstance(node, yaml.ScalarNode):
        return node.value

    if isinstance(node, yaml.SequenceNode):
        return [
            _node_to_python(child, path + (idx,), line_map)
            for idx, child in enumerate(node.value)
        ]

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


def load_yaml_with_lines(path: Path | str) -> YamlDocument:
    """Load YAML and return a :class:`YamlDocument` with key → line-number mapping.

    Uses PyYAML's composed-node API so line numbers are captured without an
    extra dependency.  Empty files return ``data=None`` at line 1.
    """
    text = Path(path).read_text(encoding="utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        return YamlDocument(data=None, line_map={(): 1})

    line_map: dict[YamlPath, int] = {}
    data = _node_to_python(node, (), line_map)
    return YamlDocument(data=data, line_map=line_map)


# ─────────────────────────────────────────────────────────────────────────────
# Back-compat aliases
# ─────────────────────────────────────────────────────────────────────────────

_atomic_write_yaml = atomic_write_yaml
_log_shadow_anomaly = log_shadow_anomaly
