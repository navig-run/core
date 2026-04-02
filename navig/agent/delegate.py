"""
navig.agent.delegate — Subagent delegation tool (F-10).

Enables the parent agent to spawn a **child** agentic session with a scoped
toolset and a shared iteration budget.  The child runs in isolation (its own
conversation history) and returns only its final text answer.

Usage inside the ReAct loop::

    # The LLM asks to call ``delegate_task``
    # → AgentToolRegistry dispatches to DelegateTool.run()
    # → spawns a child ConversationalAgent.run_agentic()
    # → returns the child's final text response

Safety:
    - Max depth: 2 (parent=0, child=1, grandchild=2). Exceeding raises
      :class:`AgentDepthError`.
    - Max concurrent children: 3 (semaphore).
    - Child budget: ``min(parent_remaining * 0.5, 30)`` iterations.
    - Child toolset: intersection of parent's active toolset and requested
      toolset (child cannot escalate).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

MAX_AGENT_DEPTH = 2
"""Hard ceiling on nesting depth (0-indexed: parent=0)."""

MAX_CONCURRENT_CHILDREN = 3
"""Maximum simultaneous child agents per parent."""

_children_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHILDREN)


# ─────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────

class AgentDepthError(RuntimeError):
    """Raised when a child agent exceeds :data:`MAX_AGENT_DEPTH`."""


# ─────────────────────────────────────────────────────────────
# DelegateTool
# ─────────────────────────────────────────────────────────────

class DelegateTool:
    """Agent tool that spawns a child :class:`ConversationalAgent` sub-session.

    Parameters (as passed by the LLM)::

        {
            "task": "Research the latest Python 3.14 changelog",
            "toolset": "research",
            "context": "We are upgrading our codebase to 3.14",
            "max_iterations": 15
        }

    Attributes:
        name:        ``"delegate_task"``
        description: Human-readable purpose for the LLM schema.
        parameters:  JSON Schema for the tool arguments.
    """

    name: str = "delegate_task"
    description: str = (
        "Delegate a self-contained subtask to a specialist child agent. "
        "The child runs in isolation with its own conversation and returns a "
        "text answer. Use this for research tasks, code generation, or "
        "any job that benefits from a focused context."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Clear description of the subtask for the child agent.",
            },
            "toolset": {
                "type": "string",
                "description": (
                    "Named toolset the child should use (e.g. 'core', 'research', 'code'). "
                    "The child's tools will be the INTERSECTION of this and the parent's tools."
                ),
                "default": "core",
            },
            "context": {
                "type": "string",
                "description": "Additional context or background the child needs.",
                "default": "",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Max LLM iterations for the child (capped by budget).",
                "default": 15,
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        parent_depth: int = 0,
        parent_budget: Any | None = None,
        parent_toolsets: list[str] | None = None,
        cost_tracker: Any | None = None,
    ) -> None:
        self._parent_depth = parent_depth
        self._parent_budget = parent_budget
        self._parent_toolsets = parent_toolsets or ["core"]
        self._cost_tracker = cost_tracker

    async def run(self, args: dict[str, Any], **kwargs: Any) -> Any:
        """Spawn a child agent and return its result.

        Returns a ToolResult-compatible object with ``.output`` and ``.error``.
        """
        child_depth = self._parent_depth + 1
        if child_depth > MAX_AGENT_DEPTH:
            return _DelegateResult(
                output="",
                error=(
                    f"Agent depth limit exceeded ({child_depth} > {MAX_AGENT_DEPTH}). "
                    "Cannot delegate further."
                ),
            )

        task = args.get("task", "")
        if not task:
            return _DelegateResult(output="", error="Missing required 'task' argument.")

        requested_toolset = args.get("toolset", "core")
        context = args.get("context", "")
        requested_max = args.get("max_iterations", 15)

        # ── Budget sharing ──
        if self._parent_budget is not None:
            remaining = self._parent_budget.remaining()
            child_max = min(int(remaining * 0.5), 30, requested_max)
        else:
            child_max = min(requested_max, 30)

        if child_max < 1:
            return _DelegateResult(
                output="",
                error="Parent budget exhausted — cannot delegate.",
            )

        # ── Toolset intersection ──
        child_toolset = self._compute_toolset_intersection(requested_toolset)

        # ── Compose child message ──
        child_message = task
        if context:
            child_message = f"Context: {context}\n\nTask: {task}"

        # ── Spawn child ──
        async with _children_semaphore:
            try:
                result = await self._run_child(
                    message=child_message,
                    toolset=child_toolset,
                    max_iterations=child_max,
                    depth=child_depth,
                )
                return _DelegateResult(output=result, error=None)
            except AgentDepthError as exc:
                return _DelegateResult(output="", error=str(exc))
            except Exception as exc:
                logger.error("Delegate child agent failed: %s", exc)
                return _DelegateResult(
                    output="",
                    error=f"Child agent error: {exc}",
                )

    async def _run_child(
        self,
        message: str,
        toolset: str | list[str],
        max_iterations: int,
        depth: int,
    ) -> str:
        """Instantiate and run a child ConversationalAgent."""
        from navig.agent.conversational import ConversationalAgent

        child = ConversationalAgent()
        # Propagate user identity
        child._user_identity = getattr(self, "_user_identity", {})  # noqa: SLF001

        return await child.run_agentic(
            message=message,
            max_iterations=max_iterations,
            toolset=toolset,
            cost_tracker=self._cost_tracker,
        )

    def _compute_toolset_intersection(self, requested: str) -> list[str] | str:
        """Intersect the requested toolset with parent's active toolsets.

        The child cannot escalate — it gets at most the parent's tools.
        """
        try:
            from navig.agent.toolsets import resolve_toolset_names

            parent_names = set()
            for ts in self._parent_toolsets:
                names = resolve_toolset_names(ts)
                if names is None:
                    # Parent has "full" — allow anything
                    return requested
                parent_names.update(names)

            requested_names = resolve_toolset_names(requested)
            if requested_names is None:
                # Child wants "full" — constrain to parent's tools
                return list(parent_names)

            intersection = [n for n in requested_names if n in parent_names]
            return intersection if intersection else "core"
        except Exception:
            return "core"


class _DelegateResult:
    """Minimal ToolResult-like return value."""

    def __init__(self, output: str, error: str | None) -> None:
        self.output = output or ""
        self.error = error
        self.content = self.output  # alias for registry _result_to_str


# ─────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────

def register_delegate_tool(
    parent_depth: int = 0,
    parent_budget: Any | None = None,
    parent_toolsets: list[str] | None = None,
    cost_tracker: Any | None = None,
) -> None:
    """Register ``delegate_task`` in the :data:`_AGENT_REGISTRY`.

    Called by the parent :meth:`run_agentic` when the ``delegation`` toolset
    is requested.
    """
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        tool = DelegateTool(
            parent_depth=parent_depth,
            parent_budget=parent_budget,
            parent_toolsets=parent_toolsets,
            cost_tracker=cost_tracker,
        )
        _AGENT_REGISTRY.register(tool=tool, toolset="delegation")
        logger.debug("Registered delegate_task tool (depth=%d)", parent_depth)
    except Exception as exc:
        logger.debug("Failed to register delegate_task: %s", exc)
