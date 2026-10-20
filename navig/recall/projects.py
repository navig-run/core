"""Map a conversation's working directory to the navig project it belongs to.

Never guesses from keywords. Resolution order:

1. Worktree folding — ``<repo>\\.dev\\worktrees\\<slug>`` belongs to ``<repo>``; a Codex
   worktree ``~/.codex/worktrees/<id>/<name>`` belongs to the project whose folder is
   ``<name>``.
2. The longest registered project root (``~/.navig/spaces.json``) that contains the path.
3. The nearest existing ancestor holding ``.git`` or ``.navig`` → its folder name.
4. ``shared``.

Paths are compared case-insensitively and separator-normalised on Windows, because the
same folder appears as ``C:\\projects\\…`` and ``C:\\projects\\…`` in one transcript.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SHARED = "shared"
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class ProjectRef:
    id: str
    root: str = ""  # "" for the shared bucket


def _key(path: str) -> str:
    p = os.path.normcase(os.path.normpath(path.strip().strip('"')))
    return p.rstrip("\\/")


def slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.casefold()).strip("-") or SHARED


def _fold_worktree(path: str) -> tuple[str, str | None]:
    """(path with any in-repo worktree suffix removed, codex worktree folder name or None)."""
    k = _key(path)
    sep = os.sep if os.sep in k else "/"
    for workdir in (".dev", ".navig_worktrees"):
        marker = os.path.normcase(f"{sep}{workdir}{sep}worktrees{sep}" if workdir == ".dev"
                                  else f"{sep}{workdir}{sep}")
        i = k.find(marker)
        if i >= 0:
            # `.navig_worktrees/<slug>/…` has no extra `worktrees` segment — it IS the dir.
            return path[:i], None
    codex = os.path.normcase(f"{sep}.codex{sep}worktrees{sep}")
    j = k.find(codex)
    if j >= 0:
        parts = [p for p in k[j + len(codex):].split(sep) if p]
        if len(parts) >= 2:
            return path, parts[1]
    return path, None


class ProjectResolver:
    """Resolves paths against one snapshot of the project registry (cached per path)."""

    def __init__(self, entries: list[tuple[str, str]] | None = None, home: str | None = None):
        if entries is None:
            entries = _registry_entries()
        # (normalised root, id), longest root first so the most specific project wins.
        self._roots = sorted(((_key(p), i) for i, p in entries if p and i), key=lambda t: -len(t[0]))
        self._by_name = {os.path.basename(r): i for r, i in self._roots}
        self._home = _key(home if home is not None else str(Path.home()))
        self._cache: dict[str, ProjectRef] = {}

    def by_name(self, name: str) -> str | None:
        """Registered project whose id or folder name is *name*."""
        n = os.path.normcase(name)
        for root, pid in self._roots:
            if os.path.normcase(pid) == n:
                return pid
        return self._by_name.get(n)

    def root_for(self, project_id: str) -> str | None:
        """Registered root of *project_id* (normalised), or None."""
        n = os.path.normcase(project_id)
        return next((root for root, pid in self._roots if os.path.normcase(pid) == n), None)

    def by_claude_slug(self, claude_slug: str) -> str | None:
        """Registered project whose path Claude Code encodes as *claude_slug*
        (every non-alphanumeric character becomes ``-``)."""
        want = claude_slug.casefold()
        for root, pid in self._roots:
            if re.sub(r"[^A-Za-z0-9]", "-", root).casefold() == want:
                return pid
        return None

    def resolve(self, cwd: str | None) -> ProjectRef:
        if not cwd:
            return ProjectRef(SHARED)
        hit = self._cache.get(cwd)
        if hit is None:
            hit = self._cache[cwd] = self._resolve(cwd)
        return hit

    def _resolve(self, cwd: str) -> ProjectRef:
        folded, codex_name = _fold_worktree(cwd)
        if codex_name:
            pid = self._by_name.get(os.path.normcase(codex_name))
            return ProjectRef(pid or slug(codex_name), "")
        k = _key(folded)
        for root, pid in self._roots:
            if root == self._home:
                continue  # a registered home folder must not swallow everything below it
            if k == root or k.startswith(root + os.sep):
                return ProjectRef(pid, root)
        found = _nearest_marked_root(Path(folded), stop=self._home)
        if found is not None:
            return ProjectRef(slug(found.name), _key(str(found)))
        return ProjectRef(SHARED)


def _nearest_marked_root(path: Path, *, stop: str) -> Path | None:
    for cand in (path, *path.parents):
        if _key(str(cand)) == stop or cand.parent == cand:
            return None
        try:
            if (cand / ".git").exists() or (cand / ".navig").is_dir():
                return cand
        except OSError:
            continue
    return None


def _registry_entries() -> list[tuple[str, str]]:
    """(id, path) per space — ONE row per id via ``canonical_row_for_id``: legacy
    duplicate rows (worktree checkouts registered under the main project's id) used to
    each become a candidate root, and ``root_for(id)`` (longest-path-first) preferred
    the WORKTREE — breaking file-key prefix checks for main-checkout paths. A broken
    registry still degrades to marker-based resolution."""
    try:
        from navig.spaces.registry import canonical_row_for_id, load_registry  # noqa: PLC0415

        reg = load_registry()
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for e in reg["spaces"]:
            sid = str(e.get("id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            row = canonical_row_for_id(reg, sid) or e
            out.append((sid, str(row.get("path") or "")))
        return out
    except Exception:  # noqa: BLE001 — a broken registry degrades to marker-based resolution
        return []


def project_for_path(cwd: str | None) -> ProjectRef:
    """One-off resolution (builds a fresh resolver). Batch callers keep a ``ProjectResolver``."""
    return ProjectResolver().resolve(cwd)
