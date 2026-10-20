"""The preset catalog — file presets layered under the operator's saved rows.

Sources, lowest precedence first (a later source with the same id wins):

1. builtin   ``navig/builtin/presets/*.preset.yaml``      (ships in the wheel)
2. plugin    ``<plugin package>/presets/*.preset.yaml``   (e.g. navig-audio's enhance modes)
3. user      ``store_dir()/presets/*.preset.yaml``         (hand-written files)
4. project   ``<project>/.navig/presets/*.preset.yaml``
5. space     ``<active space>/.navig/presets/*.preset.yaml``
6. library   rows in the prompt library database (``presets`` table): everything
   created, edited, duplicated or imported from a NAVIG surface.

File presets are read-only. Editing one saves a library row with the same id (an
*override*); :func:`reset_preset` deletes that row and the file preset shows again.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from navig.presets.model import (
    FORMAT_VERSION,
    TOOLS,
    Preset,
    PresetError,
    preset_from_dict,
)

logger = logging.getLogger(__name__)

EXPORT_KIND = "navig-preset"
_FILE_GLOB = "*.preset.yaml"
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# Last scan's malformed files, for `navig presets list` / doctor-style reporting.
_load_errors: list[dict[str, str]] = []


def _store():
    from navig.store.prompt_library import get_prompt_store

    return get_prompt_store()


# ── File sources ─────────────────────────────────────────────────────


def preset_roots() -> list[tuple[str, Path]]:
    """``(source, dir)`` pairs to scan, lowest precedence first. Missing dirs skipped."""
    roots: list[tuple[str, Path]] = []
    try:
        from navig.platform.paths import builtin_store_dir

        roots.append(("builtin", builtin_store_dir() / "presets"))
    except Exception:  # noqa: BLE001 — discovery degrades, never raises
        logger.debug("builtin preset dir unavailable", exc_info=True)
    try:
        from navig.plugins.package import plugin_capability_dirs

        roots.extend(("plugin", d) for d in plugin_capability_dirs("presets"))
    except Exception:  # noqa: BLE001
        logger.debug("plugin preset dirs unavailable", exc_info=True)
    try:
        from navig.platform.paths import store_dir

        roots.append(("user", store_dir() / "presets"))
    except Exception:  # noqa: BLE001
        logger.debug("user preset dir unavailable", exc_info=True)
    project = _project_root()
    if project is not None:
        roots.append(("project", project / ".navig" / "presets"))
    space = _space_root()
    if space is not None and (project is None or space.resolve() != project.resolve()):
        roots.append(("space", space / ".navig" / "presets"))
    return [(src, d) for src, d in roots if d.is_dir()]


def _project_root() -> Path | None:
    try:
        from navig.platform.paths import find_app_root

        return find_app_root()
    except Exception:  # noqa: BLE001
        return None


def _space_root() -> Path | None:
    try:
        from navig.spaces.active import get_active_working_dir

        d = get_active_working_dir()
        return d if (d / ".navig").is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def context_root() -> Path | None:
    """The directory a text preset's ``references`` resolve under: space, else project."""
    return _space_root() or _project_root()


def _load_file(path: Path, source: str) -> Preset | None:
    import yaml

    from navig.core.yaml_io import read_text_retrying

    try:
        raw = yaml.safe_load(read_text_retrying(path))
        return preset_from_dict(raw, source=source, source_path=str(path))
    except (OSError, yaml.YAMLError, PresetError, TypeError, ValueError) as exc:
        _load_errors.append({"path": str(path), "error": str(exc)})
        logger.warning("preset file skipped: %s (%s)", path, exc)
        return None


def _file_presets() -> dict[str, Preset]:
    _load_errors.clear()
    out: dict[str, Preset] = {}
    for source, d in preset_roots():
        for f in sorted(d.rglob(_FILE_GLOB)):
            p = _load_file(f, source)
            if p is not None:
                out[p.id] = p  # later roots win
    return out


def load_errors() -> list[dict[str, str]]:
    return list(_load_errors)


# ── Merged view ──────────────────────────────────────────────────────


def _row_to_preset(row: dict[str, Any]) -> Preset | None:
    try:
        p = preset_from_dict(row, source=str(row.get("source") or "user"))
    except PresetError as exc:
        logger.warning("stored preset %r is invalid and was skipped: %s", row.get("id"), exc)
        return None
    p.favorite = bool(row.get("favorite"))
    p.archived = bool(row.get("archived"))
    p.created_at = str(row.get("created_at") or "")
    p.updated_at = str(row.get("updated_at") or "")
    ov = row.get("overrides")
    p.overrides = int(ov) if isinstance(ov, int) else None
    return p


def _all() -> dict[str, Preset]:
    merged = _file_presets()
    store = _store()
    favs = store.pref_all("fav:")
    for pid, p in merged.items():
        p.favorite = favs.get(f"fav:{pid}") == "1"
    for row in store.preset_rows():
        p = _row_to_preset(row)
        if p is not None:
            merged[p.id] = p
    return merged


def list_presets(
    *, tool: str = "", layer: str = "", q: str = "", favorites_first: bool = True
) -> list[Preset]:
    items = [p for p in _all().values() if not p.archived]
    if tool:
        items = [p for p in items if p.tool == tool]
    if layer:
        items = [p for p in items if p.layer == layer]
    if q:
        ql = q.lower()
        items = [
            p for p in items
            if ql in p.name.lower() or ql in p.id or ql in p.description.lower()
            or ql in p.category.lower() or any(ql in t for t in p.tags)
        ]
    key = (lambda p: (not p.favorite, p.tool, p.name.lower())) if favorites_first else (lambda p: (p.tool, p.name.lower()))
    return sorted(items, key=key)


def get_preset(pid: str) -> Preset | None:
    p = _all().get((pid or "").strip().lower())
    return None if p is None or p.archived else p


def file_preset(pid: str) -> Preset | None:
    """The read-only file preset behind ``pid`` (ignoring any library override)."""
    return _file_presets().get(pid)


def facets() -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {t: {} for t in TOOLS}
    for p in list_presets():
        counts[p.tool][p.layer] = counts[p.tool].get(p.layer, 0) + 1
    return {
        "tools": {t: {"count": sum(c.values()), "layers": c} for t, c in counts.items()},
        "defaults": {t: get_default(t) for t in TOOLS},
        "errors": load_errors(),
    }


# ── Writes ───────────────────────────────────────────────────────────


def _new_id(name: str, tool: str) -> str:
    base = "user/" + (_SLUG_STRIP.sub("-", name.lower()).strip("-") or tool.replace(".", "-"))
    base = base[:100]
    existing = _all()
    pid, n = base, 2
    while pid in existing:
        pid, n = f"{base}-{n}", n + 1
    return pid


def save_preset(data: dict[str, Any], *, source: str = "user") -> Preset:
    """Create (no id) or update (id) a preset. Updating a file preset overrides it."""
    data = dict(data)
    if not str(data.get("id") or "").strip():
        data["id"] = _new_id(str(data.get("name") or ""), str(data.get("tool") or ""))
    pid = str(data["id"]).strip().lower()
    current = get_preset(pid)
    if current is not None and not data.get("tool"):
        data["tool"] = current.tool
    if current is not None and current.tool != data.get("tool"):
        raise PresetError(f"tool: a preset cannot change tool ({current.tool} → {data.get('tool')})")
    if current is not None and current.source == "imported":
        source = "imported"  # editing does not launder trust; see trust_preset()
    base = file_preset(pid)
    p = preset_from_dict(data, source=source)
    if current is not None:
        p.version = max(p.version, current.version + 1) if current.source in ("user", "imported") else p.version
    row = p.to_dict()
    for k in ("trusted", "editable"):
        row.pop(k, None)
    if base is not None:
        row["overrides"] = base.version
    if current is not None:
        row["favorite"] = current.favorite
    stored = _store().preset_put(row)
    out = _row_to_preset(stored)
    if out is None:
        raise PresetError(f"preset {pid!r} could not be read back after saving")
    return out


def duplicate_preset(pid: str, name: str = "") -> Preset:
    src = get_preset(pid)
    if src is None:
        raise KeyError(pid)
    data = src.to_portable()
    data["name"] = name.strip() or f"{src.name} (copy)"
    data["id"] = ""
    data["version"] = 1
    return save_preset(data, source="imported" if src.source == "imported" else "user")


def trust_preset(pid: str) -> Preset:
    """Mark an imported preset as reviewed: its instructions then apply unquoted."""
    src = get_preset(pid)
    if src is None:
        raise KeyError(pid)
    if src.source != "imported":
        return src
    row = _store().preset_row(src.id)
    if row is None:
        raise KeyError(pid)
    row["source"] = "user"
    stored = _store().preset_put(row)
    out = _row_to_preset(stored)
    if out is None:
        raise PresetError(f"preset {pid!r} could not be read back")
    return out


def rename_preset(pid: str, name: str) -> Preset:
    src = get_preset(pid)
    if src is None:
        raise KeyError(pid)
    if not name.strip():
        raise PresetError("name: required")
    data = src.to_portable()
    data["name"] = name.strip()
    return save_preset(data, source=src.source if src.editable else "user")


def delete_preset(pid: str) -> bool:
    """Delete a library preset. A file preset cannot be deleted — reset or hide it."""
    pid = (pid or "").strip().lower()
    if file_preset(pid) is not None:
        row = _store().preset_row(pid)
        if row is None:
            raise PresetError(f"{pid!r} is a {file_preset(pid).source} preset; it can be overridden or reset, not deleted")  # type: ignore[union-attr]
        return _store().preset_delete(pid)  # deleting the override == reset
    done = _store().preset_delete(pid)
    if done:
        for tool in TOOLS:
            if get_default(tool) == pid:
                set_default(tool, None)
        _store().pref_set(f"fav:{pid}", None)
    return done


def reset_preset(pid: str) -> bool:
    """Drop the library override of a file preset. True if one was removed."""
    pid = (pid or "").strip().lower()
    if file_preset(pid) is None:
        raise PresetError(f"{pid!r} has no built-in version to reset to")
    return _store().preset_delete(pid)


def set_favorite(pid: str, value: bool | None = None) -> Preset:
    p = get_preset(pid)
    if p is None:
        raise KeyError(pid)
    val = (not p.favorite) if value is None else bool(value)
    store = _store()
    row = store.preset_row(p.id)
    if row is not None:
        row["favorite"] = val
        store.preset_put(row)
    else:
        store.pref_set(f"fav:{p.id}", "1" if val else None)
    p.favorite = val
    return p


def get_default(tool: str) -> str | None:
    pid = _store().pref_get(f"default:{tool}")
    return pid if pid and get_preset(pid) is not None else None


def set_default(tool: str, pid: str | None) -> None:
    if tool not in TOOLS:
        raise PresetError(f"tool: {tool!r} is not one of {', '.join(TOOLS)}")
    if pid is not None:
        p = get_preset(pid)
        if p is None:
            raise KeyError(pid)
        if p.tool != tool:
            raise PresetError(f"{pid!r} is a {p.tool} preset, not {tool}")
    _store().pref_set(f"default:{tool}", pid)


def record_use(ids: Iterable[str]) -> None:
    store = _store()
    for pid in ids:
        try:
            store.preset_record_use(pid)
        except Exception:  # noqa: BLE001 — usage stats never fail a generation
            logger.debug("preset use count failed for %s", pid, exc_info=True)


# ── Import / export ──────────────────────────────────────────────────

# Strings that look like credentials or machine-local paths never leave in an export.
_SECRETISH = re.compile(
    r"(vault:|vault://|sk-[A-Za-z0-9]{8,}|xox[abp]-|gh[pousr]_[A-Za-z0-9]{12,}|AKIA[0-9A-Z]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:api[_-]?key|token|password|secret)\s*[:=]\s*\S{6,})"
)
_ABS_PATH = re.compile(r"(?<![\w/])(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|root|var|etc)/)[^\s'\"]*")


def scrub(text: str) -> tuple[str, int]:
    """Redact secret-looking tokens and absolute paths. Returns (text, n_redactions)."""
    n = 0

    def _sub(_m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return "[redacted]"

    text = _SECRETISH.sub(_sub, text)
    text = _ABS_PATH.sub(_sub, text)
    return text, n


def export_preset(pid: str) -> tuple[dict[str, Any], list[str]]:
    """A portable, scrubbed document for ``pid`` plus warnings about what was removed."""
    p = get_preset(pid)
    if p is None:
        raise KeyError(pid)
    data = p.to_portable()
    warnings: list[str] = []
    redactions = 0

    def _walk(v: Any) -> Any:
        nonlocal redactions
        if isinstance(v, str):
            out, n = scrub(v)
            redactions += n
            return out
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        return v

    data = _walk(data)
    if redactions:
        warnings.append(f"{redactions} secret-looking value(s) or local path(s) were redacted")
    if data.get("references"):
        warnings.append(
            f"{len(data['references'])} reference(s) are listed by relative path only; "
            "the files themselves are not included"
        )
    return {"kind": EXPORT_KIND, "format_version": FORMAT_VERSION, "preset": data}, warnings


def import_preset(doc: Any, *, overwrite: bool = False) -> Preset:
    """Import an exported document. Imported presets are marked untrusted."""
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except ValueError as exc:
            raise PresetError(f"import: not valid JSON ({exc})") from None
    if not isinstance(doc, dict) or doc.get("kind") != EXPORT_KIND:
        raise PresetError(f"import: not a {EXPORT_KIND} document")
    if int(doc.get("format_version") or 0) > FORMAT_VERSION:
        raise PresetError("import: made by a newer NAVIG; update NAVIG to import it")
    raw = doc.get("preset")
    if not isinstance(raw, dict):
        raise PresetError("import: missing 'preset'")
    raw = dict(raw)
    pid = str(raw.get("id") or "").strip().lower()
    if pid and get_preset(pid) is not None and not overwrite:
        raw["id"] = ""
        raw["name"] = f"{raw.get('name') or pid} (imported)"
    elif pid and file_preset(pid) is not None:
        # Never let an import impersonate a builtin by overriding its id.
        raw["id"] = ""
    return save_preset(raw, source="imported")
