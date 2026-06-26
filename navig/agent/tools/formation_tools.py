"""navig.agent.tools.formation_tools — run the active formation as a specialist team.

Exposes ``formation_run``: the agent can hand a complex request to the currently
active formation, which runs its specialist agents in parallel (each a real ReAct
child) and returns a synthesized, optionally-verified result.

Registered in the ``"coordinator"`` toolset (alongside ``coordinator_run``) so it is
NOT handed to formation sub-agents themselves — preventing recursive coordination.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


class FormationRunTool(BaseTool):
    """Run the active formation's specialists against a request and synthesize results."""

    name = "formation_run"
    description = (
        "Hand a complex, multi-disciplinary request to the active formation — a team "
        "of specialist agents (e.g. architect, devops, QA, security) — which work in "
        "parallel and return a synthesized answer. Use for tasks that benefit from "
        "multiple expert perspectives, not simple single-step work."
    )
    owner_only = False
    parameters = [
        {
            "name": "request",
            "type": "string",
            "description": "The request to run across the formation's specialists.",
            "required": True,
        },
        {
            "name": "max_workers",
            "type": "integer",
            "description": "Maximum specialists to run in parallel (default 3).",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        request = args.get("request", "")
        if not request:
            return ToolResult(name=self.name, success=False, error="'request' parameter is required")
        max_workers = int(args.get("max_workers", 3) or 3)

        await self._emit(on_status, "planning", "Assembling the formation's specialists")
        try:
            from navig.formations.coordinator import run_active_formation

            result = await run_active_formation(request, max_workers=max_workers)
            await self._emit(
                on_status, "done", f"{result.get('workers', 0)} specialist(s) finished"
            )
            return ToolResult(name=self.name, success=True, output=json.dumps(result))
        except Exception as exc:  # noqa: BLE001
            logger.debug("formation_run failed: %s", exc)
            return ToolResult(name=self.name, success=False, error=str(exc))
