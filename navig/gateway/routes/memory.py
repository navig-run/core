"""Memory routes: sessions, history, delete, add_message, knowledge CRUD + search, stats."""
from __future__ import annotations

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError(
        "aiohttp is required for gateway routes (pip install aiohttp)"
    ) from _exc
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/memory/sessions", _sessions(gateway))
    app.router.add_get("/memory/history/{session_key}", _history(gateway))
    app.router.add_delete("/memory/sessions/{session_key}", _delete_session(gateway))
    app.router.add_post("/memory/messages", _add_message(gateway))
    app.router.add_get("/memory/knowledge", _knowledge_list(gateway))
    app.router.add_post("/memory/knowledge", _knowledge_add(gateway))
    app.router.add_get("/memory/knowledge/search", _knowledge_search(gateway))
    app.router.add_get("/memory/stats", _stats(gateway))


def _get_store(gw):
    getter = getattr(gw, "_get_memory_store", None)
    if callable(getter):
        return getter()
    if not hasattr(gw, '_conversation_store'):
        from navig.memory import ConversationStore
        db_path = gw.config.storage_dir / "memory.db"
        gw._conversation_store = ConversationStore(db_path)
    return gw._conversation_store


def _get_kb(gw):
    getter = getattr(gw, "_get_knowledge_base", None)
    if callable(getter):
        return getter()
    if not hasattr(gw, '_knowledge_base'):
        from navig.memory import KnowledgeBase
        db_path = gw.config.storage_dir / "knowledge.db"
        gw._knowledge_base = KnowledgeBase(db_path, embedding_provider=None)
    return gw._knowledge_base


def _sessions(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            store = _get_store(gw)
            limit = int(r.query.get("limit", "50"))
            sessions = store.list_sessions(limit=limit)
            return json_ok({
                "sessions": [
                    {
                        "session_key": s.session_key,
                        "message_count": s.message_count,
                        "total_tokens": s.total_tokens,
                        "created_at": s.created_at.isoformat(),
                        "updated_at": s.updated_at.isoformat(),
                    }
                    for s in sessions
                ]
            })
        except Exception as e:
            return json_error_response("Failed to list memory sessions", status=500, code="internal_error", details={"error": str(e)})
    return h


def _history(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            store = _get_store(gw)
            session_key = r.match_info["session_key"]
            limit = int(r.query.get("limit", "100"))
            messages = store.get_history(session_key, limit=limit)
            return json_ok({
                "session_key": session_key,
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "timestamp": m.timestamp.isoformat(),
                        "token_count": m.token_count,
                    }
                    for m in messages
                ]
            })
        except Exception as e:
            return json_error_response("Failed to get memory history", status=500, code="internal_error", details={"error": str(e)})
    return h


def _delete_session(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            store = _get_store(gw)
            session_key = r.match_info["session_key"]
            if store.delete_session(session_key):
                return json_ok({"session_key": session_key, "deleted": True})
            else:
                return json_error_response("Session not found", status=404, code="not_found")
        except Exception as e:
            return json_error_response("Failed to delete session", status=500, code="internal_error", details={"error": str(e)})
    return h


def _add_message(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            from navig.memory import Message
            store = _get_store(gw)
            data = await r.json()
            message = Message(
                session_key=data["session_key"],
                role=data.get("role", "user"),
                content=data["content"],
                token_count=data.get("token_count", 0),
                metadata=data.get("metadata", {}),
            )
            stored = store.add_message(message)
            return json_ok({
                "message": {
                    "id": stored.id,
                    "session_key": stored.session_key,
                    "role": stored.role,
                    "timestamp": stored.timestamp.isoformat(),
                }
            })
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            return json_error_response("Failed to add message", status=500, code="internal_error", details={"error": str(e)})
    return h


def _knowledge_list(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            from navig.memory import KnowledgeEntry
            kb = _get_kb(gw)
            limit = int(r.query.get("limit", "50"))
            tag = r.query.get("tag")
            source = r.query.get("source")
            if tag:
                entries = kb.list_by_tag(tag, limit=limit)
            elif source:
                entries = kb.list_by_source(source, limit=limit)
            else:
                raw_entries = kb.export_entries()[:limit]
                entries = [KnowledgeEntry.from_dict(e) for e in raw_entries]
            return json_ok({
                "entries": [
                    {
                        "id": e.id,
                        "key": e.key,
                        "content": e.content[:200],
                        "tags": e.tags,
                        "source": e.source,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in entries
                ]
            })
        except Exception as e:
            return json_error_response("Failed to list knowledge entries", status=500, code="internal_error", details={"error": str(e)})
    return h


def _knowledge_add(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            from navig.memory import KnowledgeEntry
            kb = _get_kb(gw)
            data = await r.json()
            entry = KnowledgeEntry(
                key=data["key"],
                content=data["content"],
                summary=data.get("summary"),
                tags=data.get("tags", []),
                source=data.get("source", "api"),
            )
            if data.get("ttl_hours"):
                from datetime import datetime, timedelta
                entry.expires_at = datetime.utcnow() + timedelta(hours=data["ttl_hours"])
            stored = kb.upsert(entry, compute_embedding=False)
            return json_ok({
                "entry": {"id": stored.id, "key": stored.key}
            })
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            return json_error_response("Failed to add knowledge entry", status=500, code="internal_error", details={"error": str(e)})
    return h


def _knowledge_search(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            kb = _get_kb(gw)
            query = r.query.get("q", "")
            limit = int(r.query.get("limit", "10"))
            tags = r.query.get("tags", "").split(",") if r.query.get("tags") else None
            results = kb.text_search(query, limit=limit, tags=tags)
            return json_ok({
                "query": query,
                "results": [
                    {"id": e.id, "key": e.key, "content": e.content[:300], "tags": e.tags}
                    for e in results
                ]
            })
        except Exception as e:
            return json_error_response("Failed to search knowledge entries", status=500, code="internal_error", details={"error": str(e)})
    return h


def _stats(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            store = _get_store(gw)
            kb = _get_kb(gw)
            sessions = store.list_sessions(limit=1000)
            return json_ok({
                "conversation": {
                    "sessions": len(sessions),
                    "total_messages": sum(s.message_count for s in sessions),
                    "total_tokens": sum(s.total_tokens for s in sessions),
                },
                "knowledge": {"entries": kb.count()}
            })
        except Exception as e:
            return json_error_response("Failed to get memory stats", status=500, code="internal_error", details={"error": str(e)})
    return h
