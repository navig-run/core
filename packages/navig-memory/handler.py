"""
navig-memory/handler.py

Lifecycle + command registration for the Memory pack.
Tries navig.agent.memory.MemoryStore first; falls back to a JSON file store
so the pack works even without the full agent stack.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from datetime import datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

# ── Lifecycle ──────────────────────────────────────────────────────────────────


def on_load(ctx: dict) -> None:
    """Register commands on pack activation."""
    try:
        from navig.commands._registry import CommandRegistry

        CommandRegistry.register("memory_store", cmd_memory_store)
        CommandRegistry.register("memory_search", cmd_memory_search)
        CommandRegistry.register("memory_clear", cmd_memory_clear)
        CommandRegistry.register("memory_checkpoint", cmd_memory_checkpoint)
    except ImportError as exc:
        _logger.warning(
            "navig-memory: CommandRegistry unavailable — commands not registered: %s",
            exc,
        )


def on_unload(ctx: dict) -> None:
    """Deregister commands on pack deactivation."""
    try:
        from navig.commands._registry import CommandRegistry

        for name in (
            "memory_store",
            "memory_search",
            "memory_clear",
            "memory_checkpoint",
        ):
            CommandRegistry.deregister(name)
    except ImportError as exc:
        _logger.warning(
            "navig-memory: CommandRegistry unavailable — could not deregister: %s", exc
        )


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
        if ctx:
            store_dir = None
            if isinstance(ctx, dict):
                store_dir = ctx.get("store_dir")
            else:
                store_dir = getattr(ctx, "store_dir", None)
                if store_dir is None:
                    try:
                        store_dir = ctx["store_dir"]
                    except Exception:  # noqa: BLE001
                        store_dir = None
            if store_dir:
                return pathlib.Path(store_dir) / "memories.json"
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    try:
        from navig.platform.paths import config_dir

        return config_dir() / "store" / "memory" / "memories.json"
    except Exception:
        return pathlib.Path.home() / ".navig" / "store" / "memory" / "memories.json"


def _checkpoint_path(ctx: Any = None) -> pathlib.Path:
    store_path = _store_path(ctx)
    return store_path.parent / "checkpoints"


def _conversation_db_path() -> pathlib.Path | None:
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        return pathlib.Path(cfg.global_config_dir) / "memory" / "memory.db"
    except Exception:  # noqa: BLE001
        return None


def _latest_conversation_snapshot() -> dict[str, Any] | None:
    db_path = _conversation_db_path()
    if db_path is None or not db_path.exists():
        return None

    try:
        from navig.memory import ConversationStore

        store = ConversationStore(db_path)
        try:
            sessions = store.list_sessions(limit=1)
            if not sessions:
                return None

            session = sessions[0]
            messages = store.get_history(session.session_key, limit=10)
            return {
                "session_key": session.session_key,
                "message_count": session.message_count,
                "updated_at": session.updated_at.isoformat(),
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "timestamp": message.timestamp.isoformat(),
                    }
                    for message in messages
                ],
            }
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("navig-memory: could not build checkpoint conversation snapshot: %s", exc)
        return None


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
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def store(self, content: str, tags: list[str] | None = None) -> str:
        memories = self._load()
        entry = {
            "id": str(len(memories) + 1),
            "content": content,
            "tags": tags or [],
            "ts": time.time(),
        }
        memories.append(entry)
        self._save(memories)
        return entry["id"]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        memories = self._load()
        q = query.lower()
        results = [
            m
            for m in memories
            if q in m.get("content", "").lower()
            or any(q in t.lower() for t in m.get("tags", []))
        ]
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


def cmd_memory_checkpoint(args: dict, ctx: Any = None) -> dict:
    """
    Snapshot workspace and latest conversation context.

    args:
      root_path (str, optional): Workspace root to record in the snapshot.
    """
    checkpoint_dir = _checkpoint_path(ctx)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc)
    checkpoint_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    checkpoint_file = checkpoint_dir / f"{checkpoint_id}.json"
    snapshot = {
        "id": checkpoint_id,
        "created_at": created_at.isoformat(),
        "workspace_root": args.get("root_path") or str(pathlib.Path.cwd()),
        "memory_store": str(_store_path(ctx)),
        "latest_session": _latest_conversation_snapshot(),
    }
    try:
        checkpoint_file.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "status": "ok",
            "data": {"id": checkpoint_id, "path": str(checkpoint_file)},
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ── COMMANDS registry ─────────────────────────────────────────────────────────

COMMANDS: dict[str, Any] = {
    "memory_store": cmd_memory_store,
    "memory_search": cmd_memory_search,
    "memory_clear": cmd_memory_clear,
    "memory_checkpoint": cmd_memory_checkpoint,
}
