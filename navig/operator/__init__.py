"""navig.operator — the operator's planning + dispatch surface.

The operator turns the user's current situation (finance, inbox, spaces,
projects) into a short situational read plus a handful of concrete next steps.
Surfaced by the deck RUN button as a plan-mode wizard.
"""

from navig.operator.planner import (
    OperatorPlan,
    PlanStep,
    build_plan,
    gather_context,
)

__all__ = ["OperatorPlan", "PlanStep", "build_plan", "gather_context"]
