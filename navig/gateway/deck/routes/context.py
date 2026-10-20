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
import threading
import time
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


# ─── Space file counts: cached, and bounded per request ─────────────────────
#
# Counting is the expensive part of /spaces. Measured on a real install with 42 spaces:
# the walks take 9.5 s warm, one project alone 4.9 s, and the cold request took 15.7 s.
# That cold time crosses the 15 s ceiling a linked NAVIG OS puts on every proxied deck call,
# so a linked machine's Spaces view just reported "the brain did not answer". Counts are
# a hint, not a contract. A count is reused for _COUNT_TTL_S. After that, the old value is
# served while it is refreshed. A space never counted before is counted only while the
# request is still inside _COUNT_BUDGET_S. Any space left over comes back as ``None`` with
# ``counts_pending`` set, and a background thread fills it in for the next request.

_COUNT_MAX_WALK = 5000
_COUNT_TTL_S = 300.0
_COUNT_BUDGET_S = 2.0
_count_cache: dict[str, tuple[float, int]] = {}
_count_lock = threading.Lock()
_count_refreshing: set[str] = set()


def _store_count(key: str, path: Path) -> int:
    n = _file_count_in(path, max_walk=_COUNT_MAX_WALK)
    with _count_lock:
        _count_cache[key] = (time.monotonic(), n)
    return n


def _refresh_counts_in_background(paths: list[Path]) -> None:
    """Count *paths* on one daemon thread, skipping any a previous refresh already owns."""
    with _count_lock:
        todo = [p for p in paths if str(p) not in _count_refreshing]
        _count_refreshing.update(str(p) for p in todo)
    if not todo:
        return

    def _run() -> None:
        for path in todo:
            try:
                _store_count(str(path), path)
            except Exception as exc:  # noqa: BLE001 — a hint must never take the thread down
                logger.debug("space count refresh failed for %s: %s", path, exc)
            finally:
                with _count_lock:
                    _count_refreshing.discard(str(path))

    threading.Thread(target=_run, name="navig-space-counts", daemon=True).start()


def _space_counts(paths: list[Path], budget_s: float | None = None) -> tuple[dict[str, int | None], bool]:
    """File counts for *paths* within a time budget → ``(counts by path, pending)``.

    ``pending`` is True when at least one count is missing or stale and a background
    refresh was started for it. Blocking — call off the event loop.
    """
    budget = _COUNT_BUDGET_S if budget_s is None else budget_s
    deadline = time.monotonic() + budget
    out: dict[str, int | None] = {}
    refresh: list[Path] = []
    for path in paths:
        key = str(path)
        with _count_lock:
            hit = _count_cache.get(key)
        if hit is not None:
            out[key] = hit[1]
            if time.monotonic() - hit[0] > _COUNT_TTL_S:
                refresh.append(path)  # serve the old value, refresh behind it
        elif time.monotonic() < deadline:
            out[key] = _store_count(key, path)
        else:
            out[key] = None
            refresh.append(path)
    if refresh:
        _refresh_counts_in_background(refresh)
    return out, bool(refresh)


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
    def _build() -> dict[str, Any]:
        """list_files()/get_stats() are synchronous SQLite reads (+ formatting a potentially
        large index) — run off the event loop so a big memory bank can't stall the gateway.
        MemoryStorage keeps a per-thread connection (threading.local, check_same_thread=False,
        WAL), so a worker-thread read is safe — the same reason index_async runs off-loop."""
        files_raw = list(mgr.list_files() or [])
        stats = dict(mgr.get_stats() or {})

        formatted = [_format_file(f) for f in files_raw]
        by_source: dict[str, int] = {}
        for f in formatted:
            by_source[f["source"]] = by_source.get(f["source"], 0) + 1

        # Sort by indexed_at DESC for "recent" — top 10
        try:
            recent = sorted(formatted, key=lambda x: x.get("indexed_at") or "", reverse=True)[:10]
        except Exception:
            recent = formatted[:10]

        return {
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
        }

    try:
        payload = await asyncio.to_thread(_build)
    except Exception as exc:
        logger.exception("context engine read failed")
        return _err(str(exc))

    payload["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _ok(payload)


async def handle_deck_context_files(request: "web.Request") -> "web.Response":
    """Paginated indexed-file list with optional source-type filter."""
    mgr = _get_manager()
    if mgr is None:
        return _ok({"files": [], "total": 0, "next_offset": None})

    # Bad pagination params are a client error (400), not a server error (500).
    try:
        offset = max(0, int(request.query.get("offset", 0)))
        limit = max(1, min(200, int(request.query.get("limit", 100))))
    except (ValueError, TypeError):
        return _err("offset and limit must be integers", 400)
    source_filter = request.query.get("source", "").strip()
    search = request.query.get("q", "").strip().lower()

    def _page() -> dict[str, Any]:
        """SQLite read + format + filter + sort off the event loop (see handle_deck_context)."""
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
        return {"files": page, "total": total, "next_offset": next_offset}

    try:
        return _ok(await asyncio.to_thread(_page))
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

        rows: list[tuple[str, Any, Path, bool]] = []
        for name, cfg in discovered.items():
            try:
                path = Path(str(getattr(cfg, "path", "")))
                rows.append((name, cfg, path, path.exists()))
            except Exception as exc:
                logger.debug("skip space %s: %s", name, exc)

        # Only the counts are slow, so the time budget applies to them alone (see _space_counts).
        counts, pending = _space_counts([path for _n, _c, path, exists in rows if exists])

        spaces_out: list[dict[str, Any]] = []
        for name, cfg, path, exists in rows:
            count = counts.get(str(path)) if exists else 0
            spaces_out.append({
                "name": name,
                "canonical_name": getattr(cfg, "canonical_name", name),
                "scope": getattr(cfg, "scope", "global"),
                "path": str(path),
                "exists": exists,
                # None = not counted yet (counts_pending); a capped walk is a floor, not a total.
                "file_count": count,
                "file_count_capped": count is not None and count >= _COUNT_MAX_WALK,
                "active": name == active_name,
            })

        spaces_out.sort(key=lambda s: (s["scope"] != "project", s["name"]))
        return {"spaces": spaces_out, "active": active_name, "counts_pending": pending}

    try:
        return _ok(await asyncio.to_thread(_collect))
    except Exception as exc:
        logger.exception("spaces discovery failed")
        return _err(str(exc))
