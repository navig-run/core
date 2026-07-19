"""Context Engine handlers for the Deck API.

Exposes the memory bank (indexed files + chunks + embeddings) and the spaces
graph (workspaces / contexts on disk) for the deck UI's Context viewer.

Endpoints registered in `navig/gateway/deck/__init__.py`:
    GET /api/deck/context          → overview (stats + by-source + recent)
    GET /api/deck/context/files    → paginated file list with metadata
    GET /api/deck/spaces           → discovered spaces with file counts
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _get_manager():
    """Lazy-import the memory manager so a missing module doesn't break the gateway."""
    try:
        from navig.memory.manager import get_memory_manager  # type: ignore[import]
        return get_memory_manager()
    except Exception as exc:
        logger.debug("memory manager unavailable: %s", exc)
        return None


def _classify_source(path: str) -> str:
    """Heuristic source-type buckets for the Context viewer."""
    p = path.lower()
    ext = os.path.splitext(p)[1]
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h",
              ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".sql"):
        return "code"
    if ext in (".md", ".markdown", ".mdx", ".rst", ".txt", ".org"):
        return "notes"
    if ext in (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env"):
        return "config"
    if ext in (".html", ".htm", ".url"):
        return "bookmarks"
    if ext in (".pdf", ".doc", ".docx", ".odt"):
        return "documents"
    return "other"


# Build / VCS / cache dirs excluded from a space's file count: they're not the
# operator's content, and (node_modules especially) they're the reason the walk
# would hit the cap and burn time. `.navig` is deliberately NOT here — it holds
# the plans/wiki/memory that make a folder a space.
_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".dev", "dist",
    "build", ".next", "target", ".mypy_cache", ".pytest_cache", ".cache",
})


def _file_count_in(path: Path, max_walk: int = 5000) -> int:
    """Cheap recursive file count for a space directory (capped, blocking — call off-loop).

    Prunes build/VCS/cache dirs (``_SKIP_DIRS``) both so the number reflects real space
    content and so a stray ``node_modules`` can't blow past the cap. Runs synchronous
    ``os.walk`` — callers MUST invoke it off the event loop (``asyncio.to_thread``).
    """
    if not path.exists():
        return 0
    n = 0
    try:
        for _root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]  # prune in place → don't descend
            n += len(files)
            if n >= max_walk:
                return max_walk
    except Exception:
        pass
    return n


def _format_file(f: dict[str, Any]) -> dict[str, Any]:
    """Normalize a memory file dict for the deck UI."""
    path = str(f.get("file_path") or f.get("path") or "")
    return {
        "path": path,
        "source": _classify_source(path),
        "chunk_count": int(f.get("chunk_count") or 0),
        "total_tokens": int(f.get("total_tokens") or 0),
        "indexed_at": f.get("indexed_at") or "",
        "last_modified": f.get("last_modified") or "",
        "file_hash": (f.get("file_hash") or "")[:12],
    }


# ─── Endpoints ──────────────────────────────────────────────────────────────


async def handle_deck_context(request: "web.Request") -> "web.Response":
    """Context overview: stats + by-source breakdown + recent indexed files."""
    mgr = _get_manager()
    if mgr is None:
        return _ok({
            "ready": False,
            "file_count": 0,
            "chunk_count": 0,
            "total_tokens": 0,
            "by_source": {},
            "recent": [],
            "stats": {},
        })
    try:
        files_raw = list(mgr.list_files() or [])
        stats = dict(mgr.get_stats() or {})
    except Exception as exc:
        logger.exception("context engine read failed")
        return _err(str(exc))

    formatted = [_format_file(f) for f in files_raw]
    by_source: dict[str, int] = {}
    for f in formatted:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1

    # Sort by indexed_at DESC for "recent" — top 10
    try:
        recent = sorted(formatted, key=lambda x: x.get("indexed_at") or "", reverse=True)[:10]
    except Exception:
        recent = formatted[:10]

    return _ok({
        "ready": True,
        "file_count": int(stats.get("file_count") or len(formatted)),
        "chunk_count": int(stats.get("chunk_count") or 0),
        "total_tokens": int(stats.get("total_tokens") or 0),
        "by_source": by_source,
        "recent": recent,
        "stats": {
            "database_size_mb": float(stats.get("database_size_mb") or 0.0),
            "embedded_chunks": int(stats.get("embedded_chunks") or 0),
            "embeddings_enabled": bool(stats.get("embeddings_enabled") or False),
            "embedding_model": str(stats.get("embedding_model") or ""),
            "memory_dir": str(stats.get("memory_dir") or ""),
        },
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


async def handle_deck_context_files(request: "web.Request") -> "web.Response":
    """Paginated indexed-file list with optional source-type filter."""
    mgr = _get_manager()
    if mgr is None:
        return _ok({"files": [], "total": 0, "next_offset": None})

    try:
        offset = max(0, int(request.query.get("offset", 0)))
        limit = max(1, min(200, int(request.query.get("limit", 100))))
        source_filter = request.query.get("source", "").strip()
        search = request.query.get("q", "").strip().lower()

        files_raw = list(mgr.list_files() or [])
        formatted = [_format_file(f) for f in files_raw]

        if source_filter and source_filter != "all":
            formatted = [f for f in formatted if f["source"] == source_filter]
        if search:
            formatted = [f for f in formatted if search in f["path"].lower()]

        formatted.sort(key=lambda x: x.get("indexed_at") or "", reverse=True)
        total = len(formatted)
        page = formatted[offset:offset + limit]
        next_offset = offset + limit if offset + limit < total else None

        return _ok({"files": page, "total": total, "next_offset": next_offset})
    except Exception as exc:
        logger.exception("context files read failed")
        return _err(str(exc))


async def handle_deck_spaces(request: "web.Request") -> "web.Response":
    """List discovered spaces (global + project-local) with file counts."""
    try:
        from navig.spaces.resolver import (  # type: ignore[import]
            discover_space_paths,
            get_default_space,
        )
    except Exception as exc:
        logger.debug("spaces module unavailable: %s", exc)
        return _ok({"spaces": [], "active": "default"})

    def _collect() -> dict[str, Any]:
        """All the blocking filesystem work — discovery, the active-space read, and the
        per-space ``os.walk`` — in ONE off-loop thread so a large space tree can't stall
        the gateway (the same reason plans reads/writes run in ``asyncio.to_thread``)."""
        discovered = discover_space_paths() or {}
        active_default = get_default_space()

        # Resolve the active space through the ONE canonical reader (NAVIG_SPACE env → cache
        # file, the source of truth navig space switch writes first → config-key mirror),
        # falling back to the discovery default when nothing is set. An inline cache-file-only
        # read would miss the env override and the config mirror and drift from the CLI.
        active_name = active_default
        try:
            from navig.commands.space import resolve_active_space  # type: ignore[import]
            resolved = resolve_active_space()
            if resolved:
                active_name = resolved
        except Exception:
            pass

        spaces_out: list[dict[str, Any]] = []
        for name, cfg in discovered.items():
            try:
                path = Path(str(getattr(cfg, "path", "")))
                exists = path.exists()
                spaces_out.append({
                    "name": name,
                    "canonical_name": getattr(cfg, "canonical_name", name),
                    "scope": getattr(cfg, "scope", "global"),
                    "path": str(path),
                    "exists": exists,
                    "file_count": _file_count_in(path) if exists else 0,
                    "active": name == active_name,
                })
            except Exception as exc:
                logger.debug("skip space %s: %s", name, exc)

        spaces_out.sort(key=lambda s: (s["scope"] != "project", s["name"]))
        return {"spaces": spaces_out, "active": active_name}

    try:
        return _ok(await asyncio.to_thread(_collect))
    except Exception as exc:
        logger.exception("spaces discovery failed")
        return _err(str(exc))
