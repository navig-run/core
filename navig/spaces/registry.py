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

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.core.json_io import (
    JsonReadError,
    atomic_write_json,
    load_json_for_update,
    load_json_safe,
)
from navig.debug_logger import get_debug_logger
from navig.platform import paths

logger = get_debug_logger()

_DEFAULT_REGISTRY: dict[str, Any] = {"version": 1, "active": None, "spaces": []}


def _registry_file() -> Path:
    return paths.config_dir() / "spaces.json"


def _norm(p: str | Path) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return str(p)


def source_for(path: str | Path) -> str:
    """``"root"`` for a space living directly under ``~/.navig/spaces``, else ``"external"``.

    The one rule for the ``source`` column. Three callers each had their own: ``wire``
    used a string prefix (fragile to case and separators on Windows), ``space init``
    compared parents, and ``space doctor --fix`` hardcoded ``"root"`` — so an unregistered
    project folder repaired by doctor was filed as a spaces-root resident. Compared on
    normalised paths, direct children only: a space nested deeper is somebody's
    sub-space, not a root one.
    """
    try:
        return "root" if Path(_norm(path)).parent == Path(_norm(paths.spaces_dir())) else "external"
    except Exception:  # noqa: BLE001
        return "external"


def _normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return dict(_DEFAULT_REGISTRY)
    data.setdefault("version", 1)
    data.setdefault("active", None)
    if not isinstance(data.get("spaces"), list):
        data["spaces"] = []
    return data


def load_registry() -> dict[str, Any]:
    """Read-only view for discovery / list / is_enabled / is_trusted — degrades to a
    fresh registry on ANY failure (a corrupt or transiently-locked file must not break
    discovery). NEVER use this for a load-modify-save; use ``load_registry_for_update``."""
    return _normalize(load_json_safe(_registry_file(), default=dict(_DEFAULT_REGISTRY)))


def load_registry_for_update() -> dict[str, Any]:
    """Load for a load-MODIFY-save. Raises :class:`JsonReadError` when the file exists
    but is transiently unreadable (a lock that outlives the retries), so a mutator ABORTS
    its save instead of persisting an empty registry over every space and its
    trust/enabled/active state. A corrupt file is quarantined ``spaces.json.corrupt`` and
    then treated as empty (the bytes are already lost)."""
    return _normalize(load_json_for_update(_registry_file(), default=dict(_DEFAULT_REGISTRY)))


def _load_for_mutation() -> dict[str, Any] | None:
    """The registry to mutate, or ``None`` when it is transiently unreadable — in which
    case the caller MUST skip its save. Silently wiping every space (and the user's
    trust decisions) is far worse than skipping one update; the lock is temporary."""
    try:
        return load_registry_for_update()
    except JsonReadError as exc:
        logger.warning("spaces registry unreadable — skipping update to avoid wiping it: %s", exc)
        return None


def save_registry(reg: dict[str, Any]) -> None:
    try:
        atomic_write_json(reg, _registry_file())
    except OSError:
        pass  # best-effort


def _find(reg: dict[str, Any], id_or_path: str) -> dict[str, Any] | None:
    rp = _norm(id_or_path)
    for e in reg["spaces"]:
        if e.get("id") == id_or_path or _norm(e.get("path", "")) == rp:
            return e
    return None


def entry_for(path: str | Path) -> dict[str, Any] | None:
    """The registry row for *path* (read-only view), or None when unregistered."""
    rp = _norm(path)
    return next((e for e in load_registry()["spaces"] if _norm(e.get("path", "")) == rp), None)


def id_taken_by_another_path(space_id: str, path: str | Path) -> str | None:
    """The path already registered under *space_id*, if it is not *path* — else None.

    ``register`` keys on PATH, so a new folder that merely shares a NAME with an existing
    space appended a second entry with the same id, and ``_find(id)`` then answered
    whichever came first: ``cd ~/projects/homelab && navig space init`` silently made
    ``navig space use homelab`` ambiguous and listed two ``homelab`` rows. Callers that
    are about to register ask this first and refuse with the other path named.
    """
    reg = load_registry()
    rp = _norm(path)
    for e in reg.get("spaces", []):
        if e.get("id") == space_id and _norm(e.get("path", "")) != rp:
            return str(e.get("path", ""))
    return None


def register(
    path: str | Path,
    *,
    id: str | None = None,
    name: str | None = None,
    source: str = "root",
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a space in the registry. Existing ``enabled`` is preserved.

    Keys on PATH. It does not police id uniqueness — see ``id_taken_by_another_path``,
    which every user-facing entry point (init, wire, doctor --fix) consults first.
    """
    rp = _norm(path)
    entry = {
        "id": id or Path(rp).name,
        "name": name or Path(rp).name,
        "path": rp,
        "source": source,
        "enabled": enabled,
        "last_active": None,
    }
    reg = _load_for_mutation()
    if reg is None:
        return entry  # registry locked — not persisted now; usable in-memory, next write persists
    existing = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if existing is not None:
        if id:
            existing["id"] = id
        if name:
            existing["name"] = name
        existing["source"] = source
        save_registry(reg)
        return existing
    reg["spaces"].append(entry)
    save_registry(reg)
    return entry


def ensure_registered(
    path: str | Path, *, id: str | None = None, name: str | None = None, source: str = "root"
) -> None:
    """Register *path*, and correct a drifted id. Writes only when something changed.

    ⚠ This used to return the moment the PATH was known, so an entry written before the
    space had a manifest kept its FOLDER name as its id forever — `company-space` for a
    space canonically called `company`. All 19 entries on a real install were like that,
    which made a lookup by canonical id miss precisely the spaces that needed one.

    The id is a derived LABEL, not a key held anywhere else: `_find` matches by id or
    path, `is_enabled`/`is_trusted` take a path, and `enabled`/`trusted` live on the row
    — so correcting it preserves every stored state.
    """
    reg = _load_for_mutation()
    if reg is None:
        return  # registry locked — skip; a corrupt/locked read must not wipe it
    rp = _norm(path)
    existing = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if existing is not None:
        if id and existing.get("id") != id:
            existing["id"] = id
            if name:
                existing["name"] = name
            save_registry(reg)
        return
    reg["spaces"].append({
        "id": id or Path(rp).name, "name": name or Path(rp).name,
        "path": rp, "source": source, "enabled": True, "last_active": None,
    })
    save_registry(reg)


def set_enabled(id_or_path: str, enabled: bool) -> bool:
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — leave every space untouched
    e = _find(reg, id_or_path)
    if e is None:
        return False
    e["enabled"] = enabled
    save_registry(reg)
    return True


def forget(id_or_path: str) -> bool:
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — don't drop anyone
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


def rename(path: str | Path, new_id: str) -> str | None:
    """Re-key the entry for *path* as *new_id*; returns the id it had, or None if unregistered.

    The registry half of ``navig space rename``. Keys on PATH like ``register`` and
    ``ensure_registered`` do, so an entry whose id had drifted from its manifest is
    corrected rather than missed. Refuses (``ValueError``) when *new_id* already belongs
    to a different path — the one-id-one-space rule that init, wire and doctor enforce on
    the way in must hold on a rename too. The display ``name`` follows the id only when it
    was the same string (a space named after its id keeps that property; a space with its
    own display name keeps that instead). Never adds or drops a row.
    """
    reg = _load_for_mutation()
    if reg is None:
        return None  # registry locked — rename nothing rather than half of it
    rp = _norm(path)
    entry = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if entry is None:
        return None
    holder = next(
        (e for e in reg["spaces"] if e.get("id") == new_id and e is not entry), None
    )
    if holder is not None:
        raise ValueError(f"'{new_id}' is already the id of {holder.get('path', '?')}")
    old_id = str(entry.get("id") or "")
    if entry.get("name") in (old_id, None, ""):
        entry["name"] = new_id
    entry["id"] = new_id
    save_registry(reg)
    return old_id


def repath(old_path: str | Path, new_path: str | Path) -> bool:
    """Point the row for *old_path* at *new_path*, preserving every other field.

    The registry keys on PATH, so a space whose FOLDER moves (``space rename
    --move-folder``) needs its row carried across — and `forget` + `register` would
    reset the columns that are decisions rather than derivations: ``enabled``,
    ``trusted``, ``last_active``. Refuses (returns False) when *new_path* already has
    a row, so two rows can never claim one folder. The ``active`` pointer is carried
    too when it named the old path.
    """
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked - move nothing rather than half of it
    op, np = _norm(old_path), _norm(new_path)
    if op == np:
        return False
    entry = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == op), None)
    if entry is None:
        return False
    if any(_norm(e.get("path", "")) == np for e in reg["spaces"]):
        return False
    entry["path"] = np
    if _norm(reg.get("active") or "") == op:
        reg["active"] = np
    save_registry(reg)
    return True


def is_enabled(path: str | Path) -> bool:
    """Unknown spaces default to enabled (they get auto-registered on discovery)."""
    e = next((x for x in load_registry()["spaces"] if _norm(x.get("path", "")) == _norm(path)), None)
    return True if e is None else bool(e.get("enabled", True))


def is_trusted(path: str | Path) -> bool | None:
    """Space trust for bundled active capabilities (.navig/plugins, hooks, MCP).

    Returns True/False when the user has decided, None when never asked.
    Unknown spaces are None (untrusted until confirmed) — passive content
    (skills/prompts/personas) does not require trust.
    """
    e = next((x for x in load_registry()["spaces"] if _norm(x.get("path", "")) == _norm(path)), None)
    if e is None or "trusted" not in e:
        return None
    return bool(e.get("trusted"))


def set_trusted(id_or_path: str, trusted: bool) -> bool:
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — never silently reset trust decisions
    e = _find(reg, id_or_path)
    if e is None:
        return False
    e["trusted"] = trusted
    save_registry(reg)
    return True


def mark_active(path: str | Path) -> None:
    reg = _load_for_mutation()
    if reg is None:
        return  # registry locked — don't lose every space to record an active pointer
    rp = _norm(path)
    reg["active"] = rp
    e = next((x for x in reg["spaces"] if _norm(x.get("path", "")) == rp), None)
    if e is not None:
        e["last_active"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_registry(reg)
