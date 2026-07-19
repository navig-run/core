"""Wiki browser + unified memory search routes for the Deck API (slice B4).

Surfaces a space's ``.navig/wiki`` knowledge base and the MemoryHub-style
unified memory query over HTTP — the wiki/KB/context capabilities of the
archived navig-memory VS Code extension (ledger: docs/forge-consolidation.md),
now rendered by the OS Knowledge and Context apps.

The wiki *engine* stays where it lives: listing and full-text search reuse
``navig.commands.wiki.list_wiki_pages`` / ``search_wiki`` (the same functions
behind ``navig wiki list`` / ``navig wiki search``) — nothing is reimplemented
here. The unified memory query mirrors the archived memoryHub adapters
(plans · git log · wiki index · memory notes) assembled within a caller-set
character budget. No LLM calls anywhere on these paths.

Routes (registered in navig/gateway/deck/__init__.py):
    GET  /api/deck/wiki/pages     → list wiki pages (rel path, title, mtime, size)
    GET  /api/deck/wiki/page      → read one page (?path= rel to .navig/wiki)
    POST /api/deck/wiki/page      → save/create a page (atomic, *.md only, confined)
    GET  /api/deck/wiki/search    → full-text search (?q=) via the wiki engine
    GET  /api/deck/memory/query   → unified memory search (?q=&budget=) —
                                    {sections:[{source,title,content,truncated}]}

Every route takes an optional ``space`` param (query for GET, body for POST):
a space id from the spaces registry resolved to its root, same as the plans
routes. Omitted → the active project root. Page paths are always relative to
the space's ``.navig/wiki`` and are resolved + prefix-verified — traversal
outside the wiki is a 400. Deleting pages is intentionally NOT exposed (the
CLI archives via ``navig wiki remove``).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_WIKI_REL = ".navig/wiki"
_GIT_TIMEOUT = 10  # seconds — local plumbing only, never network
_GIT_LOG_LIMIT = 15
_SEARCH_LIMIT = 50
_DEFAULT_BUDGET = 8_000  # chars (~2k tokens at 4 chars/token, memoryHub estimate)
_MIN_BUDGET = 500
_MAX_BUDGET = 100_000


# ── Helpers (house pattern — see routes/plans.py) ─────────────────────────────

def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _json(request: "web.Request") -> dict | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class _WikiOpError(Exception):
    """A user-reportable wiki/memory-operation failure carrying an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _find_project_root() -> Path:
    try:
        from navig.commands.plans import _find_project_root as _fpr

        return _fpr()
    except Exception:
        return Path.cwd()


def _space_root(space_id: str) -> Path | None:
    """Resolve a spaces-registry id to its root path (same seam as plans routes)."""
    try:
        from navig.spaces.resolver import discover_space_paths

        cfg = (discover_space_paths() or {}).get(space_id)
        if cfg is not None:
            p = Path(str(getattr(cfg, "path", "")))
            return p if str(p) else None
    except Exception:
        logger.debug("space resolve failed for %s", space_id, exc_info=True)
    return None


def _resolve_root(space: str | None) -> Path:
    """Space id → space root; no space → the active project root."""
    if space:
        root = _space_root(space)
        if root is None:
            raise _WikiOpError(f"unknown space: {space}", 404)
        return root
    return _find_project_root()


def _space_param(request: "web.Request", body: dict | None = None) -> str | None:
    space = (body or {}).get("space") or request.rel_url.query.get("space")
    space = str(space).strip() if space else ""
    return space or None


def _wiki_dir(root: Path) -> Path:
    return root / ".navig" / "wiki"


def _confined_page_path(wiki_dir: Path, rel: str) -> Path | None:
    """Resolve *rel* inside *wiki_dir*; ``None`` when it escapes (traversal)."""
    from navig.gateway.deck.routes._utils import confine_under  # noqa: PLC0415

    return confine_under(wiki_dir, rel)


# ── Payload builders (sync — called from thread workers) ──────────────────────

def _pages_payload(wiki_dir: Path) -> list[dict[str, Any]]:
    """Wiki page listing via the CLI's own engine (``navig wiki list``)."""
    from navig.commands.wiki import list_wiki_pages

    pages: list[dict[str, Any]] = []
    for page in list_wiki_pages(wiki_dir):
        modified = page.get("modified")
        pages.append({
            "path": page["path"],
            "name": page["name"],
            "title": page["title"],
            "folder": page["folder"],
            "size": page["size"],
            "mtime": modified.timestamp() if hasattr(modified, "timestamp") else modified,
        })
    return pages


# ── Wiki routes ───────────────────────────────────────────────────────────────

async def handle_wiki_pages(request: "web.Request") -> "web.Response":
    """List all wiki pages for a space (rel paths under ``.navig/wiki``)."""
    try:
        root = _resolve_root(_space_param(request))
    except _WikiOpError as exc:
        return _err(str(exc), exc.status)
    wiki_dir = _wiki_dir(root)

    def _read() -> dict[str, Any]:
        return {
            "exists": wiki_dir.is_dir(),
            "pages": _pages_payload(wiki_dir) if wiki_dir.is_dir() else [],
        }

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("wiki pages listing failed")
        return _err(str(exc), 500)


async def handle_wiki_page_get(request: "web.Request") -> "web.Response":
    """Read one wiki page (path relative to the space's ``.navig/wiki``)."""
    try:
        root = _resolve_root(_space_param(request))
    except _WikiOpError as exc:
        return _err(str(exc), exc.status)
    wiki_dir = _wiki_dir(root)
    rel = request.rel_url.query.get("path", "")
    target = _confined_page_path(wiki_dir, rel)
    if target is None:
        return _err(f"path must stay inside {_WIKI_REL}", 400)
    if target.suffix.lower() != ".md":
        return _err("only .md wiki pages can be read")
    if not target.is_file():
        return _err("wiki page not found", 404)

    def _read() -> dict[str, Any]:
        return {
            "path": target.relative_to(wiki_dir.resolve()).as_posix(),
            "content": target.read_text(encoding="utf-8", errors="replace"),
            "mtime": target.stat().st_mtime,
        }

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("wiki page read failed")
        return _err(str(exc), 500)


async def handle_wiki_page_save(request: "web.Request") -> "web.Response":
    """Save (or create) a wiki page — atomic write, confined to ``.navig/wiki``.

    ``*.md`` only; parent folders are created as needed. Hidden segments
    (``.meta`` and friends) are refused — the wiki's internals belong to the
    engine, not the editor surface.
    """
    body = await _json(request) or {}
    try:
        root = _resolve_root(_space_param(request, body))
    except _WikiOpError as exc:
        return _err(str(exc), exc.status)
    wiki_dir = _wiki_dir(root)
    rel = str(body.get("path") or "")
    content = body.get("content")
    if not isinstance(content, str):
        return _err("content must be a string")
    target = _confined_page_path(wiki_dir, rel)
    if target is None:
        return _err(f"path must stay inside {_WIKI_REL}", 400)
    if target.suffix.lower() != ".md":
        return _err("only .md wiki pages can be saved")
    try:
        rel_parts = target.relative_to(wiki_dir.resolve()).parts
    except ValueError:
        return _err(f"path must stay inside {_WIKI_REL}", 400)
    if any(part.startswith(".") for part in rel_parts):
        return _err("hidden wiki paths (dot segments) cannot be written")

    def _write() -> dict[str, Any]:
        from navig.core.yaml_io import atomic_write_text

        created = not target.exists()
        atomic_write_text(target, content)
        return {
            "path": target.relative_to(wiki_dir.resolve()).as_posix(),
            "mtime": target.stat().st_mtime,
            "created": created,
        }

    try:
        return _ok(await asyncio.to_thread(_write))
    except Exception as exc:
        logger.exception("wiki page save failed")
        return _err(str(exc), 500)


async def handle_wiki_search(request: "web.Request") -> "web.Response":
    """Full-text search across a space's wiki — the ``navig wiki search`` engine."""
    q = (request.rel_url.query.get("q") or "").strip()
    if not q:
        return _err("q is required")
    try:
        root = _resolve_root(_space_param(request))
    except _WikiOpError as exc:
        return _err(str(exc), exc.status)
    wiki_dir = _wiki_dir(root)

    def _search() -> dict[str, Any]:
        if not wiki_dir.is_dir():
            return {"q": q, "results": []}
        from navig.commands.wiki import search_wiki

        results = search_wiki(wiki_dir, q)[:_SEARCH_LIMIT]
        return {
            "q": q,
            "results": [
                {"path": r["path"], "matches": r["matches"], "context": r["context"]}
                for r in results
            ],
        }

    try:
        return _ok(await asyncio.to_thread(_search))
    except Exception as exc:
        logger.exception("wiki search failed")
        return _err(str(exc), 500)


# ── Unified memory query (the archived MemoryHub, server-side) ────────────────

def _section(source: str, title: str, content: str) -> dict[str, Any]:
    return {"source": source, "title": title, "content": content}


def _adapter_plans(root: Path, q: str) -> dict[str, Any] | None:
    """CURRENT_PHASE (80 lines) + DEV_PLAN (60 lines) + milestone progress."""
    plans_dir = root / ".navig" / "plans"
    parts: list[str] = []
    for fname, max_lines in (("CURRENT_PHASE.md", 80), ("DEV_PLAN.md", 60)):
        f = plans_dir / fname
        if not f.is_file():
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if q:
            ql = q.lower()
            lines = [ln for ln in lines if ql in ln.lower()][:max_lines]
            if not lines:
                continue
        else:
            lines = lines[:max_lines]
        parts.append(f"**{fname}**\n\n" + "\n".join(lines))
    if not q:
        try:
            from navig.plans.milestone_progress import MilestoneProgressEngine

            done = total = 0
            for ms in MilestoneProgressEngine(root).list_milestones():
                done += ms.done_count
                total += ms.total_count
            if total:
                pct = round(done / total * 100, 1)
                parts.append(f"Milestone progress: {done}/{total} tasks ({pct}%)")
        except Exception:
            logger.debug("milestone progress unavailable", exc_info=True)
    if not parts:
        return None
    return _section("plans", "Workspace plans", "\n\n---\n\n".join(parts))


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT,
    )


def _adapter_git(root: Path, q: str) -> dict[str, Any] | None:
    """Last one-line commits; a query filters subjects (case-insensitive).

    Only when the space root IS a repo's toplevel — ``git log`` in a non-repo
    space would otherwise walk UP and report the ENCLOSING repository's
    history as the space's memory (same guard as the plans milestone routes).
    """
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return None
        if Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        proc = _git(
            root, "log", "--oneline", "--no-decorate", "-50" if q else f"-{_GIT_LOG_LIMIT}"
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if q:
        ql = q.lower()
        lines = [ln for ln in lines if ql in ln.lower()]
    lines = lines[:_GIT_LOG_LIMIT]
    if not lines:
        return None
    return _section("git", "Recent commits", "\n".join(lines))


def _adapter_wiki(root: Path, q: str) -> dict[str, Any] | None:
    """Wiki index (first-heading excerpts) — or engine search hits for a query."""
    wiki_dir = _wiki_dir(root)
    if not wiki_dir.is_dir():
        return None
    if q:
        from navig.commands.wiki import search_wiki

        results = search_wiki(wiki_dir, q)[:10]
        if not results:
            return None
        lines = [f"- {r['path']} ({r['matches']}): {r['context']}" for r in results]
        return _section("wiki", "Wiki matches", "\n".join(lines))
    entries: list[str] = []
    for page in sorted(wiki_dir.rglob("*.md")):
        rel = page.relative_to(wiki_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            with page.open(encoding="utf-8", errors="replace") as fh:
                head = fh.read(512)  # heading only — never load whole pages for an index
        except OSError:
            continue
        first = next((ln for ln in head.splitlines() if ln.startswith("#")), None)
        title = first.lstrip("#").strip() if first else page.stem
        entries.append(f"- {title} ({rel.as_posix()})")
        if len(entries) >= 40:
            break
    if not entries:
        return None
    return _section("wiki", "Wiki index", "\n".join(entries))


def _adapter_notes(q: str) -> dict[str, Any] | None:
    """Approved key facts from the shared memory bank (the agent's notes)."""
    from navig.memory.key_facts import get_key_fact_store

    store = get_key_fact_store()
    if q:
        facts = [f for f, _rank in store.search_keyword(q, limit=10)]
        facts = [f for f in facts if f.approved == 1]
    else:
        facts = store.get_active(limit=10, approved=1)
    lines = [f"- [{f.category}] {f.content.strip()}" for f in facts if f.content.strip()]
    if not lines:
        return None
    return _section("notes", "Memory notes", "\n".join(lines))


def _assemble(
    sections: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], int, bool]:
    """Fit sections into *budget* chars; truncate the crossing one at a newline."""
    out: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for s in sections:
        content = s["content"]
        if used + len(content) <= budget:
            out.append({**s, "truncated": False})
            used += len(content)
            continue
        remaining = budget - used
        if remaining > 100:
            raw = content[:remaining]
            snap = raw.rfind("\n")
            if snap > 50:
                raw = raw[:snap]
            out.append({**s, "content": raw + "\n…", "truncated": True})
            used += len(raw)
        truncated = True
        break
    return out, used, truncated


async def handle_memory_query(request: "web.Request") -> "web.Response":
    """Unified memory search: plans + git log + wiki + notes in one budgeted reply.

    ``q`` filters every source (omit it for the ambient snapshot the archived
    MemoryHub built); ``budget`` caps total content chars (default 8000).
    """
    q = (request.rel_url.query.get("q") or "").strip()
    raw_budget = (request.rel_url.query.get("budget") or "").strip()
    try:
        budget = int(raw_budget) if raw_budget else _DEFAULT_BUDGET
    except ValueError:
        return _err("budget must be an integer (characters)")
    budget = max(_MIN_BUDGET, min(_MAX_BUDGET, budget))
    try:
        root = _resolve_root(_space_param(request))
    except _WikiOpError as exc:
        return _err(str(exc), exc.status)

    def _gather() -> dict[str, Any]:
        adapters = (
            ("plans", lambda: _adapter_plans(root, q)),
            ("git", lambda: _adapter_git(root, q)),
            ("wiki", lambda: _adapter_wiki(root, q)),
            ("notes", lambda: _adapter_notes(q)),
        )
        sections: list[dict[str, Any]] = []
        for name, fn in adapters:
            try:
                section = fn()
            except Exception:
                # A failing adapter never blocks the rest (memoryHub contract).
                logger.debug("memory adapter %s failed", name, exc_info=True)
                section = None
            if section is not None:
                sections.append(section)
        assembled, used, truncated = _assemble(sections, budget)
        return {
            "q": q,
            "budget": budget,
            "used": used,
            "truncated": truncated,
            "sections": assembled,
        }

    try:
        return _ok(await asyncio.to_thread(_gather))
    except Exception as exc:
        logger.exception("memory query failed")
        return _err(str(exc), 500)
