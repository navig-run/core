"""
navig.agent.tools.plan_tools — Agent tools for plan-mode interaction.

Provides three tools the LLM can call while in planning mode:

* ``plan_add_step``  — append a step to the current plan
* ``plan_show``      — display the plan in Markdown
* ``plan_approve``   — transition the plan from REVIEWING → EXECUTING

These tools are registered in the ``"plan"`` toolset and gated by
``PlanInterceptor.is_planning`` so they only appear to the LLM when
plan mode is active.

FA-01 implementation.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.platform import paths
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Shared reference — set by register_plan_tools()
# ─────────────────────────────────────────────────────────────

_interceptor_ref: Any = None  # Will be PlanInterceptor


def set_interceptor(interceptor: Any) -> None:
    """Bind the module-level interceptor so tools can access the session."""
    global _interceptor_ref
    _interceptor_ref = interceptor


def _get_interceptor() -> Any:
    if _interceptor_ref is None:
        raise RuntimeError("Plan tools not initialised — call set_interceptor() first.")
    return _interceptor_ref


# ─────────────────────────────────────────────────────────────
# plan_add_step
# ─────────────────────────────────────────────────────────────


class PlanAddStepTool(BaseTool):
    """Add a step to the current plan."""

    name = "plan_add_step"
    description = (
        "Add a new step to the current plan.  Provide a description plus "
        "optional lists of files that will be affected and tools that will be called.  "
        "Risk level can be 'low', 'medium', or 'high'."
    )
    owner_only = False
    parameters = [
        {
            "name": "description",
            "type": "string",
            "description": "What this step will do",
            "required": True,
        },
        {
            "name": "files",
            "type": "string",
            "description": "Comma-separated list of files that will be modified",
            "required": False,
        },
        {
            "name": "tools",
            "type": "string",
            "description": "Comma-separated list of tool names this step will call",
            "required": False,
        },
        {
            "name": "risk",
            "type": "string",
            "description": "Risk level: low, medium, or high (default: low)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.plan_mode import PlanStep

        interceptor = _get_interceptor()

        desc = args.get("description", "").strip()
        if not desc:
            return ToolResult(name=self.name, success=False, output="description is required.", error="missing description")

        files_raw = args.get("files", "")
        tools_raw = args.get("tools", "")
        risk = args.get("risk", "low").strip().lower()

        files = [f.strip() for f in files_raw.split(",") if f.strip()] if files_raw else []
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()] if tools_raw else []

        try:
            step = PlanStep(
                description=desc,
                tool_calls=tools,
                files_affected=files,
                risk_level=risk,
            )
            idx = interceptor.add_step(step)
            return ToolResult(
                name=self.name,
                success=True,
                output=f"Step {idx + 1} added: {desc}",
            )
        except (ValueError, RuntimeError) as exc:
            return ToolResult(name=self.name, success=False, output=str(exc), error=str(exc))


# ─────────────────────────────────────────────────────────────
# plan_show
# ─────────────────────────────────────────────────────────────


class PlanShowTool(BaseTool):
    """Display the current plan in Markdown."""

    name = "plan_show"
    description = (
        "Show the current plan with all steps, their status, affected files, "
        "and risk levels.  Returns Markdown."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        interceptor = _get_interceptor()
        text = interceptor.format_plan()
        return ToolResult(name=self.name, success=True, output=text)


# ─────────────────────────────────────────────────────────────
# plan_approve
# ─────────────────────────────────────────────────────────────


class PlanApproveTool(BaseTool):
    """Approve the plan and move to execute mode."""

    name = "plan_approve"
    description = (
        "Approve the current plan.  Moves the plan from REVIEWING to EXECUTING "
        "so all tools become available again."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        interceptor = _get_interceptor()
        try:
            # Auto-transition planning → reviewing if still planning
            if interceptor.is_planning:
                interceptor.review()
            interceptor.approve()
            summary = interceptor.session.summary()
            return ToolResult(
                name=self.name,
                success=True,
                output=(
                    f"Plan approved — {summary['total_steps']} steps ready for execution.  "
                    "All tools are now available."
                ),
            )
        except RuntimeError as exc:
            return ToolResult(name=self.name, success=False, output=str(exc), error=str(exc))


# ─────────────────────────────────────────────────────────────
# get_plan_context — agent tool for PlanContext
# ─────────────────────────────────────────────────────────────


class GetPlanContextTool(BaseTool):
    """Return a contextual snapshot of the active space's plans, wiki, and docs."""

    name = "get_plan_context"
    description = (
        "Gather situational awareness from the active space: current phase, "
        "dev plan progress, related wiki pages, project docs, and inbox count. "
        "Call with an optional 'space' argument to target a specific space."
    )
    owner_only = False
    parameters = [
        {
            "name": "space",
            "type": "string",
            "description": "Space name (e.g. 'devops'). Omit for active space.",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        try:
            from navig.plans.context import PlanContext
            from navig.spaces.resolver import get_default_space

            mcp_enabled = False
            try:
                from navig.config import config

                agent_cfg = getattr(config, "agent", {})
                mcp_cfg = agent_cfg.get("mcp", {}) if hasattr(agent_cfg, "get") else {}
                mcp_enabled = bool(mcp_cfg.get("enabled", False))
            except Exception:
                mcp_enabled = False

            space = args.get("space") or get_default_space()
            ctx = PlanContext(
                space_root=str(paths.config_dir() / "spaces"),
                mcp_enabled=mcp_enabled,
            )
            snapshot = ctx.gather(space)
            return ToolResult(name=self.name, success=True, output=snapshot)
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Failed to gather plan context: {exc}",
                error=str(exc),
            )


# ─────────────────────────────────────────────────────────────
# Registration helpers
# ─────────────────────────────────────────────────────────────


def register_plan_context_tool() -> None:
    """Register the ``get_plan_context`` tool in the ``core`` toolset."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(GetPlanContextTool(), toolset="core")
    logger.debug("Agent tool registered: get_plan_context")


def register_plan_tools(interceptor: Any) -> None:
    """Register plan tools in the agent registry bound to *interceptor*.

    The tools are placed in the ``"plan"`` toolset and gated by a check_fn
    that returns ``True`` only when the interceptor is active.
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    set_interceptor(interceptor)

    _AGENT_REGISTRY.register(
        PlanAddStepTool(),
        toolset="plan",
        check_fn=lambda: interceptor.is_active,
    )
    _AGENT_REGISTRY.register(
        PlanShowTool(),
        toolset="plan",
        check_fn=lambda: interceptor.is_active,
    )
    _AGENT_REGISTRY.register(
        PlanApproveTool(),
        toolset="plan",
        check_fn=lambda: interceptor.is_active,
    )
    logger.debug("Plan tools registered (plan_add_step, plan_show, plan_approve)")
