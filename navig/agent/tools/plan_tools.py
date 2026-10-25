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
# Session-scoped interceptor binding  (replaces the old module-global
# `_interceptor_ref`, whose single slot let session A's plan tools answer
# for session B — see the two-session tests in tests/planning/).
# ─────────────────────────────────────────────────────────────

#: Bound sessions are capped so a long-lived process cannot accumulate
#: dead sessions; evicting the least-recently-bound one is safe because
#: plan state is in-memory only (a restart discards it anyway).
_MAX_BOUND_SESSIONS = 128

#: Slot for single-session callers and the historical no-key API.
_DEFAULT_SESSION_KEY = "<default>"

#: session key → live PlanInterceptor (insertion order = binding recency).
_interceptors: dict[str, Any] = {}


def set_interceptor(interceptor: Any, session_key: str | None = None) -> None:
    """Bind *interceptor* for *session_key* (or the default session slot).

    A tool that received a real ``_session_id`` NEVER falls back to the
    default slot — that fallback is exactly the cross-session leak this
    module-local store replaced.
    """
    key = session_key or _DEFAULT_SESSION_KEY
    _interceptors.pop(key, None)  # re-insert = most-recently-used order
    _interceptors[key] = interceptor
    while len(_interceptors) > _MAX_BOUND_SESSIONS:
        _interceptors.pop(next(iter(_interceptors)))


def release_interceptor(session_key: str | None = None) -> None:
    """Drop the binding for *session_key* (or the default slot)."""
    _interceptors.pop(session_key or _DEFAULT_SESSION_KEY, None)


def get_interceptor(session_key: str | None = None) -> Any | None:
    """The interceptor bound for *session_key*, or ``None``.

    ``None``/empty key addresses the default slot; any real key is looked up
    strictly (no cross-session fallback).
    """
    if not session_key:
        return _interceptors.get(_DEFAULT_SESSION_KEY)
    return _interceptors.get(session_key)


def _get_interceptor(session_key: str | None = None) -> Any:
    """Historical accessor — raises when nothing is bound (tests pin this)."""
    interceptor = get_interceptor(session_key)
    if interceptor is None:
        raise RuntimeError("Plan tools not initialised — call set_interceptor() first.")
    return interceptor


def _interceptor_for_args(args: dict[str, Any]) -> Any | None:
    """Resolve the caller's interceptor from the injected ``_session_id``."""
    return get_interceptor(args.get("_session_id"))


def interceptor_active(session_key: str | None) -> bool:
    """True when *session_key* has a live (non-INACTIVE) plan."""
    interceptor = get_interceptor(session_key)
    return bool(interceptor is not None and interceptor.is_active)


def ensure_interceptor(session_key: str) -> Any:
    """Return *session_key*'s active interceptor, constructing+starting one if needed.

    Idempotent: an already-active binding is returned untouched (plan state
    spans multiple ``run_agentic`` turns of the same session).
    """
    existing = get_interceptor(session_key)
    if existing is not None and existing.is_active:
        return existing
    from navig.agent.plan_mode import PlanInterceptor  # noqa: PLC0415

    interceptor = PlanInterceptor()
    interceptor.start()
    set_interceptor(interceptor, session_key)
    return interceptor


def plan_block_reason(tool_name: str, session_key: str | None) -> str | None:
    """The plan-mode write-gate verdict for one tool dispatch.

    Returns the block message during PLANNING for a non-read-only tool, else
    ``None``.  Fail-open by construction: no bound interceptor means no plan,
    so nothing is blocked.
    """
    interceptor = get_interceptor(session_key)
    if interceptor is None:
        return None
    return interceptor.get_block_reason(tool_name)


def apply_plan_toolsets(toolsets: list[str], session_key: str | None) -> list[str]:
    """Append the ``plan`` toolset when *session_key* has a live plan.

    This is how plan tools reach the model's schema: they register under the
    ``plan`` toolset unconditionally, but a turn only offers that toolset to
    the session that actually has a plan.
    """
    if interceptor_active(session_key) and "plan" not in toolsets:
        return [*toolsets, "plan"]
    return toolsets


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
    needs_session = True
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
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.plan_mode import PlanStep

        interceptor = _interceptor_for_args(args)
        if interceptor is None:
            return ToolResult(
                name=self.name,
                success=False,
                output=(
                    "Plan mode is not active in this session — there is no plan to "
                    "add steps to."
                ),
                error="no active plan for this session",
            )

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
    needs_session = True
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        interceptor = _interceptor_for_args(args)
        if interceptor is None:
            return ToolResult(
                name=self.name,
                success=False,
                output="Plan mode is not active in this session — no plan to show.",
                error="no active plan for this session",
            )
        text = interceptor.format_plan()
        return ToolResult(name=self.name, success=True, output=text)


# ─────────────────────────────────────────────────────────────
# plan_approve
# ─────────────────────────────────────────────────────────────


class PlanApproveTool(BaseTool):
    """Approve the plan and move to execute mode.

    The APPROVAL itself is enforced by the approval gate: ``plan_approve`` is
    listed in ``DESTRUCTIVE_TOOLS`` (and owner-only), so the model can never
    flip a plan to EXECUTING on its own — the operator approves the call
    through the normal human approval channel.  The state transition below
    only runs after that gate says yes.
    """

    name = "plan_approve"
    description = (
        "Approve the current plan.  Moves the plan from REVIEWING to EXECUTING "
        "so all tools become available again.  Requires operator approval."
    )
    owner_only = True
    needs_session = True
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        interceptor = _interceptor_for_args(args)
        if interceptor is None:
            return ToolResult(
                name=self.name,
                success=False,
                output="Plan mode is not active in this session — no plan to approve.",
                error="no active plan for this session",
            )
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
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        try:
            from navig.plans.context import PlanContext
            from navig.spaces.resolver import get_default_space

            mcp_enabled = False
            try:
                from navig.config import get as _cfg_get

                mcp_enabled = bool(_cfg_get("agent.mcp.enabled", False))
            except Exception:
                mcp_enabled = False

            space = args.get("space") or get_default_space()
            ctx = PlanContext(
                space_root=str(paths.spaces_dir()),
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


class ProjectContextTool(BaseTool):
    """Return a project's context pack — the budgeted description of the whole project."""

    name = "project_context"
    description = (
        "Get the full context of a project (a folder with .navig/): its vision, plans, "
        "current phase, open todos, agent instructions, README, runnable scripts, a code "
        "outline and recent git history — the same pack the Projects hub shows. Omit "
        "'project' for the active one. Use it before planning or changing a project."
    )
    owner_only = False
    parameters = [
        {
            "name": "project",
            "type": "string",
            "description": "Project id (see the Projects app or `navig project list`). Omit for the active project.",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        import asyncio

        def _build() -> dict[str, Any]:
            from navig.projects.context_pack import build_pack
            from navig.projects.model import resolve_project
            from navig.projects.store import get_project_store
            from navig.spaces import registry
            from navig.spaces.space_manifest import load_space_manifest

            project = str(args.get("project") or "").strip()
            if not project:
                active = registry.load_registry().get("active")
                row = registry.entry_for(active) if active else None
                if not row:
                    raise ValueError("no active project — pass 'project' (see navig project list)")
                project = str(row.get("id"))
            pid, root = resolve_project(project)
            manifest = load_space_manifest(root)
            pack = build_pack(root, name=manifest.resolved_name or root.name, manifest=manifest.data,
                              store=get_project_store())
            return {"project": pid, "path": str(root), "context": pack["markdown"],
                    "todo_counts": pack.get("todo_counts"),
                    "truncated_sources": [s["source"] for s in pack["report"]["sources"] if s["truncated"]]}

        try:
            return ToolResult(name=self.name, success=True, output=await asyncio.to_thread(_build))
        except Exception as exc:  # noqa: BLE001 — a tool reports, it never raises into the loop
            return ToolResult(name=self.name, success=False,
                              output=f"Could not build the project context: {exc}", error=str(exc))


# ─────────────────────────────────────────────────────────────
# Registration helpers
# ─────────────────────────────────────────────────────────────


def register_plan_context_tool() -> None:
    """Register ``get_plan_context`` and ``project_context`` in the ``core`` toolset."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(GetPlanContextTool(), toolset="core")
    _AGENT_REGISTRY.register(ProjectContextTool(), toolset="core")
    logger.debug("Agent tools registered: get_plan_context, project_context")


def register_plan_tools(interceptor: Any | None = None, *, session_key: str | None = None) -> None:
    """Register plan tools in the agent registry (toolset ``"plan"``).

    Registration is now unconditional and session-agnostic: availability is
    decided per session at exposure time (``apply_plan_toolsets``) and at
    dispatch time (``plan_block_reason`` / the tools' own ``_session_id``
    lookup), so there is no ``check_fn`` closure binding one session's
    interceptor into every other session's schemas.

    Args:
        interceptor: Optional interceptor to bind immediately (historical
            signature — single-session callers and tests pass one here).
        session_key: Session to bind *interceptor* for (default slot if omitted).
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    if interceptor is not None:
        set_interceptor(interceptor, session_key)

    _AGENT_REGISTRY.register(PlanAddStepTool(), toolset="plan")
    _AGENT_REGISTRY.register(PlanShowTool(), toolset="plan")
    _AGENT_REGISTRY.register(PlanApproveTool(), toolset="plan")
    logger.debug("Plan tools registered (plan_add_step, plan_show, plan_approve)")
