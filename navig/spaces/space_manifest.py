"""SpaceManifest — the parsed, authoritative identity of a folder-space.

A *space* is any folder with a ``.navig/`` directory. Its identity/intent lives
in one of (first found wins): ``.navig/space.json`` · ``.navig/space.config.json``
· ``.navig/space.manifest.json`` — or the community "file-at-root" variants. A
bare ``.navig/`` with no manifest is still a valid space (rooted at the dir).

Deliberately dependency-free (plain dict, no pydantic): the parser's whole job is
to *never crash a switch*. Every field is optional, unknown keys are kept, files
are read ``utf-8-sig``, and any parse error degrades to an empty manifest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Manifest filenames, priority order. Looked up under .navig/ first, then at the
# space root (the community/D:\spaces "file-at-root" shape).
MANIFEST_NAMES: tuple[str, ...] = ("space.json", "space.config.json", "space.manifest.json")
_YAML_NAMES: tuple[str, ...] = ("space.yaml", "space.yml")


class SpaceManifest:
    """Plain dict-backed view over a space manifest (superset of every real shape)."""

    __slots__ = ("data", "source_path")

    def __init__(self, data: dict[str, Any] | None = None, source_path: Path | None = None) -> None:
        self.data: dict[str, Any] = dict(data or {})
        self.source_path = source_path

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # ── Resolved views (accept every alias seen in the wild) ─────────────────────

    @property
    def resolved_id(self) -> str | None:
        d = self.data
        return d.get("id") or d.get("space_id") or d.get("spaceId") or d.get("name") or d.get("display_name")

    @property
    def resolved_name(self) -> str | None:
        d = self.data
        return d.get("display_name") or d.get("name") or d.get("id") or d.get("space_id") or d.get("spaceId")

    @property
    def root(self) -> str:
        r = self.data.get("root")
        return r if isinstance(r, str) and r.strip() else "."

    @property
    def resolved_formation(self) -> str | None:
        """Normalize flat vs nested formation → a single id."""
        f = self.data.get("formation")
        if isinstance(f, str) and f.strip():
            return f.strip()
        if isinstance(f, dict):
            v = f.get("id") or f.get("name")
            if v:
                return str(v)
        agent = self.data.get("agent")
        if isinstance(agent, dict):
            v = agent.get("formation")
            if v:
                return str(v)
        return None

    def _id_list(self, key: str) -> list[str]:
        val = self.data.get(key)
        if not isinstance(val, list):
            return []  # some manifests store skills/packages as a COUNT (int) — not an allow-list
        out: list[str] = []
        for it in val:
            if isinstance(it, str) and it.strip():
                out.append(it.strip())
            elif isinstance(it, dict):
                v = it.get("id") or it.get("name")
                if v:
                    out.append(str(v))
        return out

    @property
    def skill_allowlist(self) -> list[str]:
        """Non-empty = allow-list filter; empty/absent = all (back-compat)."""
        return self._id_list("skills")

    @property
    def package_allowlist(self) -> list[str]:
        return self._id_list("packages")

    @property
    def app_allowlist(self) -> list[str]:
        """The space's PINNED apps (desktop sidebar view-filter, locked 2026-07-09).

        Non-empty = only these module ids, in this order; empty/absent = all
        apps. A view-filter over the globally-enabled modules — never a second
        enablement store.
        """
        return self._id_list("apps")

    @property
    def app_sections(self) -> dict[str, dict[str, list[str]]]:
        """The space's per-app SECTION layouts (desktop sidebar view-filter).

        Shape: ``{"finance": {"order": ["Ledger", ...], "hidden": ["Leaks"]}}``.
        Absent/empty ⇒ every app renders its declared sections in registry order.

        A view-filter, exactly like :attr:`app_allowlist` — it can only reorder
        or hide sections an app already declares, never add one or change what
        the app can do. Malformed entries are DROPPED rather than raised on: a
        hand-edited manifest must not be able to break the sidebar, and an id
        that no longer matches simply stops applying.
        """
        val = self.data.get("app_sections")
        if not isinstance(val, dict):
            return {}
        out: dict[str, dict[str, list[str]]] = {}
        for app_id, layout in val.items():
            if not isinstance(app_id, str) or not app_id.strip() or not isinstance(layout, dict):
                continue
            entry: dict[str, list[str]] = {}
            for key in ("order", "hidden"):
                ids = layout.get(key)
                if isinstance(ids, list):
                    clean = [s.strip() for s in ids if isinstance(s, str) and s.strip()]
                    if clean:
                        entry[key] = clean
            if entry:
                out[app_id.strip()] = entry
        return out

    @property
    def books(self) -> str | None:
        """The finance BOOK this space works in (locked rule #6: Default space →
        personal books; Company space → company books).

        Absent/empty ⇒ the default (personal) book — full back-compat. A name
        selects a separate ledger partition; consumers sanitize it for storage.
        """
        v = self.data.get("books")
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None


def find_manifest_file(space_dir: Path) -> Path | None:
    """Return the manifest path for *space_dir*, or None for a bare ``.navig/``."""
    nav = space_dir / ".navig"
    for name in (*MANIFEST_NAMES, *_YAML_NAMES):
        p = nav / name
        if p.is_file():
            return p
    for name in (*MANIFEST_NAMES, *_YAML_NAMES):  # community file-at-root shape
        p = space_dir / name
        if p.is_file():
            return p
    return None


# Manifests already reported as unreadable this process. `load_space_manifest` runs
# once per space on EVERY scan (dozens of spaces, many times a minute), so without
# this a single bad file would bury the incident log in duplicates of itself.
# Per-process, so a daemon restart re-reports — which is what you want if it's
# still broken.
_reported_unreadable: set[str] = set()


def _report_unreadable(path: Path, reason: str) -> None:
    """Record that a manifest degraded to EMPTY. Never raises, never duplicates.

    The degradation itself is deliberate — a bad manifest must not break a space
    switch — but it is exactly the shape of failure this codebase keeps getting
    burned by: everything stays green while the space quietly loses its name, its
    pinned apps, and its finance ``books`` (so Finance silently starts writing to
    the personal ledger). Surviving it is right; hiding it is not.
    """
    key = str(path)
    if key in _reported_unreadable:
        return
    _reported_unreadable.add(key)
    try:
        from navig.core import incidents  # noqa: PLC0415 — keep the import off the scan path

        incidents.record(incidents.SPACE_MANIFEST_UNREADABLE, path=key, reason=reason)
    except Exception:  # noqa: BLE001 — an observation must never break the observed
        pass


def load_space_manifest(space_dir: Path) -> SpaceManifest:
    """Parse the space manifest permissively. Never raises.

    A manifest that cannot be parsed degrades to an EMPTY manifest (so a switch
    never breaks) **and** records a ``space_manifest_unreadable`` incident, which
    surfaces in ``navig doctor`` → Config Health and pushes through the
    config-incidents monitor. See :func:`_report_unreadable`.
    """
    path = find_manifest_file(space_dir)
    if path is None:
        return SpaceManifest()  # bare .navig/ — still a valid space
    try:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix in (".yaml", ".yml"):
            import yaml  # noqa: PLC0415
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            # Parsed, but not an object — a list or scalar at the top level. Same
            # empty-manifest outcome as a parse error, so report it the same way.
            _report_unreadable(path, f"top level is {type(data).__name__}, not an object")
            return SpaceManifest(source_path=path)
        # Some schemas nest everything under a top-level "space:" key.
        inner = data.get("space")
        if isinstance(inner, dict) and "id" not in data and "space_id" not in data:
            data = {**data, **inner}
        return SpaceManifest(data, source_path=path)
    except Exception as exc:  # noqa: BLE001 — permissive: a bad manifest must not break a switch
        _report_unreadable(path, f"{type(exc).__name__}: {exc}")
        return SpaceManifest(source_path=path)


def is_space_dir(path: Path) -> bool:
    """A folder is a space iff it has ``.navig/`` or a file-at-root manifest."""
    try:
        if (path / ".navig").is_dir():
            return True
    except OSError:
        return False
    return find_manifest_file(path) is not None


class ManifestNotWritable(Exception):
    """A space manifest can't be safely written in place — it's a YAML manifest
    (edit the key by hand) or unreadable / non-object JSON. Callers surface the
    message (CLI error, deck 409); we never clobber a file we couldn't read."""


def set_manifest_field(
    space_dir: Path, key: str, value: Any, *, id_hint: str | None = None
) -> Path:
    """Set — or DELETE, when *value* is None — one top-level manifest field,
    preserving every other key, and return the written path.

    The single safe writer for a JSON manifest: a bare ``.navig/`` (or missing
    dir) is bootstrapped with a minimal ``space.json`` (``id`` = *id_hint* or the
    folder name); a YAML manifest, or unreadable / non-object JSON, raises
    :class:`ManifestNotWritable` instead of being overwritten — a failed read
    must never become a destructive write (see the config-layer rule in
    CLAUDE.md). Writes pretty JSON with a trailing newline, matching the scaffold.

    The write is ATOMIC (temp-file + fsync + replace, via
    ``navig.core.json_io.atomic_write_json``). It used to truncate-then-write in
    place, which was survivable while this was an admin-frequency writer — but
    the desktop sidebar now calls it on every app/section drag, hide and
    reorder, so a crash, a full disk, or a sync client locking the file
    mid-write became a realistic way to leave ``space.json`` truncated. That
    matters more than it looks: :func:`load_space_manifest` deliberately
    swallows a corrupt manifest and returns an EMPTY one, so the space would
    silently lose its ``name``, its pinned ``apps``, and — worst — its
    ``books``, re-pointing Finance at the default personal ledger with every
    surface still green.
    """
    nav = space_dir / ".navig"
    path = find_manifest_file(space_dir)
    if path is not None and path.suffix in (".yaml", ".yml"):
        raise ManifestNotWritable(f"space manifest is YAML — edit its `{key}:` by hand")
    if path is None:  # bare .navig/ (or nothing yet) — create the minimal manifest
        nav.mkdir(parents=True, exist_ok=True)
        path = nav / MANIFEST_NAMES[0]
        data: dict[str, Any] = {"id": id_hint or space_dir.name}
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            raise ManifestNotWritable("space manifest is unreadable JSON — fix it by hand") from exc
        if not isinstance(data, dict):
            raise ManifestNotWritable("space manifest is not a JSON object")
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    # atomic_write_TEXT, not atomic_write_json: the json helper emits no trailing
    # newline, and this function's contract (and the scaffold it has to match) is
    # pretty JSON *with* one. Same atomicity, byte-identical output.
    from navig.core.yaml_io import (
        atomic_write_text,  # noqa: PLC0415 — keep import cost off the hot path
    )

    atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    return path
