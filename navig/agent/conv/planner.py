"""PlanExtractor, PlanValidator, FallbackPlanner: JSON plan parsing and no-AI fallback."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, TypedDict

import json_repair  # ships py.typed — fully typed, no ignore needed

logger = logging.getLogger(__name__)

# ── Typed plan schema ─────────────────────────────────────────────────────────


class PlanStep(TypedDict):
    """A single execution step within a validated plan."""

    action: str
    description: str
    params: dict[str, Any]
    confirmation_needed: bool


class ValidatedPlan(TypedDict):
    """A structurally validated, fully typed plan ready for execution."""

    plan: list[PlanStep]


# ── Action registries ─────────────────────────────────────────────────────────
# Derived from ActionRegistry — single source of truth.
# Planner/validator use these for fast membership tests; they are intentionally
# kept as frozensets so existing callers see no API change.

from navig.agent.action_registry import get_action_registry as _get_registry

KNOWN_ACTIONS: frozenset[str] = _get_registry().known_ids()
ACTIONS_REQUIRING_PARAMS: frozenset[str] = _get_registry().requires_params_ids()


# ── Plan extraction ──────────────────────────────────────────────────────────


class PlanExtractor:
    """
    Parses a JSON plan block out of a free-form AI response string.
    Three ordered strategies: fenced ```json block, brace substring, json-repair.
    Guarantees: extract() and _extract_plan() never raise; return None on failure.
    """

    @classmethod
    def extract(cls, raw: str) -> Any | None:
        """
        Return the first parseable JSON object found in *raw*, or None.

        Strategies attempted in order:
          1. Fenced ```json … ``` block (greedy, captures nested braces).
          2. Substring from first ``{`` to last ``}`` via json.loads().
          3. Full-string repair via json_repair.repair_json() + json.loads().

        Never raises; logs debug on total failure.
        """
        try:
            # Strategy 1: fenced ```json ... ``` block — greedy to capture nested braces.
            match = re.search(r"```json\s*(\{.*\})\s*```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass  # malformed JSON; skip line

            # Strategy 2: substring from first '{' to last '}'.
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    pass  # malformed JSON; skip line

            # Strategy 3: json-repair — handles truncated / malformed LLM JSON.
            try:
                repaired: str = json_repair.repair_json(raw)
                if repaired and repaired.strip() not in ("null", '""', ""):
                    candidate = json.loads(repaired)
                    if isinstance(candidate, dict):
                        return candidate
            except Exception as exc:
                logger.debug("PlanExtractor: json_repair strategy failed: %s", exc)

            return None

        except Exception as exc:
            logger.debug("PlanExtractor.extract: all strategies failed: %s", exc)
            return None

    def _extract_plan(self, raw: str) -> Optional[ValidatedPlan]:
        """
        Convenience pipeline: extract raw JSON then validate and type it.

        Returns a ``ValidatedPlan`` on success, ``None`` on any failure.
        Never raises — the entire body is guarded by a top-level exception handler.
        """
        try:
            data = PlanExtractor.extract(raw)
            if data is None:
                return None
            return PlanValidator.validate(data)
        except Exception as exc:
            logger.warning("PlanExtractor._extract_plan: unexpected error: %s", exc)
            return None


# ── Plan validation & scoring ─────────────────────────────────────────────────


class PlanValidator:
    """
    Validates and coerces a raw dict into a ``ValidatedPlan`` TypedDict.
    Owns the field-presence schema and quality scoring logic.
    Guarantees: validate() and score() are pure and stateless; never raise.
    """

    @classmethod
    def validate(cls, data: Any) -> Optional[ValidatedPlan]:
        """
        Return a ``ValidatedPlan`` if *data* satisfies the plan schema, else ``None``.

        Coercion (does NOT fail):
          - ``params`` missing or wrong type → coerced to ``{}``

        Hard-fail (returns ``None``):
          - *data* is not a dict, or has no ``"plan"`` key
          - ``plan`` value is not a ``list``
          - Any step is not a dict
          - Any step ``action`` is not ``str``
          - Any step ``description`` is not ``str``
          - Any step ``confirmation_needed`` is not ``bool``, ``int``, or ``None``
            (strings like ``"true"`` are rejected as malformed LLM output)
        """
        if not isinstance(data, dict):
            return None
        raw_steps = data.get("plan")
        if not isinstance(raw_steps, list):
            return None

        coerced: list[PlanStep] = []
        for step in raw_steps:
            if not isinstance(step, dict):
                return None

            action = step.get("action")
            if not isinstance(action, str):
                return None

            description = step.get("description", "")
            if not isinstance(description, str):
                return None

            params: dict[str, Any] = step.get("params", {})
            if not isinstance(params, dict):
                params = {}  # coerce — do not fail

            # Coerce to bool — LLMs often emit 0/1 or null instead of a JSON boolean.
            # Reject string values ("true"/"false") as malformed; only int/None are coerced.
            # Only hard-fail when the key is missing entirely OR the value is a string.
            raw_cn = step.get("confirmation_needed", False)
            if not isinstance(raw_cn, (bool, int, type(None))):
                return None
            confirmation_needed: bool = bool(raw_cn)

            coerced.append(
                PlanStep(
                    action=action,
                    description=description,
                    params=params,
                    confirmation_needed=confirmation_needed,
                )
            )

        return ValidatedPlan(plan=coerced)

    @classmethod
    def score(cls, plan: ValidatedPlan) -> float:
        """
        Return a quality score in ``[0.0, 1.0]`` for *plan*.

        Applies per-step penalties then clamps to ``[0.0, 1.0]``.
        Returns ``0.0`` when the plan has no steps.
        Pure function — no side effects.

        Penalties per step:
          +0.4  ``action`` not in ``KNOWN_ACTIONS``
          +0.3  ``description`` is empty or whitespace-only
          +0.3  ``action`` in ``ACTIONS_REQUIRING_PARAMS`` and ``params == {}``
        """
        steps = plan["plan"]
        n = len(steps)
        if n == 0:
            return 0.0
        total_penalty = 0.0
        for step in steps:
            if step["action"] not in KNOWN_ACTIONS:
                total_penalty += 0.4
            if not step["description"].strip():
                total_penalty += 0.3
            if step["action"] in ACTIONS_REQUIRING_PARAMS and step["params"] == {}:
                total_penalty += 0.3
        return max(0.0, min(1.0, 1.0 - total_penalty / n))


# ── No-AI fallback ───────────────────────────────────────────────────────────


class FallbackPlanner:
    """
    Pattern-matching plan generator used when no AI client is configured.
    Owns the intent-to-plan routing for common automation intents.
    Guarantees: plan() never raises; returns None for unrecognised intents.
    """

    def plan(self, message: str) -> dict[str, Any] | None:
        """Derive a JSON plan dict from *message* via keyword patterns, or None."""
        lower = message.lower()

        if any(w in lower for w in ("open", "launch", "start", "run")):
            m = re.search(r"(?:open|launch|start|run)\s+(?:the\s+)?(\w+)", lower)
            if m:
                app = m.group(1)
                return _make_plan(
                    f"You want me to open {app}",
                    [{"action": "auto.open_app", "params": {"target": app},
                      "description": f"Opening {app}"}],
                    message=f"Sure! Opening {app} 🚀",
                )

        if any(w in lower for w in ("click", "press", "tap")):
            m = re.search(r"(\d+)[,\s]+(\d+)", message)
            if m:
                x, y = int(m.group(1)), int(m.group(2))
                return _make_plan(
                    f"You want me to click at ({x}, {y})",
                    [{"action": "auto.click", "params": {"x": x, "y": y},
                      "description": f"Clicking at ({x}, {y})"}],
                    message=f"Clicking at ({x}, {y}) 👆",
                )

        if any(w in lower for w in ("type", "write", "enter")):
            m = re.search(r'(?:type|write|enter)\s+["\']?(.+?)["\']?$', message, re.IGNORECASE)
            if m:
                text = m.group(1)
                return _make_plan(
                    f"You want me to type: {text}",
                    [{"action": "auto.type", "params": {"text": text},
                      "description": "Typing text"}],
                    message="Typing that for you! ⌨️",
                )

        if "workflow" in lower or "automate" in lower:
            m = re.search(
                r"(?:create|make|generate)\s+(?:a\s+)?workflow\s+(?:to|that|for)?\s*(.+)",
                lower,
            )
            if m:
                desc = m.group(1)
                return _make_plan(
                    f"Create a workflow: {desc}",
                    [{"action": "evolve.workflow", "params": {"goal": desc},
                      "description": f"Creating workflow: {desc}"}],
                    confirmation_needed=True,
                    message=f"I'll create a workflow to {desc}. Proceed? 🛠️",
                )

        return None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plan(
    understanding: str,
    steps: list[dict[str, Any]],
    *,
    confirmation_needed: bool = False,
    message: str = "",
) -> dict[str, Any]:
    return {
        "understanding": understanding,
        "plan": steps,
        "confirmation_needed": confirmation_needed,
        "message": message,
    }
