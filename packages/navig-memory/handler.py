"""
navig-memory/handler.py

Lifecycle + command registration for the Memory pack.
Tries navig.agent.memory.MemoryStore first; falls back to a JSON file store
so the pack works even without the full agent stack.
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Any


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def on_load(ctx: dict) -> None:
    """Register commands on pack activation."""
    try:
        from navig.commands._registry import CommandRegistry
        CommandRegistry.register("memory_store", cmd_memory_store)
        CommandRegistry.register("memory_search", cmd_memory_search)
        CommandRegistry.register("memory_clear", cmd_memory_clear)
    except ImportError:
        pass  # optional dependency not installed; feature disabled


def on_unload(ctx: dict) -> None:
    """Deregister commands on pack deactivation."""
    try:
        from navig.commands._registry import CommandRegistry
        for name in ("memory_store", "memory_search", "memory_clear"):
            CommandRegistry.deregister(name)
    except ImportError:
        pass  # optional dependency not installed; feature disabled


def on_event(event: str, ctx: dict) -> dict | None:
    return None


# ── Backend selection ──────────────────────────────────────────────────────────

def _get_store(ctx: Any = None):
    """Return a MemoryStore-compatible object. Prefers native; falls back to JSON."""
    try:
        from navig.agent.memory import MemoryStore  # type: ignore
        return MemoryStore()
    except ImportError:
        return _JsonMemoryStore(_store_path(ctx))


def _store_path(ctx: Any = None) -> pathlib.Path:
    try:
        if ctx and hasattr(ctx, "store_dir"):
            return pathlib.Path(ctx["store_dir"]) / "memories.json"
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    try:
        from navig.space.paths import get_global_root
        return get_global_root() / "store" / "memory" / "memories.json"
    except Exception:
        return pathlib.Path.home() / ".navig" / "store" / "memory" / "memories.json"


class _JsonMemoryStore:
    """Minimal JSON-file-backed memory store fallback."""

    def __init__(self, path: pathlib.Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, data: list[dict]) -> None:
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def store(self, content: str, tags: list[str] | None = None) -> str:
        memories = self._load()
        entry = {"id": str(len(memories) + 1), "content": content,
                 "tags": tags or [], "ts": time.time()}
        memories.append(entry)
        self._save(memories)
        return entry["id"]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        memories = self._load()
        q = query.lower()
        results = [m for m in memories if q in m.get("content", "").lower()
                   or any(q in t.lower() for t in m.get("tags", []))]
        return results[-limit:]

    def clear(self, confirm: bool = False) -> int:
        if not confirm:
            return 0
        memories = self._load()
        count = len(memories)
        self._save([])
        return count


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_memory_store(args: dict, ctx: Any = None) -> dict:
    """
    Persist a memory entry.

    args:
      content (str): Text to remember.
      tags (list[str], optional): Tags for retrieval.
    """
    content = args.get("content", "")
    if not content:
        return {"status": "error", "message": "Missing 'content' argument"}
    tags = args.get("tags", [])
    try:
        store = _get_store(ctx)
        memory_id = store.store(content, tags=tags)
        return {"status": "ok", "data": {"id": memory_id}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_memory_search(args: dict, ctx: Any = None) -> dict:
    """
    Search stored memories.

    args:
      query (str): Search text.
      limit (int, optional): Max results (default 10).
    """
    query = args.get("query", "")
    if not query:
        return {"status": "error", "message": "Missing 'query' argument"}
    limit = int(args.get("limit", 10))
    try:
        store = _get_store(ctx)
        results = store.search(query, limit=limit)
        return {"status": "ok", "data": results}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_memory_clear(args: dict, ctx: Any = None) -> dict:
    """
    Clear all stored memories.

    args:
      confirm (bool): Must be True to proceed.
    """
    confirm = args.get("confirm", False)
    if not confirm:
        return {"status": "error", "message": "Pass confirm=true to wipe all memories"}
    try:
        store = _get_store(ctx)
        count = store.clear(confirm=True)
        return {"status": "ok", "data": {"cleared": count}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ── COMMANDS registry ─────────────────────────────────────────────────────────

COMMANDS: dict[str, Any] = {
    "memory_store": cmd_memory_store,
    "memory_search": cmd_memory_search,
    "memory_clear": cmd_memory_clear,
}
