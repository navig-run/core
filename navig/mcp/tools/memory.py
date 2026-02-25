import json
from typing import Dict, Any, List

def register(server: Any) -> None:
    """Register memory (key facts) tools."""
    server.tools.update({
        "memory.key_facts.remember": {
            "name": "memory.key_facts.remember",
            "description": "Store a key fact in persistent memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The fact text to remember"
                    },
                    "source": {
                        "type": "string",
                        "description": "Origin label for this fact (defaults to 'mcp')",
                        "default": "mcp"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional topic tags"
                    }
                },
                "required": ["fact"]
            }
        },
        "memory.key_facts.forget": {
            "name": "memory.key_facts.forget",
            "description": "Soft-delete a stored key fact by its ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "string",
                        "description": "UUID of the fact to forget"
                    }
                },
                "required": ["fact_id"]
            }
        },
        "memory.key_facts.retrieve": {
            "name": "memory.key_facts.retrieve",
            "description": "Search persistent memory for key facts matching a query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of facts to return",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        },
        "memory.key_facts.stats": {
            "name": "memory.key_facts.stats",
            "description": "Return counts of total, active, deleted, and superseded facts.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "memory.key_facts.update": {
            "name": "memory.key_facts.update",
            "description": "Update the content of an existing key fact by its ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "string",
                        "description": "UUID of the fact to update"
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Replacement fact text"
                    }
                },
                "required": ["fact_id", "new_content"]
            }
        }
    })
    
    server._tool_handlers.update({
        "memory.key_facts.remember": _tool_memory_remember,
        "memory.key_facts.forget": _tool_memory_forget,
        "memory.key_facts.retrieve": _tool_memory_retrieve,
        "memory.key_facts.stats": _tool_memory_stats,
        "memory.key_facts.update": _tool_memory_update,
    })

def _tool_memory_remember(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Store a key fact via KeyFactStore.upsert()."""
    import logging as _logging
    _log = _logging.getLogger("navig.mcp.memory.remember")
    try:
        from navig.memory.key_facts import KeyFact, KeyFactStore
        fact_text = args.get("fact", "").strip()
        if not fact_text:
            return {"error": "fact is required", "isError": True}
        source = args.get("source", "mcp")
        tags = args.get("tags", [])
        store = KeyFactStore()
        fact = KeyFact(
            content=fact_text,
            source_platform=source,
            tags=list(tags) if tags else [],
        )
        stored = store.upsert(fact)
        return {"id": stored.id, "status": "stored"}
    except Exception as exc:
        _log.error("memory.key_facts.remember failed: %s", exc, exc_info=True)
        return {"error": str(exc), "isError": True}

def _tool_memory_forget(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Soft-delete a key fact via KeyFactStore.soft_delete()."""
    import logging as _logging
    _log = _logging.getLogger("navig.mcp.memory.forget")
    try:
        from navig.memory.key_facts import KeyFactStore
        fact_id = args.get("fact_id", "").strip()
        if not fact_id:
            return {"error": "fact_id is required", "isError": True}
        store = KeyFactStore()
        deleted = store.soft_delete(fact_id)
        if not deleted:
            return {
                "success": False,
                "fact_id": fact_id,
                "error": f"No fact found with id '{fact_id}'",
                "isError": True,
            }
        return {"success": True, "fact_id": fact_id}
    except Exception as exc:
        _log.error("memory.key_facts.forget failed: %s", exc, exc_info=True)
        return {"error": str(exc), "fact_id": args.get("fact_id"), "isError": True}

def _tool_memory_retrieve(server: Any, args: Dict[str, Any]) -> Any:
    """Search key facts via FactRetriever.retrieve()."""
    import logging as _logging
    _log = _logging.getLogger("navig.mcp.memory.retrieve")
    try:
        from navig.memory.key_facts import KeyFactStore
        from navig.memory.fact_retriever import FactRetriever
        query = args.get("query", "").strip()
        if not query:
            return []
        limit = int(args.get("limit", 10))
        store = KeyFactStore()
        retriever = FactRetriever(store)
        result = retriever.retrieve(query)
        facts = result.facts[:limit]
        return [
            {
                "id": rf.fact.id,
                "content": rf.fact.content,
                "category": rf.fact.category,
                "tags": rf.fact.tags,
                "confidence": rf.fact.confidence,
                "score": rf.combined_score,
                "created_at": rf.fact.created_at,
            }
            for rf in facts
        ]
    except Exception as exc:
        _log.error("memory.key_facts.retrieve failed: %s", exc, exc_info=True)
        return {"error": str(exc), "facts": [], "isError": True}

def _tool_memory_stats(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return store statistics via KeyFactStore.get_stats()."""
    import logging as _logging
    _log = _logging.getLogger("navig.mcp.memory.stats")
    try:
        from navig.memory.key_facts import KeyFactStore
        store = KeyFactStore()
        return store.get_stats()
    except Exception as exc:
        _log.error("memory.key_facts.stats failed: %s", exc, exc_info=True)
        return {"error": str(exc), "isError": True}


def _tool_memory_update(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing fact's content in-place (soft-delete + re-insert)."""
    import logging as _logging
    _log = _logging.getLogger("navig.mcp.memory.update")
    try:
        from navig.memory.key_facts import KeyFact, KeyFactStore
        fact_id = args.get("fact_id", "").strip()
        new_content = args.get("new_content", "").strip()
        if not fact_id:
            return {"error": "fact_id is required", "isError": True}
        if not new_content:
            return {"error": "new_content is required", "isError": True}
        store = KeyFactStore()
        # Fetch original to preserve category/tags
        original = store.get(fact_id)
        if not original:
            return {"error": f"No fact found with id '{fact_id}'", "isError": True}
        # Soft-delete the old version
        store.soft_delete(fact_id)
        # Insert updated version
        updated = KeyFact(
            content=new_content,
            category=original.category,
            tags=original.tags,
            confidence=original.confidence,
            source_platform=original.source_platform or "agent_update",
        )
        stored = store.upsert(updated)
        return {"id": stored.id, "old_id": fact_id, "status": "updated"}
    except Exception as exc:
        _log.error("memory.key_facts.update failed: %s", exc, exc_info=True)
        return {"error": str(exc), "isError": True}
