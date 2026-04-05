"""
navig.agent.tool_permissions — Pre-execution tool allow/deny filter.

Ported from .lab/claude_py/src/permissions.py (ToolPermissionContext).

A :class:`ToolPermissionContext` is applied *before* the LLM ever sees the
tool schema.  Blocked tools are silently removed from the ``tools=[]`` list
sent to the API, and any attempt to dispatch a removed tool raises
:class:`ToolPermissionDenied`.

This is intentionally separate from :mod:`navig.safety_guard` (which
classifies commands at *execution* time by regex).  The two layers are
complementary: permission context controls *visibility*; safety guard
controls *execution* risk.

Usage::

    from navig.agent.tool_permissions import ToolPermissionContext

    # Block specific tools
    ctx = ToolPermissionContext(deny_names=frozenset({"git_commit", "db_query"}))

    # Allow-only mode (all others blocked)
    ctx = ToolPermissionContext(allow_only=frozenset({"web_fetch", "kb_search"}))

    # Restrict by prefix (e.g. no navig_db_* tools)
    ctx = ToolPermissionContext(deny_prefixes=("navig_db_",))

    # Apply to OpenAI schemas list
    filtered = ctx.filter_schemas(registry.get_openai_schemas())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class ToolPermissionDenied(Exception):
    """Raised when a tool call is attempted for a blocked tool.

    Attributes:
        tool_name: The name of the blocked tool.
        reason:    Human-readable reason for the denial.
    """

    def __init__(
        self, tool_name: str, reason: str = "tool is not permitted in this session"
    ) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool {tool_name!r} is not permitted: {reason}")


@dataclass(frozen=True)
class ToolPermissionContext:
    """Immutable pre-execution filter over the agent tool registry.

    Three filter modes (mutually exclusive; precedence: allow_only > deny_names / deny_prefixes):

    1. **allow_only** — if set, *only* tools in this set are visible.
       All others are blocked.
    2. **deny_names** — explicit blocklist by exact tool name.
    3. **deny_prefixes** — block all tools whose name starts with any of these
       prefixes (e.g. ``("navig_db_",)`` blocks all DB tools).

    To combine deny_names and deny_prefixes, set both; they are ORed.

    Attributes:
        deny_names:   Frozenset of tool names to block.
        deny_prefixes: Tuple of name prefixes to block.
        allow_only:   If non-None, only these tool names are permitted.
    """

    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    allow_only: frozenset[str] | None = None

    # ── Core predicate ─────────────────────────────────────

    def blocks(self, tool_name: str) -> bool:
        """Return True if *tool_name* should be removed from the schema list.

        Args:
            tool_name: The tool's ``name`` field as registered.

        Returns:
            True  → tool is blocked (remove from schema / reject dispatch).
            False → tool is permitted.
        """
        if self.allow_only is not None:
            return tool_name not in self.allow_only
        if tool_name in self.deny_names:
            return True
        return any(tool_name.startswith(p) for p in self.deny_prefixes)

    # ── Schema-level filtering ──────────────────────────────

    def filter_schemas(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove blocked tools from an OpenAI ``tools=[]`` schema list.

        Expects items in the ``{"type": "function", "function": {"name": ...}}``
        format produced by :meth:`AgentToolRegistry.get_openai_schemas`.

        Args:
            schemas: List of OpenAI function schema dicts.

        Returns:
            Filtered list with blocked tools removed.
        """
        if self._is_noop():
            return schemas

        filtered: list[dict[str, Any]] = []
        for schema in schemas:
            name = (schema.get("function") or {}).get("name", "")
            if not name or not self.blocks(name):
                filtered.append(schema)
            else:
                logger.debug("ToolPermissionContext: removing blocked tool %r from schema", name)
        return filtered

    # ── Derived views ───────────────────────────────────────

    def allowed_names(self, all_names: list[str]) -> list[str]:
        """Return the subset of *all_names* that are not blocked.

        Args:
            all_names: Full list of registered tool names.

        Returns:
            Sorted list of permitted tool names.
        """
        return sorted(n for n in all_names if not self.blocks(n))

    def merge_deny(self, additional_names: frozenset[str]) -> ToolPermissionContext:
        """Return a new context with *additional_names* added to deny_names.

        Does not modify *self* (frozen dataclass).

        Args:
            additional_names: Extra tool names to block.

        Returns:
            New :class:`ToolPermissionContext` with merged deny list.
        """
        return ToolPermissionContext(
            deny_names=self.deny_names | additional_names,
            deny_prefixes=self.deny_prefixes,
            allow_only=self.allow_only,
        )

    def intersect_allow(self, tools: frozenset[str]) -> ToolPermissionContext:
        """Return a new context whose allow_only is intersected with *tools*.

        Use this when a child worker should only see a *subset* of what
        the parent permits.

        Args:
            tools: Tool names the child is allowed to use.

        Returns:
            New :class:`ToolPermissionContext` with tighter restrictions.
        """
        if self.allow_only is not None:
            return ToolPermissionContext(
                deny_names=self.deny_names,
                deny_prefixes=self.deny_prefixes,
                allow_only=self.allow_only & tools,
            )
        return ToolPermissionContext(
            deny_names=self.deny_names,
            deny_prefixes=self.deny_prefixes,
            allow_only=tools,
        )

    # ── String representation ───────────────────────────────

    def __str__(self) -> str:
        if self.allow_only is not None:
            return f"ToolPermissionContext(allow_only={sorted(self.allow_only)})"
        parts: list[str] = []
        if self.deny_names:
            parts.append(f"deny_names={sorted(self.deny_names)}")
        if self.deny_prefixes:
            parts.append(f"deny_prefixes={list(self.deny_prefixes)}")
        if not parts:
            return "ToolPermissionContext(unrestricted)"
        return f"ToolPermissionContext({', '.join(parts)})"

    # ── Private helpers ─────────────────────────────────────

    def _is_noop(self) -> bool:
        """Return True when this context imposes no restrictions at all."""
        return self.allow_only is None and not self.deny_names and not self.deny_prefixes


# ── Convenience constructors ────────────────────────────────


def allow_only(tool_names: list[str] | frozenset[str]) -> ToolPermissionContext:
    """Create a context that permits *only* the given tools."""
    return ToolPermissionContext(allow_only=frozenset(tool_names))


def deny_names_ctx(tool_names: list[str] | frozenset[str]) -> ToolPermissionContext:
    """Create a context that blocks the given tool names."""
    return ToolPermissionContext(deny_names=frozenset(tool_names))


def deny_prefixes_ctx(*prefixes: str) -> ToolPermissionContext:
    """Create a context that blocks all tools starting with any of *prefixes*."""
    return ToolPermissionContext(deny_prefixes=prefixes)


#: Convenience singleton representing no restrictions.
UNRESTRICTED = ToolPermissionContext()
