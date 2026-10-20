"""Projects as the hub sees them: list summaries, a full detail, add and scan.

A project is a registered space — the rows come from
:func:`navig.spaces.resolver.discover_space_paths` (which auto-registers what it
finds) and the identity from the space manifest. Nothing here introduces a
second registry.

Summaries are cheap-by-design because the list shows every project at once:
per-project work is cached in memory, keyed on a stat signature of the files it
read, so an unchanged project costs a handful of ``stat`` calls; git calls run
concurrently with a short timeout each.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from navig.projects import docs as _docs
from navig.projects import gitinfo
from navig.projects import todos as _todos
from navig.projects.links import collect_links

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectNotFound",
    "add_project",
    "get_project",
    "list_projects",
    "resolve_project",
    "scan_root",
]

_CODE_MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "composer.json",
                 "Gemfile", "pom.xml", "build.gradle", "Makefile", "justfile", "setup.py", "deno.json")
_CACHE_TTL = 20.0
_cache: dict[tuple[str, str], tuple[float, str, Any]] = {}
_cache_lock = threading.Lock()


class ProjectNotFound(LookupError):
    pass


def _norm(p: str | Path) -> str:
    try:
        return os.path.normcase(str(Path(p).expanduser().resolve()))
    except Exception:  # noqa: BLE001
        return os.path.normcase(str(p))


def _cached(kind: str, root: Path, sig: str, build: Any, keep: Any = None) -> Any:
    """Memoise *build()* per (kind, project) while *sig* is unchanged and fresh.

    *keep(value)* False means "do not remember this one" — a result that only says
    the source could not be read must be retried next time, not served until the
    signature changes.
    """
    key = (kind, _norm(root))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[1] == sig and now - hit[0] < _CACHE_TTL:
            return hit[2]
    value = build()
    if keep is None or keep(value):
        with _cache_lock:
            _cache[key] = (now, sig, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _peek(kind: str, root: Path, sig: str) -> tuple[Any, bool] | None:
    """The cached value for *sig* even past its TTL, and whether it is still fresh."""
    with _cache_lock:
        hit = _cache.get((kind, _norm(root)))
    if not hit or hit[1] != sig:
        return None
    return hit[2], time.monotonic() - hit[0] < _CACHE_TTL


# Git state is the slow part of the list (two processes per repo, ~1.5 s each on a busy
# machine), so the deck asks for it without waiting: whatever is cached is served, and
# anything missing or stale is read here, in the background, for the next request.
_warm_lock = threading.Lock()
_warming: set[str] = set()
_warm_pool: ThreadPoolExecutor | None = None


def _warm_git(root: Path) -> None:
    global _warm_pool
    key = _norm(root)
    with _warm_lock:
        if key in _warming:
            return
        _warming.add(key)
        if _warm_pool is None:
            _warm_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="projects-git")
        pool = _warm_pool

    def _run() -> None:
        try:
            _git_light(root)
        except Exception:  # noqa: BLE001 — a background refresh must never surface
            logger.debug("background git read failed for %s", root, exc_info=True)
        finally:
            with _warm_lock:
                _warming.discard(key)

    pool.submit(_run)


def _sig(paths: list[Path]) -> str:
    parts = []
    for p in paths:
        try:
            st = p.stat()
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _docs_sig(root: Path) -> str:
    """Directory mtimes of the doc roots — changes when a doc is added/removed/saved
    in place (atomic writes replace the file, which bumps the directory)."""
    dirs = [root, root / ".navig", root / ".navig" / "plans", root / "plans"]
    plans = root / ".navig" / "plans"
    if plans.is_dir():
        try:
            dirs += [d for d in plans.iterdir() if d.is_dir()]
        except OSError:
            pass
    return _sig(dirs)


# ── resolution ──────────────────────────────────────────────────────────────


def _discover() -> dict[str, Any]:
    from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

    return discover_space_paths(include_disabled=True)


def resolve_project(project_id: str) -> tuple[str, Path]:
    """Space id → (canonical id, root). Raises :class:`ProjectNotFound`."""
    pid = (project_id or "").strip()
    if not pid:
        raise ProjectNotFound("project id is required")
    spaces = _discover()
    cfg = spaces.get(pid)
    if cfg is None:
        from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415

        cfg = spaces.get(normalize_space_name(pid))
    if cfg is None:
        raise ProjectNotFound(f"unknown project: {pid}")
    return cfg.canonical_name, Path(cfg.path)


# ── summaries ───────────────────────────────────────────────────────────────


def _kind(root: Path) -> str:
    return "code" if any((root / m).exists() for m in _CODE_MARKERS) else "workshop"


_PHASE_LINE = re.compile(r"^(?:#{2,4}\s*phase\s*[:—–-]\s*|\*\*phase\*\*\s*:\s*)(.+)$", re.IGNORECASE)
_TEMPLATE_MARK = "what are you working on right now"


def phase_title(text: str, fm: dict[str, Any] | None = None) -> str:
    """A human title for a CURRENT_PHASE doc — never the boilerplate H1.

    Measured on 20 real phase files: 12 had the H1 "Current Phase" (or
    "CURRENT_PHASE.md") and nothing else in it, so the H1 alone told you nothing.
    The real title lives after "Current Phase:", in a "## Phase: X" line, under
    "## Active Goal", or in the first H2. An untouched template is "" (not set).
    """
    if fm and fm.get("title"):
        return str(fm["title"]).strip()
    lines = [ln.strip() for ln in text.splitlines()]
    body = [ln for ln in lines if ln and not ln.startswith("---")]
    if any(_TEMPLATE_MARK in ln.lower() for ln in body[:3]) and not any(
            ln.startswith("- [") for ln in body):
        return ""
    for ln in body:
        if ln.startswith("# "):
            h1 = ln[2:].strip()
            m = re.search(r"current[\s_]phase(?:\.md)?\s*[:—–-]\s*(.+)$", h1, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
            if not re.search(r"current[\s_]phase", h1, re.IGNORECASE):
                return h1
            break
    for ln in body:
        m = _PHASE_LINE.match(ln)
        if m:
            return m.group(1).strip().strip("*").strip()
    for i, ln in enumerate(body):
        if re.match(r"^#{2,3}\s*active goal", ln, re.IGNORECASE):
            nxt = next((x for x in body[i + 1:] if not x.startswith("#")), "")
            if nxt:
                return nxt.lstrip("> ").strip()
            break
    h2 = next((ln for ln in body if ln.startswith("## ")), "")
    return re.sub(r"^[^\w(]+", "", h2[3:]).strip()


def _phase(root: Path) -> dict[str, Any] | None:
    from navig.plans.frontmatter import parse_frontmatter  # noqa: PLC0415
    from navig.spaces.plan_files import plan_file  # noqa: PLC0415

    path = plan_file(root, "CURRENT_PHASE.md")
    if path is None:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return None
    fm = parse_frontmatter(head) or {}
    title = phase_title(head, fm)
    return {
        "title": title[:160],
        "phase": fm.get("phase"),
        "status": fm.get("status"),
        "path": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
        "mtime": path.stat().st_mtime,
    }


def _todo_counts(root: Path) -> dict[str, int]:
    return _cached("todos", root, _docs_sig(root),
                   lambda: _todos.summarize(_todos.extract_todos(root)))


def _profile_light(root: Path) -> dict[str, Any]:
    from navig.projects.profile import build_profile  # noqa: PLC0415

    sig = _sig([root / m for m in ("package.json", "pyproject.toml", "Makefile", "justfile",
                                   "pnpm-workspace.yaml", "Cargo.toml")])

    def _build() -> dict[str, Any]:
        p = build_profile(root)
        return {"stack": p.get("stack", [])[:8], "scripts": len(p.get("scripts", [])),
                "package_manager": p.get("package_manager")}

    return _cached("profile", root, sig, _build)


def _git_sig(root: Path) -> str:
    return _sig([root / ".git" / "HEAD", root / ".git" / "index"])


def _git_light(root: Path, *, fast: bool = False) -> dict[str, Any]:
    # Tracked changes only: the untracked scan is what made `git status` cost ~1 s on a
    # big repo, and the list shows a dirty dot, not a file count. Detail uses the full one.
    if fast:
        if not gitinfo.has_own_repo(root):
            return {"repo": False}  # what git_state answers too — no reason to queue a read
        seen = _peek("git", root, _git_sig(root))
        if seen is not None:
            value, fresh = seen
            if not fresh:
                _warm_git(root)  # serve the last known state, refresh it behind the scenes
            return value
        _warm_git(root)
        # The branch costs a file read; the change count needs git, so it follows.
        return {"repo": True, "branch": gitinfo.head_branch(root), "dirty": None,
                "last_commit": None, "pending": True}
    return _cached("git", root, _git_sig(root),
                   lambda: gitinfo.git_state(root, commits=1, untracked=False),
                   keep=lambda g: not g.get("unknown"))


def _running(root: Path) -> list[dict[str, Any]]:
    from navig.projects.runner import get_runner  # noqa: PLC0415

    try:
        return get_runner().list_runs(root=root, include_finished=False)
    except Exception:  # noqa: BLE001 — a run-state read must not break the project list
        logger.debug("runner query failed", exc_info=True)
        return []


def _summary(canonical: str, cfg: Any, reg_rows: dict[str, dict[str, Any]],
             prefs: dict[str, dict[str, Any]], active: str | None,
             fast: bool = False) -> dict[str, Any]:
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    root = Path(cfg.path)
    key = _norm(root)
    row = reg_rows.get(key, {})
    manifest = load_space_manifest(root)
    kind = _kind(root)
    git = (_git_light(root, fast=fast) if kind == "code" or gitinfo.has_own_repo(root)
           else {"repo": False})
    profile = _profile_light(root) if kind == "code" else {"stack": [], "scripts": 0, "package_manager": None}
    phase = _phase(root)
    last_activity = max(
        [t for t in (
            _ts(git.get("last_commit")),
            (phase or {}).get("mtime"),
            _ts(row.get("last_active")),
        ) if t] or [0]
    )
    running = _running(root)
    return {
        "id": canonical,
        "name": manifest.resolved_name or row.get("name") or root.name,
        "path": str(root),
        "description": str(manifest.get("description") or manifest.get("tagline") or ""),
        "source": row.get("source") or cfg.scope,
        "kind": kind,
        "enabled": bool(row.get("enabled", True)),
        "trusted": row.get("trusted"),
        "active": active is not None and key == active,
        "exists": root.is_dir(),
        "stack": profile.get("stack", []),
        "scripts": profile.get("scripts", 0),
        "package_manager": profile.get("package_manager"),
        "phase": phase,
        "todos": _todo_counts(root),
        "git": {k: git.get(k) for k in ("repo", "branch", "dirty", "last_commit", "unknown", "pending")},
        "running": [{"key": r.get("script_key"), "name": r.get("name"), "ports": r.get("ports", [])}
                    for r in running],
        "links": collect_links(str(root), canonical),
        "prefs": prefs.get(key, {}),
        "last_activity": last_activity or None,
    }


def _ts(value: Any) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _registry_rows() -> tuple[dict[str, dict[str, Any]], str | None]:
    from navig.spaces import registry as _registry  # noqa: PLC0415

    reg = _registry.load_registry()
    rows = {_norm(e.get("path", "")): e for e in reg.get("spaces", []) if e.get("path")}
    active = _norm(reg["active"]) if reg.get("active") else None
    return rows, active


def _prefs() -> dict[str, dict[str, Any]]:
    try:
        from navig.projects.store import get_project_store  # noqa: PLC0415

        return get_project_store().all_prefs()
    except Exception:  # noqa: BLE001 — prefs are cosmetic; never fail the list on them
        logger.debug("project prefs unavailable", exc_info=True)
        return {}


def list_projects(*, refresh: bool = False, fast: bool = False) -> list[dict[str, Any]]:
    """Every known project, pinned first, then most recent activity.

    *fast* never waits on git: a project whose git state is not cached yet comes back
    with its branch (read from `.git/HEAD`) and ``git.pending``, and the state is read
    in the background for the next call. The CLI keeps the default, exact path.
    """
    if refresh:
        clear_cache()
    spaces = _discover()
    rows, active = _registry_rows()
    prefs = _prefs()
    items = list(spaces.items())

    def _one(item: tuple[str, Any]) -> dict[str, Any] | None:
        canonical, cfg = item
        try:
            return _summary(canonical, cfg, rows, prefs, active, fast=fast)
        except Exception:  # noqa: BLE001 — one broken folder must not empty the list
            logger.warning("project summary failed for %s", cfg.path, exc_info=True)
            return {"id": canonical, "name": canonical, "path": str(cfg.path), "error": True,
                    "kind": "workshop", "enabled": True, "todos": {}, "stack": [], "links": [],
                    "running": [], "prefs": {}, "git": {}, "phase": None, "exists": Path(cfg.path).is_dir()}

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="projects") as pool:
        out = [s for s in pool.map(_one, items) if s]
    out.sort(key=lambda s: (
        0 if (s.get("prefs") or {}).get("pinned") else 1,
        -(s.get("last_activity") or 0),
        str(s.get("name", "")).lower(),
    ))
    return out


def get_project(project_id: str) -> dict[str, Any]:
    """The full detail of one project — everything the hub's Overview needs."""
    from navig.projects.profile import build_profile  # noqa: PLC0415

    canonical, root = resolve_project(project_id)
    spaces = _discover()
    rows, active = _registry_rows()
    summary = _summary(canonical, spaces[canonical], rows, _prefs(), active)
    doc_index = _docs.index_docs(root)
    todos = _todos.extract_todos(root, doc_index)
    profile = build_profile(root) if summary["kind"] == "code" else {"scripts": [], "stack": [], "packages": []}
    missing = [n for n in ("VISION", "ROADMAP", "CURRENT_PHASE") if not any(
        d.get("canonical") is not None and Path(d["path"]).stem.upper() == n for d in doc_index)]
    next_action = next((t for t in todos if t["state"] == "in_progress"), None) or next(
        (t for t in todos if t["state"] == "open"), None)
    return {
        **summary,
        "git": gitinfo.git_state(root, commits=8),
        "profile": {k: profile.get(k) for k in ("stack", "metadata", "package_manager", "packages", "scripts")},
        "docs": doc_index,
        "todos": _todos.summarize(todos),
        "next_action": next_action,
        "doc_health": {"missing": missing, "count": len(doc_index)},
    }


# ── add / scan ──────────────────────────────────────────────────────────────


def _unique_id(base: str, path: Path) -> str:
    from navig.spaces import registry as _registry  # noqa: PLC0415

    candidate = base
    n = 2
    while _registry.id_taken_by_another_path(candidate, path):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def add_project(path: str | Path, *, scaffold: bool = False) -> dict[str, Any]:
    """Register a folder as a project. Reads its `.navig/` immediately.

    A folder without `.navig/` gets an empty one (a space IS a folder with
    `.navig/`); ``scaffold=True`` instead lays down the full plans skeleton —
    additive only, never overwriting a file.
    """
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"not a folder: {root}")
    root = root.resolve()
    created: list[str] = []
    if scaffold:
        from navig.commands.space import _scaffold_space_skeleton  # noqa: PLC0415

        created = _scaffold_space_skeleton(root, root.name).get("created", [])
    elif not (root / ".navig").is_dir():
        (root / ".navig").mkdir()
        created = [".navig/"]
    manifest = load_space_manifest(root)
    sid = _unique_id(normalize_space_name(manifest.resolved_id or root.name), root)
    entry = _registry.register(root, id=sid, name=manifest.resolved_name or root.name,
                               source=_registry.source_for(root), enabled=True)
    clear_cache()
    return {"id": entry.get("id", sid), "path": str(root), "created": created}


def scan_root(path: str | Path, *, add: bool = False) -> dict[str, Any]:
    """Look one level into *path* for projects.

    A child with `.navig/` is a project (registered when *add*); a child with
    only a code manifest is *suggested* — adding it creates `.navig/`, which is
    a write the operator should choose, not one a scan makes for them.
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"not a folder: {root}")
    rows, _active = _registry_rows()
    found: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    try:
        children = sorted((c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")),
                          key=lambda c: c.name.lower())
    except OSError as exc:
        raise ValueError(f"cannot read {root}: {exc}") from exc
    for child in children:
        has_navig = (child / ".navig").is_dir()
        is_code = _kind(child) == "code"
        if not (has_navig or is_code):
            continue
        registered = _norm(child) in rows
        item = {"name": child.name, "path": str(child.resolve()), "has_navig": has_navig,
                "kind": "code" if is_code else "workshop", "registered": registered}
        if add and has_navig and not registered:
            try:
                res = add_project(child)
                item["registered"] = True
                item["id"] = res["id"]
                added.append(item)
            except Exception as exc:  # noqa: BLE001 — report per folder, keep scanning
                item["error"] = str(exc)
        found.append(item)
    return {"root": str(root.resolve()), "candidates": found, "added": added}
