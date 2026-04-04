"""
navig.agent.plan_execute — Plan-Execute Agent Mode (MVP3 F-21).

Two-phase autonomous agent:
1. **Plan phase**: one LLM call asks for a structured JSON plan
   ``{steps: [{tool, args, reason}]}``.
2. **Approval gate**: show plan to user; require y/N confirmation
   (auto-approve with ``--yes``).
3. **Execute phase**: run each plan step as a tool call; on failure
   ask the LLM to revise remaining steps.
4. **Report phase**: generate final summary of what was done,
   success/failure per step, and cost summary.

Usage::

    from navig.agent.plan_execute import PlanExecuteAgent
    agent = PlanExecuteAgent(conversational_agent)
    result = await agent.run("restart nginx and verify health")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────

@dataclass
class PlanStep:
    """A single step in the execution plan."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    # Filled during execution
    status: str = "pending"  # pending | running | success | failed | skipped
    output: str = ""
    error: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "reason": self.reason,
            "status": self.status,
            "output": self.output[:500] if self.output else "",
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class ExecutionPlan:
    """Structured plan produced by the planning phase."""

    task: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str = ""
    total_elapsed_ms: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def succeeded(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "success"]

    @property
    def failed(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "failed"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "created_at": self.created_at,
            "total_elapsed_ms": round(self.total_elapsed_ms, 1),
            "steps": [s.to_dict() for s in self.steps],
            "summary": {
                "total": len(self.steps),
                "succeeded": len(self.succeeded),
                "failed": len(self.failed),
                "skipped": sum(1 for s in self.steps if s.status == "skipped"),
            },
        }


# ─────────────────────────────────────────────────────────────
# Planning prompt
# ─────────────────────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """\
You are a planning assistant for the NAVIG DevOps agent.  Your job is to
produce a structured execution plan for the user's task.

You have access to these tools: {tool_names}

Respond with ONLY a JSON object in this exact format (no markdown, no explanation):

{{
  "steps": [
    {{
      "tool": "<tool_name>",
      "args": {{"<param>": "<value>", ...}},
      "reason": "Brief explanation of why this step is needed"
    }}
  ]
}}

Rules:
- Each step must use exactly one tool from the available list.
- Steps are executed sequentially in order.
- Keep the plan minimal — only include necessary steps.
- If the task doesn't require any tools, return {{"steps": []}}.
- Do NOT include commentary outside the JSON.
"""

_REVISE_PROMPT = """\
Step {step_num} failed.
Tool: {tool}
Error: {error}

Remaining steps in the original plan:
{remaining_json}

Revise the remaining steps to account for this failure.
Respond with ONLY a JSON object: {{"steps": [...]}}
If no further steps are possible, return {{"steps": []}}.
"""


# ─────────────────────────────────────────────────────────────
# PlanExecuteAgent
# ─────────────────────────────────────────────────────────────

class PlanExecuteAgent:
    """Two-phase plan-execute agent mode.

    Uses the ConversationalAgent's LLM client and tool registry for execution.
    """

    def __init__(self, conversational_agent: Any):
        self._agent = conversational_agent

    async def run(
        self,
        task: str,
        *,
        toolset: str | list[str] = "core",
        dry_run: bool = False,
        auto_approve: bool = False,
        max_retries: int = 1,
    ) -> ExecutionPlan:
        """Run the full plan-execute cycle.

        Args:
            task:          Natural-language task description.
            toolset:       Toolset(s) to make available.
            dry_run:       If True, plan only — no execution.
            auto_approve:  If True, skip the user confirmation prompt.
            max_retries:   How many times to attempt LLM-driven plan revision
                           on step failure (default: 1).

        Returns:
            :class:`ExecutionPlan` with per-step results.
        """
        t0 = time.monotonic()

        # ── Phase 1: Plan ──
        plan = await self._plan(task, toolset=toolset)
        if not plan.steps:
            plan.total_elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("Plan-execute: empty plan for task %r", task)
            return plan

        if dry_run:
            plan.total_elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("Plan-execute: dry-run — %d steps planned", len(plan.steps))
            return plan

        # ── Phase 2: Approval ──
        if not auto_approve:
            approved = await self._request_approval(plan)
            if not approved:
                for step in plan.steps:
                    step.status = "skipped"
                plan.total_elapsed_ms = (time.monotonic() - t0) * 1000
                return plan

        # ── Phase 3: Execute ──
        await self._execute(plan, toolset=toolset, max_retries=max_retries)

        plan.total_elapsed_ms = (time.monotonic() - t0) * 1000

        # ── Phase 4: Save trace ──
        self._save_trace(plan)

        return plan

    # ── Phase 1: Planning ─────────────────────────────────

    async def _plan(
        self,
        task: str,
        toolset: str | list[str] = "core",
    ) -> ExecutionPlan:
        """Ask the LLM to produce a structured execution plan."""
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        toolsets = [toolset] if isinstance(toolset, str) else list(toolset)
        tool_names = _AGENT_REGISTRY.available_names(toolsets=toolsets)

        system = _PLAN_SYSTEM_PROMPT.format(tool_names=", ".join(tool_names))

        plan_json = await self._llm_call(system, task)
        steps = self._parse_plan_json(plan_json)
        return ExecutionPlan(task=task, steps=steps)

    def _parse_plan_json(self, raw: str) -> list[PlanStep]:
        """Parse the LLM's JSON plan into PlanStep objects."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Plan-execute: failed to parse plan JSON: %s", text[:200])
            return []

        steps: list[PlanStep] = []
        for item in data.get("steps", []):
            if not isinstance(item, dict):
                continue
            steps.append(
                PlanStep(
                    tool=item.get("tool", ""),
                    args=item.get("args", {}),
                    reason=item.get("reason", ""),
                )
            )
        return steps

    # ── Phase 2: Approval ─────────────────────────────────

    async def _request_approval(self, plan: ExecutionPlan) -> bool:
        """Present the plan and ask for user confirmation.

        Uses navig's console_helper for interactive prompts.
        Falls back to auto-approve if no TTY is available.
        """
        try:
            from navig import console_helper as ch

            ch.info(f"Execution plan for: {plan.task}")
            for i, step in enumerate(plan.steps, 1):
                ch.dim(f"  {i}. [{step.tool}] {step.reason}")
                if step.args:
                    for k, v in step.args.items():
                        ch.dim(f"     {k}={v}")

            import sys
            if not sys.stdin.isatty():
                logger.debug("Plan-execute: non-interactive — auto-approving")
                return True

            answer = input("\nProceed with execution? [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except Exception:
            # Non-interactive or import failure — auto-approve
            return True

    # ── Phase 3: Execution ────────────────────────────────

    async def _execute(
        self,
        plan: ExecutionPlan,
        toolset: str | list[str] = "core",
        max_retries: int = 1,
    ) -> None:
        """Execute plan steps sequentially via the tool registry."""
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        remaining = list(plan.steps)
        idx = 0

        while idx < len(remaining):
            step = remaining[idx]
            step.status = "running"
            logger.info("Plan-execute: step %d/%d — %s", idx + 1, len(remaining), step.tool)

            t0 = time.monotonic()
            try:
                result = await _AGENT_REGISTRY.dispatch(step.tool, step.args)
                step.elapsed_ms = (time.monotonic() - t0) * 1000

                if result.success:
                    step.status = "success"
                    step.output = str(result.output or "")[:2000]
                else:
                    step.status = "failed"
                    step.error = result.error or "Unknown error"
                    logger.warning("Plan-execute: step %d failed — %s", idx + 1, step.error)

                    # Attempt revision
                    if max_retries > 0:
                        revised = await self._revise_plan(
                            idx + 1, step, remaining[idx + 1:], toolset
                        )
                        if revised:
                            # Replace remaining steps with revision
                            remaining = remaining[: idx + 1] + revised
                            max_retries -= 1
            except Exception as exc:
                step.elapsed_ms = (time.monotonic() - t0) * 1000
                step.status = "failed"
                step.error = str(exc)
                logger.exception("Plan-execute: step %d exception", idx + 1)

            idx += 1

        # Update plan.steps with the (possibly revised) remaining list
        plan.steps = remaining

    async def _revise_plan(
        self,
        failed_step_num: int,
        failed_step: PlanStep,
        remaining_steps: list[PlanStep],
        toolset: str | list[str],
    ) -> list[PlanStep] | None:
        """Ask the LLM to revise remaining steps after a failure."""
        remaining_json = json.dumps(
            [{"tool": s.tool, "args": s.args, "reason": s.reason} for s in remaining_steps],
            indent=2,
        )
        prompt = _REVISE_PROMPT.format(
            step_num=failed_step_num,
            tool=failed_step.tool,
            error=failed_step.error,
            remaining_json=remaining_json,
        )
        system = "You are a plan revision assistant. Revise the plan based on the failure."

        raw = await self._llm_call(system, prompt)
        revised = self._parse_plan_json(raw)
        if revised:
            logger.info("Plan-execute: revised %d remaining steps to %d", len(remaining_steps), len(revised))
        return revised or None

    # ── Phase 4: Trace persistence ────────────────────────

    def _save_trace(self, plan: ExecutionPlan) -> None:
        """Save the execution trace to ``.navig/plans/runs/{timestamp}.json``."""
        try:
            runs_dir = Path.home() / ".navig" / "plans" / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            trace_path = runs_dir / f"{ts}.json"
            trace_path.write_text(
                json.dumps(plan.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
            logger.info("Plan-execute: trace saved to %s", trace_path)
        except Exception as exc:
            logger.debug("Plan-execute: failed to save trace — %s", exc)

    # ── LLM call helper ──────────────────────────────────

    async def _llm_call(self, system: str, user_message: str) -> str:
        """Make a single LLM call and return the text content."""
        try:
            from navig.llm_generate import run_llm
            result = await asyncio.to_thread(
                run_llm,
                user_message,
                system_prompt=system,
            )
            if result and hasattr(result, "content"):
                return result.content or ""
            return str(result) if result else ""
        except Exception as exc:
            logger.error("Plan-execute: LLM call failed — %s", exc)
            return '{"steps": []}'


# ─────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────

def format_plan_report(plan: ExecutionPlan) -> str:
    """Format a human-readable report from a completed execution plan."""
    lines: list[str] = [
        "## Execution Report",
        f"**Task:** {plan.task}",
        f"**Started:** {plan.created_at}",
        f"**Total time:** {plan.total_elapsed_ms / 1000:.1f}s",
        "",
    ]

    for i, step in enumerate(plan.steps, 1):
        icon = {"success": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏳"}.get(
            step.status, "❓"
        )
        lines.append(f"{icon} **Step {i}** — `{step.tool}` ({step.status})")
        if step.reason:
            lines.append(f"   {step.reason}")
        if step.error:
            lines.append(f"   Error: {step.error}")
        if step.output:
            preview = step.output[:200].replace("\n", " ")
            lines.append(f"   Output: {preview}")
        lines.append(f"   Time: {step.elapsed_ms / 1000:.1f}s")
        lines.append("")

    summary = plan.to_dict()["summary"]
    lines.append(
        f"**Summary:** {summary['succeeded']}/{summary['total']} succeeded, "
        f"{summary['failed']} failed, {summary['skipped']} skipped"
    )
    return "\n".join(lines)
