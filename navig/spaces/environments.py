"""Unified Environments — active pointers, recents, classification (schema 1).

The data model behind NAVIG OS's Environments / Areas / Projects, built **on the
existing stores only** (no third engine):

* **Classification** — ``kind`` on the manifest (see
  :data:`navig.spaces.space_manifest.SPACE_KINDS`): data decides the UI role, never
  the folder name. Membership stays on ``relations.parent``; Projects are referenced
  by stable registry ids from each Environment's ``projects`` list (many-to-many).
* **Active pointers** — one Environment / Area / Project at a time, through the
  *same* canonical engine the active Space uses: ``NAVIG_*`` override → cache file
  (source of truth) → ``config.yaml`` mirror (best effort). Cache files:
  ``cache/active_environment.txt`` · ``active_area.txt`` · ``active_project.txt``.
  The cache lives under the SAME config dir as the space pointer, so
  ``NAVIG_CONFIG_DIR`` isolation covers every test.
* **Recent Environments** — a bounded, de-duplicated id list in config
  (``environment.recent``); project recents stay the Project Manager's own
  ("recently active" already exists there — one owner per fact).

Readers return ``None``/``()``/``[]`` rather than raising: a session must start
whatever the environment state is. Callers own their default (parity with
``resolve_active_space`` → ``get_active_space``).
"""

from __future__ import annotations

from pathlib import Path

from navig.spaces.space_manifest import SPACE_KINDS, load_space_manifest, set_manifest_field

#: Config mirror keys (cache file remains the source of truth, exactly like
#: ``active_space``: a failed config.yaml write must never lose the pointer).
_ACTIVE_KEYS = {
    "environment": ("environment", "active"),
    "area": ("area", "active"),
    "project": ("project", "active"),
}

#: Environment variables that override the cache/config, mirroring ``NAVIG_SPACE``.
_ENV_VARS = {
    "environment": "NAVIG_ENVIRONMENT",
    "area": "NAVIG_AREA",
    "project": "NAVIG_PROJECT",
}

#: How many Environment ids Home remembers (newest first).
RECENT_MAX = 10

RECENT_CONFIG_KEY = "environment.recent"


# ── Classification (manifest readers) ─────────────────────────────────────────


def space_kind(space_dir: Path) -> str:
    """The manifest's ``kind`` — ``"space"`` for anything legacy or malformed."""
    try:
        return load_space_manifest(space_dir).kind
    except Exception:  # noqa: BLE001 — classification must never break a reader
        return "space"


def is_environment(space_dir: Path) -> bool:
    return space_kind(space_dir) == "environment"


def is_area(space_dir: Path) -> bool:
    return space_kind(space_dir) == "area"


def classify(space_dir: Path) -> str:
    """Alias of :func:`space_kind` — named for call sites narrating the model."""
    return space_kind(space_dir)


# ── Active pointers ───────────────────────────────────────────────────────────


def _cache_file(pointer: str) -> Path:
    from navig.config import get_config_manager  # noqa: PLC0415 — keep import cost off

    return Path(get_config_manager().global_config_dir) / "cache" / f"active_{pointer}.txt"


def _read_config_mirror(section: str) -> str | None:
    """Best-effort config.yaml view (never the source of truth)."""
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cfg = get_config_manager().global_config
        if isinstance(cfg, dict):
            block = cfg.get(section)
            if isinstance(block, dict):
                val = block.get("active")
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_active(pointer: str) -> str | None:
    """env var → cache file → config mirror → None (the canonical-reader order)."""
    import os  # noqa: PLC0415

    env = os.environ.get(_ENV_VARS[pointer], "").strip()
    if env:
        return env
    try:
        text = _cache_file(pointer).read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass  # first run / unreadable → fall through to the mirror
    return _read_config_mirror(_ACTIVE_KEYS[pointer][0])


def _set_active(pointer: str, value: str | None) -> None:
    """Write the cache file (truth) and mirror best-effort — the `_set_active_space` way."""
    from navig.core.yaml_io import atomic_write_text, atomic_write_yaml  # noqa: PLC0415

    cf = _cache_file(pointer)
    cf.parent.mkdir(parents=True, exist_ok=True)
    if value:
        atomic_write_text(cf, value)
    else:
        cf.unlink(missing_ok=True)

    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cm = get_config_manager()
        gc = dict(cm.global_config)
        section, key = _ACTIVE_KEYS[pointer]
        block = gc.get(section)
        if not isinstance(block, dict):
            block = {}
        block[key] = value
        if value is None and not {k: v for k, v in block.items() if v}:
            gc.pop(section, None)
        else:
            gc[section] = block
        config_file = Path(cm.global_config_dir) / "config.yaml"
        atomic_write_yaml(gc, config_file, allow_unicode=True)
    except Exception:  # noqa: BLE001
        pass  # cache file is the source of truth; the yaml mirror is best effort


def resolve_active_environment() -> str | None:
    """The active Environment id, or None — canonical reader (env → cache → config)."""
    return _resolve_active("environment")


def resolve_active_area() -> str | None:
    return _resolve_active("area")


def resolve_active_project() -> str | None:
    return _resolve_active("project")


def set_active_environment(env_id: str | None) -> None:
    """Persist the active Environment (None clears it)."""
    _set_active("environment", (env_id or "").strip() or None)


def set_active_area(area_id: str | None) -> None:
    _set_active("area", (area_id or "").strip() or None)


def set_active_project(project_id: str | None) -> None:
    _set_active("project", (project_id or "").strip() or None)


# ── Recent Environments ───────────────────────────────────────────────────────


def recent_environments() -> list[str]:
    """Newest-first Environment ids (bounded, de-duplicated); garbage → []."""
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cfg = get_config_manager().global_config
        block = cfg.get("environment") if isinstance(cfg, dict) else None
        raw = block.get(RECENT_CONFIG_KEY.split(".", 1)[1]) if isinstance(block, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for it in raw:
            if isinstance(it, str) and it.strip() and it.strip() not in out:
                out.append(it.strip())
        return out[:RECENT_MAX]
    except Exception:  # noqa: BLE001
        return []


def push_recent_environment(env_id: str | None) -> list[str]:
    """Move *env_id* to the front of the recents (dedupe, cap) and persist; returns the list.

    Writes through ``cm.set_global`` — the canonical config write — so the in-memory
    manager and ``config.yaml`` move together (a raw file write here left
    ``recent_environments()`` reading stale cache within the same process).
    """
    env_id = (env_id or "").strip()
    current = recent_environments()
    if env_id:
        current = [env_id] + [e for e in current if e != env_id]
    current = current[:RECENT_MAX]
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        get_config_manager().set_global(RECENT_CONFIG_KEY, current)
    except Exception:  # noqa: BLE001
        pass  # recents are convenience; never fail a switch over them
    return current


# ── Environment ↔ Project membership (the PM-facing many-to-many) ────────────


def environment_manifests() -> dict[str, Path]:
    """Every registered space whose manifest says ``kind: environment`` → its dir.

    One pass, tolerant of broken manifests (they simply are not Environments).
    """
    from navig.spaces.relations import registered_spaces  # noqa: PLC0415

    out: dict[str, Path] = {}
    for sid, path in registered_spaces().items():
        if sid in out or not path.is_dir():
            continue
        try:
            if load_space_manifest(path).kind == "environment":
                out[sid] = path
        except Exception:  # noqa: BLE001 — an unreadable manifest is not an Environment
            continue
    return out


def environment_project_map() -> dict[str, list[str]]:
    """project id → sorted Environment ids that reference it (one scan, no N×M rereads)."""
    out: dict[str, list[str]] = {}
    for env_id, path in sorted(environment_manifests().items()):
        for pid in _project_refs_of(path):
            out.setdefault(pid, []).append(env_id)
    return out


def _project_refs_of(path: Path) -> tuple[str, ...]:
    """project_refs of *path*, never raising (an unreadable manifest refs nothing)."""
    try:
        return load_space_manifest(path).project_refs
    except Exception:  # noqa: BLE001
        return ()


def environments_containing(project_id: str) -> list[str]:
    """Sorted Environment ids referencing *project_id* (the Project's memberships)."""
    pid = (project_id or "").strip()
    return sorted(environment_project_map().get(pid, []))


def _env_dir_for_write(env_id: str) -> Path:
    """Resolve an Environment id for a membership WRITE (strict: real Environment only)."""
    from navig.spaces.relations import registered_spaces  # noqa: PLC0415

    reg = registered_spaces()
    path = reg.get((env_id or "").strip())
    if path is None or not path.is_dir():
        raise ValueError(f"environment '{env_id}' is not a registered space")
    if load_space_manifest(path).kind != "environment":
        raise ValueError(f"'{env_id}' is not an environment (manifest kind is not 'environment')")
    return path


def attach_project(env_id: str, project_id: str) -> list[str]:
    """Reference *project_id* from *env_id*'s manifest — idempotent, validated.

    The Project id must exist in the shared identity space (the Project Manager's
    ``resolve_project`` namespace). Returns the Environment's updated project list.
    Attaching to an Area (or any non-Environment) is refused: membership edges live
    on Environments, Areas hang off them via ``relations.parent``.
    """
    from navig.spaces.relations import registered_spaces  # noqa: PLC0415

    env_path = _env_dir_for_write(env_id)
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project id is required")
    if pid not in registered_spaces():
        raise ValueError(f"unknown project: {pid}")
    current = list(load_space_manifest(env_path).project_refs)
    if pid in current:
        return current  # idempotent
    current.append(pid)
    set_manifest_field(env_path, "projects", current)
    return current


def detach_project(env_id: str, project_id: str) -> list[str]:
    """Remove *project_id* from *env_id*'s manifest — idempotent.

    A stale id (Project since renamed/removed) detaches happily: cleanup must not
    require the gone target to still exist.
    """
    env_path = _env_dir_for_write(env_id)
    pid = (project_id or "").strip()
    current = [p for p in load_space_manifest(env_path).project_refs if p != pid]
    stored = list(load_space_manifest(env_path).project_refs)
    if current != stored:
        set_manifest_field(env_path, "projects", current or None)
    return current


__all__ = [
    "SPACE_KINDS",
    "RECENT_MAX",
    "attach_project",
    "classify",
    "detach_project",
    "environment_manifests",
    "environment_project_map",
    "environments_containing",
    "is_area",
    "is_environment",
    "push_recent_environment",
    "recent_environments",
    "resolve_active_area",
    "resolve_active_environment",
    "resolve_active_project",
    "set_active_area",
    "set_active_environment",
    "set_active_project",
    "space_kind",
]
