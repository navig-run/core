"""
navig.agent.tools.coordinator_tools — Agent tools for coordinator mode.

Provides two tools for the LLM to orchestrate multi-step work:

* ``coordinator_run``    — dispatch a complex request to CoordinatorAgent
* ``coordinator_status`` — inspect per-worker outcomes from the last run

These tools are registered in the ``"coordinator"`` toolset.

FB-01 implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.agent.coordinator import CoordinatorAgent
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


# ── coordinator_run ──────────────────────────────────────────


class CoordinatorRunTool(BaseTool):
    """Dispatch a complex multi-step request to the coordinator agent."""

    name = "coordinator_run"
    description = (
        "Break a complex request into parallel worker tasks, execute them "
        "with appropriate models, and return a synthesised summary. "
        "Use this when a task clearly has multiple independent sub-steps."
    )
    owner_only = False
    parameters = [
        {
            "name": "request",
            "type": "string",
            "description": "The multi-step user request to orchestrate.",
            "required": True,
        },
        {
            "name": "tool_names",
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of tool names workers may use.",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        """Execute the coordinator orchestration."""
        request = args.get("request", "")
        if not request:
            return ToolResult(
                name=self.name,
                success=False,
                error="'request' parameter is required",
            )

        tool_names: list[str] = args.get("tool_names", [])

        await self._emit(on_status, "planning", "Breaking request into workers")

        try:
            registry = {name: None for name in tool_names}
            agent = CoordinatorAgent(tool_registry=registry)
            summary = await agent.orchestrate(request)

            # Store results for coordinator_status
            CoordinatorStatusTool._last_results = {
                wid: res.to_dict() for wid, res in agent.results.items()
            }

            await self._emit(on_status, "done", f"{agent.worker_count} workers finished")

            output = {
                "summary": summary,
                "workers": agent.worker_count,
                "failed": len(agent.failed_workers),
            }
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps(output),
            )

        except Exception as exc:
            logger.debug("coordinator_run failed: %s", exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
            )


# ── coordinator_status ───────────────────────────────────────


class CoordinatorStatusTool(BaseTool):
    """Inspect per-worker outcomes from the last coordinator run."""

    name = "coordinator_status"
    description = (
        "Return the status and results of each worker from the most recent "
        "coordinator_run invocation. Useful for debugging or reviewing "
        "individual worker outcomes."
    )
    owner_only = False
    parameters = [
        {
            "name": "coordinator_id",
            "type": "string",
            "description": "Reserved for future multi-session support. Currently ignored.",
            "required": False,
        },
    ]

    # Class-level storage populated by CoordinatorRunTool
    _last_results: dict[str, dict[str, Any]] = {}

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        """Return the per-worker results from the last coordinator run."""
        if not self._last_results:
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps({"workers": {}, "message": "No coordinator run results available"}),
            )

        await self._emit(on_status, "fetching", "Retrieving worker results")

        output = {
            "workers": self._last_results,
            "total": len(self._last_results),
        }
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(output),
        )
