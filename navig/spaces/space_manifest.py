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


def load_space_manifest(space_dir: Path) -> SpaceManifest:
    """Parse the space manifest permissively. Never raises."""
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
            return SpaceManifest(source_path=path)
        # Some schemas nest everything under a top-level "space:" key.
        inner = data.get("space")
        if isinstance(inner, dict) and "id" not in data and "space_id" not in data:
            data = {**data, **inner}
        return SpaceManifest(data, source_path=path)
    except Exception:  # noqa: BLE001 — permissive: a bad manifest must not break a switch
        return SpaceManifest(source_path=path)


def is_space_dir(path: Path) -> bool:
    """A folder is a space iff it has ``.navig/`` or a file-at-root manifest."""
    try:
        if (path / ".navig").is_dir():
            return True
    except OSError:
        return False
    return find_manifest_file(path) is not None
