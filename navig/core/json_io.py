"""Safe read-modify-write for JSON state stores — the JSON twin of
:func:`navig.core.yaml_io.load_yaml_for_update`.

A per-feature store that does ``json.load(...)`` / ``except: return {}`` and then
writes the result back on a mutation silently WIPES every record when the read fails
*transiently* (an OS lock, a half-written file mid-``os.replace``) or the file is
*corrupt*: the failed read hands back an empty ``{}`` that the save then persists over
the real data. That is the JSON form of the config-wipe class killed in YAML by
``load_yaml_for_update`` — but the AST guard (``test_no_config_wipe_pattern``) only sees
``safe_load_yaml``/``atomic_write_yaml``, so ``json.load``/``json.dump`` stores are
invisible to it. This module is the shared primitive so a store never has to hand-roll
the ~80 lines of retry/quarantine/atomic-write logic (as the first fix, #428, had to).

Built on the same primitives the YAML side uses: :func:`read_text_retrying` (rides out
transient locks, raises only when they persist) and :func:`atomic_write_text`
(temp-file + fsync + atomic ``os.replace``, with its own Windows retry).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from navig.core.yaml_io import ConfigReadError, atomic_write_text, read_text_retrying


class JsonReadError(ConfigReadError):
    """A JSON store could not be read — a transient lock / permission error that
    outlived the retries. Raised on the MUTATING path so a failed read never becomes a
    destructive write. Subclasses ``ConfigReadError`` so a caller may catch either."""


def _fresh(default: Any) -> Any:
    """A private, mutable copy of the caller's default (never the shared object)."""
    return {} if default is None else copy.deepcopy(default)


def _quarantine_corrupt(path: Path, text: str) -> None:
    """Preserve unparsable bytes beside the store as ``*.corrupt`` so nothing is
    silently lost, then let the caller start from the default. Best-effort."""
    try:
        (path.parent / (path.name + ".corrupt")).write_text(text, encoding="utf-8")
    except OSError:
        pass


def load_json_for_update(path: str | Path, *, default: Any = None) -> Any:
    """Load a JSON store for a read-MODIFY-write, refusing to mask an unreadable file.

    * absent, OR present but genuinely empty  → a fresh copy of ``default`` (``{}`` if
      ``None``); overwriting a blank file loses nothing.
    * present WITH content but transiently unreadable (lock survived the retries) →
      raises :class:`JsonReadError`, so the caller ABORTS the save rather than writing
      an empty store over every record.
    * present but corrupt (invalid JSON), OR of a different top-level type than
      ``default`` (e.g. a list where a dict-store expects a mapping) → the bytes are
      quarantined as ``*.corrupt`` and the default returned (the data is already lost;
      refusing forever would only wedge the store).
    """
    p = Path(path)
    if not p.is_file():
        return _fresh(default)
    try:
        text = read_text_retrying(p)  # rides out transient locks; raises OSError if they persist
    except OSError as exc:
        raise JsonReadError(f"{p} exists but could not be read: {exc}") from exc
    if not text.strip():
        return _fresh(default)  # empty / whitespace-only — safe to treat as blank
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        _quarantine_corrupt(p, text)
        return _fresh(default)
    # A shape mismatch is corruption too: merging a mutation into the default and saving
    # would drop whatever the (wrongly-typed) file actually held.
    if default is not None and not isinstance(data, type(default)):
        _quarantine_corrupt(p, text)
        return _fresh(default)
    return data


def load_json_safe(path: str | Path, *, default: Any = None) -> Any:
    """Read-only load for status/list views: degrade to ``default`` on ANY failure,
    because a read-only view must never crash and never writes back."""
    try:
        return load_json_for_update(path, default=default)
    except JsonReadError:
        return _fresh(default)


def atomic_write_json(
    data: Any, path: str | Path, *, indent: int = 2, sort_keys: bool = False
) -> None:
    """Serialise *data* and write it to *path* atomically (temp-file + fsync + atomic
    replace, with transient-lock retry) — a crash mid-write can't leave a truncated file
    that the next load would read as empty and then wipe over."""
    atomic_write_text(Path(path), json.dumps(data, indent=indent, sort_keys=sort_keys))
