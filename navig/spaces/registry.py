"""Spaces registry — the brain's index of known workshops (enable/disable).

`~/.navig/spaces.json`:
    { "version": 1,
      "active": "<abs path>",
      "spaces": [ { id, name, path, source, enabled, last_active } ] }

Discovery auto-registers spaces found under ``spaces.roots`` as enabled. The
deck/extension toggle ``enabled`` (hidden from switcher + not merged) and set the
active space. The folder itself is always usable when you're inside it — the
registry only governs *global* visibility/activation.

Atomic JSON read/write mirrors the gateway ``cron_jobs.json``/``tasks.json`` pattern.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.core.yaml_io import atomic_write_text
from navig.platform import paths


def _registry_file() -> Path:
    return paths.config_dir() / "spaces.json"


def _norm(p: str | Path) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return str(p)


def load_registry() -> dict[str, Any]:
    f = _registry_file()
    if not f.exists():
        return {"version": 1, "active": None, "spaces": []}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry is not an object")
        data.setdefault("version", 1)
        data.setdefault("active", None)
        data.setdefault("spaces", [])
        if not isinstance(data["spaces"], list):
            data["spaces"] = []
        return data
    except Exception:  # noqa: BLE001 — corrupt registry must not break discovery
        return {"version": 1, "active": None, "spaces": []}


def save_registry(reg: dict[str, Any]) -> None:
    try:
        atomic_write_text(_registry_file(), json.dumps(reg, indent=2) + "\n")
    except OSError:
        pass  # best-effort


def _find(reg: dict[str, Any], id_or_path: str) -> dict[str, Any] | None:
    rp = _norm(id_or_path)
    for e in reg["spaces"]:
        if e.get("id") == id_or_path or _norm(e.get("path", "")) == rp:
            return e
    return None


def register(
    path: str | Path,
    *,
    id: str | None = None,
    name: str | None = None,
    source: str = "root",
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a space in the registry. Existing ``enabled`` is preserved."""
    reg = load_registry()
    rp = _norm(path)
    existing = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if existing is not None:
        if id:
            existing["id"] = id
        if name:
            existing["name"] = name
        existing["source"] = source
        save_registry(reg)
        return existing
    entry = {
        "id": id or Path(rp).name,
        "name": name or Path(rp).name,
        "path": rp,
        "source": source,
        "enabled": enabled,
        "last_active": None,
    }
    reg["spaces"].append(entry)
    save_registry(reg)
    return entry


def ensure_registered(
    path: str | Path, *, id: str | None = None, name: str | None = None, source: str = "root"
) -> None:
    """Register *path* only if unknown — idempotent, writes at most once per space."""
    reg = load_registry()
    rp = _norm(path)
    if any(_norm(e.get("path", "")) == rp for e in reg["spaces"]):
        return
    reg["spaces"].append({
        "id": id or Path(rp).name, "name": name or Path(rp).name,
        "path": rp, "source": source, "enabled": True, "last_active": None,
    })
    save_registry(reg)


def set_enabled(id_or_path: str, enabled: bool) -> bool:
    reg = load_registry()
    e = _find(reg, id_or_path)
    if e is None:
        return False
    e["enabled"] = enabled
    save_registry(reg)
    return True


def forget(id_or_path: str) -> bool:
    reg = load_registry()
    rp = _norm(id_or_path)
    before = len(reg["spaces"])
    reg["spaces"] = [
        e for e in reg["spaces"]
        if not (e.get("id") == id_or_path or _norm(e.get("path", "")) == rp)
    ]
    if len(reg["spaces"]) != before:
        save_registry(reg)
        return True
    return False


def is_enabled(path: str | Path) -> bool:
    """Unknown spaces default to enabled (they get auto-registered on discovery)."""
    e = next((x for x in load_registry()["spaces"] if _norm(x.get("path", "")) == _norm(path)), None)
    return True if e is None else bool(e.get("enabled", True))


def mark_active(path: str | Path) -> None:
    reg = load_registry()
    rp = _norm(path)
    reg["active"] = rp
    e = next((x for x in reg["spaces"] if _norm(x.get("path", "")) == rp), None)
    if e is not None:
        e["last_active"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_registry(reg)
