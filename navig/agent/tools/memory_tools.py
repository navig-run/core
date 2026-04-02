"""
navig.agent.tools.memory_tools — Agent tools for reading and writing navig's KB.

These tools give the LLM mid-session write access to navig's structured key-fact
store (:class:`~navig.memory.key_facts.KeyFactStore`).  Facts persist across
sessions via SQLite.

Tool catalog
------------
``memory_read``    — Search KB for relevant facts.
``memory_write``   — Upsert a fact into the KB.
``memory_delete``  — Soft-delete a fact from the KB.
``kb_lookup``      — Alias for memory_read with category filtering.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

# Security: reject values containing these patterns
_SECURITY_PATTERNS = [
    re.compile(r"<script", re.IGNORECASE),
    re.compile(r"\x00"),
    re.compile(r"base64:"),
    re.compile(r"javascript:", re.IGNORECASE),
]

_MAX_KEY_CHARS = 128
_MAX_VALUE_CHARS = 2_200
_DEFAULT_LIMIT = 10


def _check_security(text: str) -> str | None:
    """Return an error message if text contains a forbidden pattern, else None."""
    for pat in _SECURITY_PATTERNS:
        if pat.search(text):
            return f"Rejected: value matches disallowed pattern {pat.pattern!r}"
    return None


def _get_store():  # type: ignore[return]
    """Return the global KeyFactStore instance (lazy import)."""
    from navig.memory.key_facts import KeyFactStore
    return KeyFactStore()


class MemoryReadTool(BaseTool):
    """Search navig's key-fact KB and return matching facts."""

    name = "memory_read"
    description = (
        "Search navig's persistent memory store for facts, preferences, or "
        "previously stored information.  Returns the most relevant entries."
    )
    owner_only = False
    parameters = [
        {
            "name": "query",
            "type": "string",
            "description": "Search query to find relevant memories",
            "required": True,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": f"Maximum number of results to return (default {_DEFAULT_LIMIT})",
            "required": False,
        },
        {
            "name": "category",
            "type": "string",
            "description": (
                "Filter by category: preference, decision, context, "
                "identity, technical, problem_solution"
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(name=self.name, success=False, error="'query' arg is required")

        limit = int(args.get("limit") or _DEFAULT_LIMIT)
        category = args.get("category") or None

        try:
            store = _get_store()
            # Try FTS search first
            if hasattr(store, "search"):
                results = store.search(query, limit=limit, category=category)
            else:
                results = []
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Memory read failed: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        if not results:
            return ToolResult(
                name=self.name,
                success=True,
                output={"query": query, "results": [], "message": "No matching memories found."},
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        formatted = []
        for fact in results:
            formatted.append({
                "id": getattr(fact, "id", ""),
                "content": getattr(fact, "content", str(fact)),
                "category": getattr(fact, "category", "context"),
                "confidence": getattr(fact, "confidence", 1.0),
            })

        return ToolResult(
            name=self.name,
            success=True,
            output={"query": query, "results": formatted, "count": len(formatted)},
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class MemoryWriteTool(BaseTool):
    """Write a fact to navig's persistent key-fact KB."""

    name = "memory_write"
    description = (
        "Store a fact, preference, or piece of information in navig's persistent memory.  "
        "Use this to remember important details across sessions."
    )
    owner_only = False
    parameters = [
        {
            "name": "content",
            "type": "string",
            "description": "The fact or information to remember (max 2200 chars)",
            "required": True,
        },
        {
            "name": "category",
            "type": "string",
            "description": (
                "Category for the fact: preference, decision, context, "
                "identity, technical, problem_solution  (default: context)"
            ),
            "required": False,
        },
        {
            "name": "confidence",
            "type": "number",
            "description": "Confidence score 0.0–1.0 (default: 0.9)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        content = args.get("content", "").strip()
        if not content:
            return ToolResult(name=self.name, success=False, error="'content' arg is required")

        if len(content) > _MAX_VALUE_CHARS:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Content too long ({len(content)} chars; max {_MAX_VALUE_CHARS})",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        security_err = _check_security(content)
        if security_err:
            return ToolResult(
                name=self.name,
                success=False,
                error=security_err,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        category = args.get("category") or "context"
        confidence = float(args.get("confidence") or 0.9)
        confidence = max(0.0, min(1.0, confidence))

        try:
            from navig.memory.key_facts import KeyFact, KeyFactStore

            store = KeyFactStore()
            fact = KeyFact(
                content=content,
                category=category,
                confidence=confidence,
                source_platform="agent",
            )
            stored_fact = store.upsert(fact)
            return ToolResult(
                name=self.name,
                success=True,
                output={
                    "stored": True,
                    "id": getattr(stored_fact, "id", ""),
                    "content": content[:100] + ("…" if len(content) > 100 else ""),
                    "category": category,
                },
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Memory write failed: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


class MemoryDeleteTool(BaseTool):
    """Soft-delete a fact from navig's key-fact KB by ID."""

    name = "memory_delete"
    description = (
        "Delete a previously stored memory by its ID.  "
        "Use memory_read to find the ID first."
    )
    owner_only = False
    parameters = [
        {
            "name": "id",
            "type": "string",
            "description": "The ID of the memory entry to delete (from memory_read results)",
            "required": True,
        }
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        fact_id = args.get("id", "").strip()
        if not fact_id:
            return ToolResult(name=self.name, success=False, error="'id' arg is required")

        try:
            from navig.memory.key_facts import KeyFactStore

            store = KeyFactStore()
            if hasattr(store, "soft_delete"):
                store.soft_delete(fact_id)
            else:
                return ToolResult(
                    name=self.name,
                    success=False,
                    error="soft_delete not available on this KeyFactStore",
                )
            return ToolResult(
                name=self.name,
                success=True,
                output={"deleted": True, "id": fact_id},
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Memory delete failed: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


class KBLookupTool(BaseTool):
    """Semantic lookup in navig's knowledge base — alias for memory_read with category filter."""

    name = "kb_lookup"
    description = (
        "Look up information from navig's knowledge base.  "
        "Searches structured facts, user preferences, and stored decisions."
    )
    owner_only = False
    parameters = [
        {
            "name": "query",
            "type": "string",
            "description": "What to look up in the knowledge base",
            "required": True,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": "Maximum entries to return (default 5)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        # Delegate to MemoryReadTool with limit defaulting to 5
        inner_args = {**args}
        if "limit" not in inner_args:
            inner_args["limit"] = 5
        delegate = MemoryReadTool()
        return await delegate.run(inner_args, on_status=on_status)
