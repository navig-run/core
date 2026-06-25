"""navig.operator.planner — build the operator's grouped "next steps" plan.

Pipeline:

  gather_context()  →  best-effort signals (finance, spaces, inbox), each source
                       independently guarded so one failure never blocks RUN.
  build_plan()      →  ask the LLM for a structured, GROUPED plan (strict JSON),
                       falling back to a deterministic plan from the raw signals
                       when no LLM is configured or parsing fails. RUN must never
                       dead-end, so the fallback is load-bearing.

The result is a serialisable ``OperatorPlan`` — a short situational summary plus
several titled plan groups (Finance / Projects / Life / Growth), each with its
own sub-steps. The deck renders it as a plan-mode wizard (approve / edit / write
a step). Step ids are unique across the whole plan so a chosen step resolves
regardless of its group.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("navig.operator.planner")

# Allowed plan-group categories (the deck maps these to icons/labels).
_CATEGORIES = ("finance", "projects", "life", "growth")
_CATEGORY_TITLES = {
    "finance": "Finance",
    "projects": "Projects",
    "life": "Life",
    "growth": "Growth",
}


@dataclass
class PlanStep:
    """A single proposed next action."""

    id: str  # globally-unique within a plan, e.g. "finance-1"
    title: str
    rationale: str = ""  # why this step matters
    instructions: str = ""  # what to do + the outcome/goal it produces
    effort: str = "medium"  # low | medium | high
    impact: str = "medium"  # low | medium | high
    capability: str = "agentic"  # how it should run as a mission
    source_ref: str = ""  # e.g. "space:navig-core" / "inbox" — traceability


@dataclass
class PlanGroup:
    """A titled cluster of steps for one life/business area."""

    id: str  # == category for now
    title: str
    category: str  # finance | projects | life | growth
    focus: str = ""  # one-line "why this area matters right now"
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class OperatorPlan:
    summary: str
    plans: list[PlanGroup] = field(default_factory=list)
    context_digest: dict[str, Any] = field(default_factory=dict)
    generated_by: str = "llm"  # "llm" | "fallback"

    def all_steps(self) -> list[PlanStep]:
        """Flatten every group's steps (for cross-group resolution)."""
        return [s for g in self.plans for s in g.steps]

    def to_payload(self) -> dict:
        """The shape the deck wizard consumes."""
        return {
            "summary": self.summary,
            "plans": [
                {
                    "id": g.id,
                    "title": g.title,
                    "category": g.category,
                    "focus": g.focus,
                    "steps": [
                        {
                            "id": s.id,
                            "title": s.title,
                            "rationale": s.rationale,
                            "instructions": s.instructions,
                            "effort": s.effort,
                            "impact": s.impact,
                        }
                        for s in g.steps
                    ],
                }
                for g in self.plans
            ],
            "generatedBy": self.generated_by,
        }


# ── context gathering ────────────────────────────────────────────────────────


def gather_context(cwd=None) -> dict[str, Any]:
    """Cheap, best-effort signal collection. Each source is independently
    try/excepted so one failure never blocks RUN."""
    ctx: dict[str, Any] = {}

    # Finance / gains
    try:
        from navig_harbor.bizops import get_overview

        ov = get_overview()
        mp = ov.get("most_profitable_project") or {}

        def _usd(cents) -> str:
            return f"${(int(cents or 0)) / 100:,.2f}"

        ctx["finance"] = {
            # Pre-formatted as money so the LLM never echoes raw cents.
            "net_this_month": _usd(ov.get("net_profit_cents")),
            "revenue_this_month": _usd(ov.get("monthly_revenue_cents")),
            "lifetime_earned": _usd(ov.get("lifetime_earned_cents")),
            "cash_on_hand": _usd(ov.get("total_cash_cents")),
            "runway_months": ov.get("runway_months"),
            "overdue_invoices": ov.get("overdue_invoices_count"),
            "open_invoices": ov.get("open_invoices_count"),
            "top_project": mp.get("name"),
            "revenue_delta_pct": (ov.get("deltas") or {}).get("revenue_pct_vs_last_month"),
        }
    except Exception:
        logger.debug("planner finance ctx failed", exc_info=True)

    # Spaces — the single best next action
    try:
        from navig.spaces.next_action import select_best_next_action

        best = select_best_next_action(cwd=cwd)
        if best:
            ctx["next_action"] = {
                "space": best.space,
                "goal": best.goal,
                "completion_pct": best.completion_pct,
                "next_task": best.next_task,
            }
    except Exception:
        logger.debug("planner spaces ctx failed", exc_info=True)

    # Inbox — pending doc count + a few sample names
    try:
        from navig.gateway.deck.routes.inbox import _find_project_root, _scan_inbox_dirs

        root = _find_project_root()
        files = _scan_inbox_dirs(root)
        ctx["inbox"] = {
            "pending": len(files),
            "samples": [f.name for f in files[:5]],
        }
    except Exception:
        logger.debug("planner inbox ctx failed", exc_info=True)

    return ctx


# ── LLM structuring ──────────────────────────────────────────────────────────

_SYS = (
    "You are NAVIG's operator — a productivity, business and life co-pilot. "
    "Given the user's current situation signals, propose SEVERAL grouped plans "
    "that move their business and life forward. Group steps by area: use the "
    "categories finance, projects, life, growth (include only the relevant "
    "areas, 2-4 groups). Each group has 2-3 concrete, specific, actionable "
    "steps. Do not invent a revenue decline from a partial month. Money values "
    "in the signals are already formatted — never output raw cents. "
    "For each step give a short 'instructions': one sentence on what to do and "
    "what finished result/goal it produces. "
    "Respond with STRICT JSON only — no prose, no markdown fences — matching:\n"
    '{"summary": "<=2 sentence read of where things stand", '
    '"plans": [{"title": str, "category": "finance|projects|life|growth", '
    '"focus": "<one line why this area matters now>", '
    '"steps": [{"title": str, "rationale": "<why>", '
    '"instructions": "<what to do + the outcome/goal>", '
    '"effort": "low|medium|high", "impact": "low|medium|high"}]}]}'
)


async def build_plan(cwd=None) -> OperatorPlan:
    """Gather context → grouped plan via LLM, with deterministic fallback."""
    ctx = gather_context(cwd)
    plan = await _plan_via_llm(ctx)
    if plan is None:
        plan = _fallback_plan(ctx)
    return plan


async def _plan_via_llm(ctx: dict) -> OperatorPlan | None:
    user = "Current situation signals (JSON):\n" + json.dumps(ctx, default=str, indent=2)
    try:
        from navig.llm_generate import llm_generate

        raw = await asyncio.to_thread(
            llm_generate,
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user},
            ],
            mode="planning",
            temperature=0.4,
            max_tokens=1200,
        )
    except Exception:
        logger.debug("planner LLM call failed", exc_info=True)
        return None

    data = _extract_json(raw)
    if not data:
        return None

    raw_groups = data.get("plans")
    # Back-compat: tolerate a flat {"steps":[...]} response by wrapping it.
    if not isinstance(raw_groups, list):
        flat = data.get("steps")
        if isinstance(flat, list):
            raw_groups = [{"title": "Next steps", "category": "projects", "steps": flat}]
        else:
            return None

    groups: list[PlanGroup] = []
    for g in raw_groups[:4]:
        if not isinstance(g, dict):
            continue
        category = _clamp_category(g.get("category"))
        raw_steps = g.get("steps")
        if not isinstance(raw_steps, list):
            continue
        steps: list[PlanStep] = []
        for s in raw_steps[:4]:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title", "")).strip()
            if not title:
                continue
            steps.append(
                PlanStep(
                    id=f"{category}-{len(steps) + 1}-{len(groups)}",
                    title=title,
                    rationale=str(s.get("rationale", "")).strip(),
                    instructions=str(s.get("instructions", "")).strip(),
                    effort=_clamp_level(s.get("effort")),
                    impact=_clamp_level(s.get("impact")),
                )
            )
        if not steps:
            continue
        groups.append(
            PlanGroup(
                id=f"{category}-{len(groups)}",
                title=str(g.get("title", "")).strip() or _CATEGORY_TITLES[category],
                category=category,
                focus=str(g.get("focus", "")).strip(),
                steps=steps,
            )
        )

    if not groups:
        return None
    summary = str(data.get("summary", "")).strip() or "Here's where things stand and what I'd do next."
    return OperatorPlan(summary=summary, plans=groups, context_digest=ctx, generated_by="llm")


def _clamp_level(v: Any) -> str:
    s = str(v or "").strip().lower()
    return s if s in ("low", "medium", "high") else "medium"


def _clamp_category(v: Any) -> str:
    s = str(v or "").strip().lower()
    return s if s in _CATEGORIES else "projects"


def _extract_json(raw: str) -> dict | None:
    """Tolerate ```json fences / leading prose: grab the first {...} block."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ── deterministic fallback ───────────────────────────────────────────────────


def _fallback_plan(ctx: dict) -> OperatorPlan:
    """Build a usable grouped plan straight from gathered signals — no LLM."""
    groups: list[PlanGroup] = []

    def _mk_group(category: str, focus: str, raw_steps: list[dict]) -> None:
        steps: list[PlanStep] = []
        for s in raw_steps:
            steps.append(
                PlanStep(
                    id=f"{category}-{len(steps) + 1}-{len(groups)}",
                    title=s["title"],
                    rationale=s.get("rationale", ""),
                    instructions=s.get("instructions", s.get("rationale", "")),
                    effort=s.get("effort", "medium"),
                    impact=s.get("impact", "medium"),
                    source_ref=s.get("source_ref", ""),
                )
            )
        if steps:
            groups.append(
                PlanGroup(
                    id=f"{category}-{len(groups)}",
                    title=_CATEGORY_TITLES[category],
                    category=category,
                    focus=focus,
                    steps=steps,
                )
            )

    # Projects — current best next action
    na = ctx.get("next_action") or {}
    if na.get("next_task"):
        pct = na.get("completion_pct") or 0.0
        _mk_group(
            "projects",
            f"{na.get('space', 'current work')} is your lowest-completion space ({pct:.0f}%).",
            [{
                "title": f"Continue {na.get('space', 'current work')}: {na['next_task']}",
                "rationale": "Finishing the lowest-completion space unblocks the most.",
                "impact": "high",
                "source_ref": f"space:{na.get('space', '')}",
            }],
        )

    # Finance — overdue invoices + cash hygiene
    fin = ctx.get("finance") or {}
    fin_steps: list[dict] = []
    if fin.get("overdue_invoices"):
        fin_steps.append({
            "title": f"Chase {fin['overdue_invoices']} overdue invoice(s)",
            "rationale": "Overdue invoices directly affect cash and runway.",
            "impact": "high", "source_ref": "finance",
        })
    fin_steps.append({
        "title": "Review spending leaks and cut one recurring drain",
        "rationale": "Open Finance → Leaks to see categories and recurring charges.",
        "effort": "low", "source_ref": "finance",
    })
    fin_steps.append({
        "title": "Set your real cash balance",
        "rationale": "Reconcile your bank/cash so the dashboard reflects real money.",
        "effort": "low", "source_ref": "finance",
    })
    _mk_group("finance", "Keep cash and runway honest.", fin_steps)

    # Inbox / Growth
    inbox = ctx.get("inbox") or {}
    growth_steps: list[dict] = []
    if inbox.get("pending"):
        growth_steps.append({
            "title": f"Triage {inbox['pending']} inbox document(s)",
            "rationale": "Pending docs are unrouted; clearing them keeps signal flowing.",
            "effort": "low", "source_ref": "inbox",
        })
    growth_steps.append({
        "title": "Pick one new revenue experiment to try this week",
        "rationale": "Diversify income beyond the current top seller.",
        "source_ref": "growth",
    })
    _mk_group("growth", "Compound small bets into new income.", growth_steps)

    # Life — wellbeing default
    _mk_group(
        "life",
        "Energy and focus compound everything else.",
        [{
            "title": "Schedule a daily 20-minute walk or workout",
            "rationale": "Consistent movement protects focus and decision quality.",
            "effort": "low",
        }],
    )

    if not groups:
        _mk_group(
            "projects",
            "No high-signal action detected.",
            [{
                "title": "Review your spaces and pick a focus for today",
                "rationale": "A quick manual review sets direction.",
            }],
        )

    return OperatorPlan(
        summary="Here's where things stand across your business and life, and what I'd do next.",
        plans=groups,
        context_digest=ctx,
        generated_by="fallback",
    )
