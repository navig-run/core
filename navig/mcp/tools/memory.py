from typing import Any


def register(server: Any) -> None:
    """Register memory (key facts) tools."""
    server.tools.update(
        {
            "memory_key_facts_remember": {
                "name": "memory_key_facts_remember",
                "description": "Store a key fact in persistent memory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact text to remember",
                        },
                        "source": {
                            "type": "string",
                            "description": "Origin label for this fact (defaults to 'mcp')",
                            "default": "mcp",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional topic tags",
                        },
                    },
                    "required": ["fact"],
                },
            },
            "memory_key_facts_forget": {
                "name": "memory_key_facts_forget",
                "description": "Soft-delete a stored key fact by its ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "description": "UUID of the fact to forget",
                        }
                    },
                    "required": ["fact_id"],
                },
            },
            "memory_key_facts_retrieve": {
                "name": "memory_key_facts_retrieve",
                "description": "Search persistent memory for key facts matching a query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of facts to return",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
            "memory_key_facts_stats": {
                "name": "memory_key_facts_stats",
                "description": "Return counts of total, active, deleted, and superseded facts.",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "memory_key_facts_update": {
                "name": "memory_key_facts_update",
                "description": "Update the content of an existing key fact by its ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "description": "UUID of the fact to update",
                        },
                        "new_content": {
                            "type": "string",
                            "description": "Replacement fact text",
                        },
                    },
                    "required": ["fact_id", "new_content"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {name: _routed(name, _local) for name, _local in RAW_TOOLS.items()}
    )

    # See navig.mcp_server._gate_tool — unlisted defaults to "safe".
    # A module's register() must be self-sufficient: register_all_tools creates this
    # dict, but a direct `module.register(server)` call (tests, a plugin host) does not,
    # and assuming it exists raised AttributeError. cdp.py already guarded; these did not.
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(
        {
            "memory_key_facts_remember": "moderate",
            "memory_key_facts_update": "moderate",
            "memory_key_facts_forget": "moderate",  # soft delete — recoverable
            # read-only — recorded explicitly so a missing entry is a build failure,
            # not a silent default to "safe".
            "memory_key_facts_retrieve": "safe",
            "memory_key_facts_stats": "safe",
        }
    )


def _memory_off_error() -> dict[str, Any] | None:
    """An MCP error payload when the user switched memory off (``memory.enabled``)."""
    from navig.memory.controls import memory_off_reason

    reason = memory_off_reason()
    return {"error": reason, "isError": True} if reason else None


def _local_memory_remember(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Store a key fact via KeyFactStore.upsert() — the LOCAL authority path."""
    import logging as _logging

    _log = _logging.getLogger("navig.mcp.memory.remember")
    try:
        from navig.memory.key_facts import KeyFact, KeyFactStore

        fact_text = args.get("fact", "").strip()
        if not fact_text:
            return {"error": "fact is required", "isError": True}
        if off := _memory_off_error():
            return off
        source = args.get("source", "mcp")
        tags = args.get("tags", [])
        store = KeyFactStore()
        fact = KeyFact(
            content=fact_text,
            source_platform=source,
            tags=list(tags) if tags else [],
        )
        stored = store.upsert(fact)
        result: dict[str, Any] = {"id": stored.id, "status": "stored"}
        if stored.metadata.get("redacted"):
            result["redacted"] = True
        return result
    except Exception as exc:
        _log.error("memory.key_facts.remember failed: %s", exc, exc_info=True)
        return {"error": str(exc), "isError": True}


def _local_memory_forget(server: Any, args: dict[str, Any]) -> dict[str, Any]:
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


def _local_memory_retrieve(server: Any, args: dict[str, Any]) -> Any:
    """Search key facts via FactRetriever.retrieve()."""
    import logging as _logging

    _log = _logging.getLogger("navig.mcp.memory.retrieve")
    try:
        from navig.memory.fact_retriever import FactRetriever
        from navig.memory.key_facts import KeyFactStore

        query = args.get("query", "").strip()
        if not query:
            return []
        if off := _memory_off_error():
            return {**off, "facts": []}
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


def _local_memory_stats(server: Any, args: dict[str, Any]) -> dict[str, Any]:
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


def _local_memory_update(server: Any, args: dict[str, Any]) -> dict[str, Any]:
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
        if off := _memory_off_error():
            return off
        store = KeyFactStore()
        # Fetch original to preserve category/tags
        original = store.get(fact_id)
        if not original:
            return {"error": f"No fact found with id '{fact_id}'", "isError": True}
        # Insert the updated version FIRST, then retire the old one: upsert can refuse
        # (a fact that is only a secret), and deleting first would lose the original.
        updated = KeyFact(
            content=new_content,
            category=original.category,
            tags=original.tags,
            confidence=original.confidence,
            source_platform=original.source_platform or "agent_update",
        )
        stored = store.upsert(updated)
        if stored.id != fact_id:
            store.soft_delete(fact_id)
        return {"id": stored.id, "old_id": fact_id, "status": "updated"}
    except Exception as exc:
        _log.error("memory.key_facts.update failed: %s", exc, exc_info=True)
        return {"error": str(exc), "isError": True}


# ── Brain routing ──────────────────────────────────────────────
#
# Key facts belong to the Brain (``navig.mcp.capability``): one row, one owner. Each tool
# keeps its local implementation above — that implementation IS the authority when this
# machine is the Brain, or when the install never linked one — and ``_routed`` asks the
# capability router where the call must run before it touches a store. A linked client
# reaches the Brain; a Brain that cannot answer produces a structured error and NOT a
# local write, because two databases under one deck key is the defect being removed.

RAW_TOOLS: dict[str, Any] = {
    "memory_key_facts_remember": _local_memory_remember,
    "memory_key_facts_forget": _local_memory_forget,
    "memory_key_facts_retrieve": _local_memory_retrieve,
    "memory_key_facts_stats": _local_memory_stats,
    "memory_key_facts_update": _local_memory_update,
}


def _call_brain(client: Any, name: str, args: dict[str, Any]) -> Any:
    """Translate one MCP tool call into the matching Brain request.

    Arguments are passed through as the caller gave them: the Brain runs the very same
    implementation that is above, so validating or reshaping here would create a second
    definition of what ``limit`` or ``tags`` mean.
    """
    op = name.rsplit("_", 1)[-1]
    if op == "remember":
        return client.memory_remember(
            str(args.get("fact", "")),
            source=str(args.get("source", "mcp") or "mcp"),
            tags=args.get("tags") or [],
        )
    if op == "forget":
        return client.memory_forget(str(args.get("fact_id", "")))
    if op == "retrieve":
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        return client.memory_retrieve(str(args.get("query", "")), limit=limit)
    if op == "stats":
        return client.memory_stats()
    if op == "update":
        return client.memory_update(
            str(args.get("fact_id", "")), new_content=args.get("new_content")
        )
    return {"error": f"no Brain route for {name}", "isError": True}


def _routed(name: str, local: Any) -> Any:
    """The handler a client actually calls: route first, then run (here or on the Brain)."""

    def run(server: Any, args: dict[str, Any]) -> Any:
        from navig.brain.client import BrainUnavailable
        from navig.mcp import capability

        route = capability.resolve(name)
        if route.is_refused:
            return route.refusal
        if route.is_brain and route.client is not None:
            try:
                return _call_brain(route.client, name, args or {})
            except BrainUnavailable as exc:
                return capability.failure_payload(exc, route.capability)
        return local(server, args)

    run.__name__ = getattr(local, "__name__", name)
    run.__wrapped__ = local  # the brain-side route executes this, never the wrapper
    return run
