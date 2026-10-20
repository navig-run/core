"""Agent tool ``creative`` — capability discovery, reuse-first advice, proposals.

Four actions, all deterministic (no LLM inside the tool):

- ``lookup``  — token search over the capability index (works in BOTH modes).
- ``decide``  — run the reuse-first ladder against the index + recent history
  evidence (BOTH modes: discovery and reuse advice belong to Standard too).
- ``propose`` — persist a proposal. The verdict is RE-DERIVED by ``decide()``
  from grounded history; the model contributes wording (problem/solution) only,
  never the verdict or the evidence counts. Refuses outside Creative mode.
- ``status``  — mode + index freshness + proposal counts.

The SCHEMA is byte-identical in both modes (registration is unconditional and
the schema never reads the mode), so switching modes can never widen the
permission surface — gated by ``tests/agent/test_creative_tool.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_ACTIONS = "lookup | decide | propose | status"
_MAX_LOOKUP = 10


class CreativeTool(BaseTool):
    name = "creative"
    description = (
        "NAVIG capability intelligence. Use BEFORE creating anything new. "
        "`lookup` finds capabilities NAVIG already has; `decide` answers the "
        "reuse-first ladder (reuse/configure/compose/extend/create/noop) with "
        "evidence from the operator's own history; `propose` persists a grounded "
        "improvement proposal (Creative mode only); `status` shows index freshness "
        "and proposal counts. Never fabricates: verdicts come from the index."
    )
    owner_only = False
    parameters = [
        {
            "name": "action",
            "type": "string",
            "description": f"One of: {_ACTIONS}",
            "required": True,
        },
        {
            "name": "query",
            "type": "string",
            "description": "lookup: token search over capability names/summaries/tags.",
            "required": False,
        },
        {
            "name": "goal",
            "type": "string",
            "description": "decide/propose: what the operator is trying to accomplish.",
            "required": False,
        },
        {
            "name": "problem",
            "type": "string",
            "description": "propose: one-line problem statement for the card (your wording).",
            "required": False,
        },
        {
            "name": "solution",
            "type": "string",
            "description": "propose: optional solution wording for the card.",
            "required": False,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": "lookup: max rows (default 5, cap 10).",
            "required": False,
        },
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        action = str(args.get("action") or "").strip().lower()
        try:
            if action == "lookup":
                output = self._lookup(args)
            elif action == "decide":
                output = self._decide(args)
            elif action == "propose":
                output = self._propose(args)
            elif action == "status":
                output = self._status()
            else:
                return ToolResult(
                    name=self.name, success=False,
                    error=f"unknown action {action!r} — one of: {_ACTIONS}",
                )
            return ToolResult(name=self.name, success=True, output=output)
        except Exception as exc:  # noqa: BLE001 - the contract: never raise
            logger.debug("creative tool failed", exc_info=True)
            return ToolResult(name=self.name, success=False, error=str(exc))

    # ── actions ──────────────────────────────────────────────────────────

    def _lookup(self, args: dict[str, Any]) -> dict[str, Any]:
        from navig.creative.index import ensure_index, find

        query = str(args.get("query") or "")
        try:
            limit = max(1, min(_MAX_LOOKUP, int(args.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        snap = ensure_index()
        hits = find(query, limit=limit, index=snap)
        # Token-bounded on purpose: id/kind/summary/invoke/state only — the full
        # capability graph never enters the context.
        return {
            "count": len(hits),
            "entries": [
                {
                    "id": h.get("id"),
                    "kind": h.get("kind"),
                    "name": h.get("name"),
                    "summary": str(h.get("summary") or "")[:200],
                    "how": (h.get("how_to_invoke") or {}).get("ref"),
                    "state": (h.get("observed") or {}).get("state"),
                }
                for h in hits
            ],
        }

    def _decide(self, args: dict[str, Any]) -> dict[str, Any]:
        from dataclasses import asdict

        from navig.creative.decide import IndexView, decide
        from navig.creative.signals import _read_operations, collect_evidence

        goal = str(args.get("goal") or "")
        evidence, _candidates = collect_evidence(records=_read_operations(), goals=[], index=IndexView.live())
        decision = decide(goal, evidence=evidence, index=IndexView.live(), config={})
        return asdict(decision)

    def _propose(self, args: dict[str, Any]) -> dict[str, Any]:
        from navig.creative import proposals as prop
        from navig.creative.decide import IndexView, decide
        from navig.creative.mode import get_agent_mode
        from navig.creative.proposals import from_decision
        from navig.creative.signals import _read_operations, collect_evidence

        if get_agent_mode() != "creative":
            return {
                "ok": False,
                "reason": "Creative mode is off — discovery and reuse advice are "
                "available (lookup/decide); proposals and authoring need Agent "
                "Mode → Creative (Settings → Brain).",
            }

        goal = str(args.get("goal") or "").strip()
        if not goal:
            return {"ok": False, "reason": "goal is required to propose"}
        evidence, _candidates = collect_evidence(
            records=_read_operations(), goals=[], index=IndexView.live()
        )
        decision = decide(goal, evidence=evidence, index=IndexView.live(), config={})
        if decision.is_noop:
            return {
                "ok": True,
                "created": False,
                "verdict": "noop",
                "reason": decision.reason,
            }
        problem = str(args.get("problem") or "").strip() or decision.reason
        solution = str(args.get("solution") or "").strip() or None
        # Evidence rows are built from what the collectors ACTUALLY saw — the
        # model never supplies counts. Falls back to the request itself only
        # when history carried no signal (e.g. a first-session goal).
        evidence_rows: list[dict[str, Any]] = [
            {"kind": "repeated_command", "detail": c, "count": n, "window": "7d"}
            for c, n in evidence.repeated_commands[:3]
        ]
        evidence_rows += [
            {"kind": "failure", "detail": c, "count": n, "window": "7d"}
            for c, n in evidence.failing_commands[:2]
        ]
        evidence_rows += [
            {"kind": "sequence", "detail": " → ".join(seq), "count": n, "window": "14d"}
            for seq, n in evidence.sequences[:2]
        ]
        evidence_rows += [
            {"kind": "blocked_goal", "detail": g, "count": 1, "window": "current"}
            for g in evidence.blocked_goals[:2]
        ]
        if not evidence_rows:
            evidence_rows = [
                {"kind": "agent_request", "detail": goal, "count": 1, "window": "session"}
            ]
        proposal = from_decision(
            decision,
            source="agent:chat",
            problem=problem,
            evidence=evidence_rows,
            solution=solution,
            goal=goal,
        )
        created, reason = prop.propose(proposal)
        if not created:
            return {"ok": True, "created": False, "reason": reason,
                    "verdict": decision.verdict}
        dispatched = False
        if prop.claim_announce(proposal["id"]):
            try:
                from navig.creative.signals import _default_dispatch

                _default_dispatch(proposal)
                dispatched = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("creative propose dispatch failed: %s", exc)
                prop.release_announce(proposal["id"])
        return {
            "ok": True,
            "created": True,
            "id": proposal["id"],
            "verdict": decision.verdict,
            "dispatched": dispatched,
        }

    def _status(self) -> dict[str, Any]:
        from navig.creative import proposals as prop
        from navig.creative.index import status as index_status
        from navig.creative.mode import get_agent_mode

        rows = prop.list_proposals()
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row.get("status", "?")] = by_status.get(row.get("status", "?"), 0) + 1
        return {
            "mode": get_agent_mode(),
            "index": index_status(),
            "proposals": {"total": len(rows), "by_status": by_status},
        }


def register_creative_tool() -> None:
    """Register the ``creative`` tool (called from ``register_all_tools``)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(CreativeTool(), toolset="creative")
    logger.debug("Agent creative tool registered")


def _schema_fingerprint() -> str:  # pragma: no cover - test helper seam
    """Stable fingerprint of the tool's OpenAI schema (mode-independence check)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    for entry in _AGENT_REGISTRY.entries():
        if entry.name == "creative":
            return json.dumps(entry.schema, sort_keys=True)
    raise KeyError("creative tool not registered")
