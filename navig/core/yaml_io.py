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

def _perf_dir() -> Path:
    """Return the perf-log directory, resolved lazily so env overrides apply."""
    return config_dir() / "perf"


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
        perf_dir = _perf_dir()
        perf_dir.mkdir(parents=True, exist_ok=True)
        log_file = perf_dir / f"{log_name}.jsonl"
        entry = {"ts": time.time(), "event": event, "data": data}
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass  # Logging failures must never propagate


# ─────────────────────────────────────────────────────────────────────────────
# Safe YAML Loading
# ─────────────────────────────────────────────────────────────────────────────


def read_text_retrying(path: Path | str, *, encoding: str = "utf-8") -> str:
    """Read *path* as text, retrying transient OS-level failures.

    The WRITE side of this module already survives Windows' transient locks (an
    antivirus or backup agent briefly holding the file) by retrying with back-off.
    The READ side did not, and the asymmetry was expensive: every writer replaces
    config.yaml via ``os.replace``, and a reader that opens the file during that
    replace can get a ``PermissionError`` / sharing violation. One such blip made
    :meth:`ConfigManager._load_global_config` return an EMPTY config — which the next
    save then wrote back over every setting the user had.

    So a transient OSError is retried here, exactly like the write. A failure that
    SURVIVES the retries is raised, never silently swallowed: a caller must be able to
    tell "this file is unreadable right now" from "this file is empty", because turning
    the first into the second is how data gets destroyed.
    """
    fp = Path(path)
    last_exc: OSError | None = None
    for attempt in range(_ATOMIC_REPLACE_RETRIES):
        try:
            return fp.read_text(encoding=encoding)
        except OSError as exc:  # PermissionError, sharing violation, transient I/O
            last_exc = exc
            if attempt < _ATOMIC_REPLACE_RETRIES - 1:
                time.sleep(_ATOMIC_REPLACE_BACKOFF_BASE * (2 ** attempt))
    logger.warning(
        "could not read %s after %d attempts: %s",
        fp, _ATOMIC_REPLACE_RETRIES, last_exc,
    )
    raise last_exc  # type: ignore[misc]


def safe_load_yaml(filepath: str | Path) -> Any:
    """Read and parse a YAML file, returning ``None`` on any failure.

    Uses :func:`yaml.safe_load` exclusively (never ``yaml.load``).
    Returns ``None`` when the file does not exist, is empty, or is invalid YAML.

    NOTE for read-modify-write callers: ``None`` is ambiguous — it means "no file",
    "empty file" AND "unreadable file". Treating it as ``{}`` and writing the result
    back DESTROYS the file's contents. Transient read failures are retried here so
    that case is rare, but a caller that writes back must still check whether the file
    exists with content before it trusts a ``None``.
    """
    fp = Path(filepath)
    if not fp.is_file():
        return None
    try:
        return yaml.safe_load(read_text_retrying(fp))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to parse YAML from %s", fp, exc_info=True)
        return None


class ConfigReadError(RuntimeError):
    """A YAML file exists WITH content but could not be read or parsed.

    Raised by :func:`load_yaml_for_update` so a read-modify-write caller ABORTS
    instead of overwriting real data with a default — the config-wipe class.
    Subclasses :class:`RuntimeError`, so a best-effort ``except Exception`` (as the
    onboarding steps use) catches it and simply skips the write.
    """


def load_yaml_for_update(
    filepath: str | Path, *, default: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Load a YAML mapping for a read-MODIFY-WRITE, refusing to mask an unreadable file.

    :func:`safe_load_yaml` collapses "missing", "empty" AND "unreadable" all into
    ``None`` — so a caller doing ``safe_load_yaml(p) or {}`` then writing the result
    back DESTROYS a file that was merely locked at read time (an antivirus/backup agent,
    or a read landing mid-``os.replace``). That is how ``deck.api_key`` — the install's
    identity — was erased twice in one night. This is the ONE helper every such caller
    should use; it distinguishes the cases the raw parser cannot:

    * file absent, OR present but genuinely empty / comments-only  -> returns ``default``
      (a fresh ``{}``); overwriting a blank file loses nothing.
    * file present WITH content but unreadable, invalid YAML, or not a mapping  -> raises
      :class:`ConfigReadError`, so the caller aborts instead of overwriting real data.

    Transient OS locks are already ridden out underneath (:func:`read_text_retrying`
    retries with back-off); only a failure that SURVIVES the retries becomes a refusal.
    Unlike a size>0 heuristic, a genuinely comments-only file is correctly treated as
    blank rather than falsely refused.
    """
    fp = Path(filepath)
    result: dict[str, Any] = {} if default is None else default
    if not fp.is_file():
        return result
    try:
        text = read_text_retrying(fp)
    except OSError as exc:  # survived the retries — unreadable, not empty
        raise ConfigReadError(f"{fp} exists but could not be read: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # has content, but not parseable — never overwrite it
        raise ConfigReadError(f"{fp} exists but is not valid YAML: {exc}") from exc
    if data is None:
        return result  # empty / comments-only — safe to treat as blank
    if not isinstance(data, dict):
        raise ConfigReadError(
            f"{fp} did not parse to a mapping (got {type(data).__name__}) — "
            "refusing to overwrite it."
        )
    return data


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
            # Flush to disk BEFORE the rename so a power-loss can't leave a
            # zero-length / partially-written config.yaml (atomic_write_text does
            # this too; the yaml path was missing it).
            fh.flush()
            os.fsync(fh.fileno())
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
        # os.fdopen() takes ownership of fd immediately — mark it open
        # BEFORE entering the with-block so the except-Exception branch
        # never attempts a double-close on an already-owned descriptor.
        fd_open = True
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
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
                tmp_path = None
            if attempt < _ATOMIC_REPLACE_RETRIES - 1:
                backoff = _ATOMIC_REPLACE_BACKOFF_BASE * (2 ** attempt)
                time.sleep(backoff)
        except Exception:
            # fd is already owned by os.fdopen; the with-block's __exit__
            # closes it on exception — no manual os.close() needed here.
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
            is_last = attempt == _ATOMIC_REPLACE_RETRIES - 1
            if is_last or sys.platform != "win32":
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
    extra dependency.  Empty or missing files return ``data=None`` at line 1.
    """
    p = Path(path)
    if not p.is_file():
        return YamlDocument(data=None, line_map={(): 1})
    text = p.read_text(encoding="utf-8")
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
