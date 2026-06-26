"""navig.formations.coordinator — run a formation as a live multi-agent team.

A *formation* declares a roster of specialist agents (architect, devops, QA, …),
each with its own persona, scope, and tool access. Historically this was metadata
only. This bridge turns it into execution: it maps each
:class:`~navig.formations.types.AgentSpec` to a coordinator
:class:`~navig.agent.coordinator.WorkerSpec` and drives the existing
:class:`~navig.agent.coordinator.CoordinatorAgent` — so the specialists run as real
parallel ReAct children and their outputs are synthesized (and, when the verifier is
enabled, verified) into one answer.

Entry points: the ``formation_run`` agent tool and ``navig formation run``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _specs_from_formation(formation: Any, request: str, max_workers: int) -> list:
    """Map a formation's loaded agents to coordinator WorkerSpecs."""
    from navig.agent.coordinator import WORKER_MODEL_FAST, WORKER_MODEL_SMART, WorkerSpec

    agents = list((getattr(formation, "loaded_agents", {}) or {}).values())
    specs: list = []
    for agent in agents[:max_workers]:
        # Heavier council weight → the smart (big) tier; everyone else → fast.
        model = WORKER_MODEL_SMART if getattr(agent, "council_weight", 1.0) > 1.0 else WORKER_MODEL_FAST
        task = (
            f"You are {agent.name} ({agent.role}).\n{agent.system_prompt}\n\n"
            f"Contribute your specialist perspective to this request:\n{request}"
        )
        specs.append(
            WorkerSpec(
                worker_id=agent.id,
                task_description=task,
                tools_allowed=list(getattr(agent, "tools", []) or []),
                model=model,
                context=getattr(agent, "personality", "") or "",
            )
        )
    return specs


async def run_formation_coordinator(
    formation: Any,
    request: str,
    *,
    max_workers: int = 3,
    user_identity: dict | None = None,
) -> dict[str, Any]:
    """Run *request* across *formation*'s specialists and return a synthesized result.

    Returns ``{"summary": str, "workers": int, "failed": int, "formation": id}``.
    """
    from navig.agent.coordinator import CoordinatorAgent

    if formation is None or not getattr(formation, "loaded_agents", None):
        return {"summary": "No active formation (or it has no loaded agents).", "workers": 0, "failed": 0}

    specs = _specs_from_formation(formation, request, max_workers)
    if not specs:
        return {"summary": "Formation has no specialists to run.", "workers": 0, "failed": 0}

    coordinator = CoordinatorAgent(
        session_context={
            "formation_id": getattr(formation, "id", ""),
            "user_identity": user_identity or {},
        }
    )
    summary = await coordinator.orchestrate(request, preset_specs=specs)
    return {
        "summary": summary,
        "workers": coordinator.worker_count,
        "failed": len(coordinator.failed_workers),
        "formation": getattr(formation, "id", ""),
    }


async def run_active_formation(request: str, *, max_workers: int = 3) -> dict[str, Any]:
    """Resolve the active formation and run it. Convenience for the tool/CLI."""
    try:
        from navig.formations.loader import get_active_formation

        formation = get_active_formation()
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not resolve active formation: %s", exc)
        formation = None
    return await run_formation_coordinator(formation, request, max_workers=max_workers)
