"""The "moved" marker: this NAVIG now lives on another machine.

``navig move out`` writes it after the bundle is made and verified. While it exists, the
daemon entry, ``navig gateway start`` and ``navig service start`` refuse to run: the
identity (``deck.api_key`` — the relay tenant the Telegram bot reaches) now belongs to
the new machine, and two brains on one tenant would fight over every update.
``navig move undo`` removes it.

It lives in ``<config_dir>/daemon/`` beside the stop flag, which ``move out`` never packs,
so a bundle can never carry the marker to the new machine. Import-light on purpose: the
daemon entry reads it on every boot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MARKER_NAME = "moved.json"


def marker_path() -> Path:
    from navig.platform import paths

    return paths.config_dir() / "daemon" / MARKER_NAME


def read_moved() -> dict[str, Any] | None:
    """The marker's contents, or None when this NAVIG has not moved. An unreadable marker
    still counts as moved: refusing to start a brain that might be a duplicate is the
    safe side, and ``navig move undo`` clears it either way."""
    try:
        path = marker_path()
    except Exception:  # noqa: BLE001 — no resolvable home: no marker can live there
        # The daemon entry boots through an unresolvable home on purpose (its chdir is
        # best-effort too); the marker must not be the thing that blocks it.
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"unreadable": True}
    except (OSError, ValueError):
        return {"unreadable": True}


def write_moved(info: dict[str, Any]) -> Path:
    from navig.core.yaml_io import atomic_write_text

    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"moved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **info}
    atomic_write_text(path, json.dumps(data, indent=2))
    return path


def clear_moved() -> bool:
    path = marker_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def moved_message(info: dict[str, Any] | None = None) -> str:
    """One sentence for every refusal site, so they all say the same thing."""
    info = info if info is not None else (read_moved() or {})
    when = str(info.get("moved_at") or "")[:10]
    where = f" (bundle: {info['bundle']})" if info.get("bundle") else ""
    return (
        f"This NAVIG moved to another machine{' on ' + when if when else ''}{where}. "
        "It stays stopped here so two machines never answer as the same NAVIG. "
        "To use it on this machine again: navig move undo"
    )


__all__ = ["clear_moved", "marker_path", "moved_message", "read_moved", "write_moved"]
