"""Plan-step execution — turn one operator plan step into a tracked task plan.

Flow (RUN wizard → "Run this" on a step):
  1. POST /requests/plan-step/refine  → propose 2-3 approach variants (or one
     "proceed directly" fallback) the user picks from (or writes their own).
  2. POST /requests/plan-step/execute → decompose the chosen approach into an
     ordered board card chain (a goal + sub-tasks), then run it. Cards the LLM
     flags as real decisions (ai_mode="approval") pause the chain for the user.

Reuses the board engine wholesale (decomposition, card chain, run cascade) so
there's no new execution infra — just orchestration.
"""

from __future__ import annotations

import json
import logging
import re

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.routes.board import (
    _emit_update,
    _err,
    _generate_tasks_sync,
    _in_executor,
    _json,
    _ok,
    _run_goal_cascade,
    _store,
)

logger = logging.getLogger(__name__)


def _extract_json_array(raw: str) -> list | None:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("variants") or data.get("approaches") or data.get("items") or []
        return data if isinstance(data, list) else None
    except Exception:
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, list) else None
        except Exception:
            return None
    return None


def _approach_variants_sync(title: str, rationale: str) -> list[dict]:
    """Propose 2-3 distinct approaches for a goal. Always returns ≥1 (a
    'proceed directly' fallback) so the wizard never dead-ends."""
    fallback = [{"id": "approach-1", "title": "Proceed directly",
                 "summary": "Break it down and start now.", "effort": "medium", "impact": "medium"}]
    try:
        from navig.llm_generate import llm_generate

        sys = (
            "You are NAVIG's operator. Given a goal, propose 2-3 DISTINCT, concrete "
            "approaches to accomplish it (e.g. a fast/lean one and a thorough one). "
            "Respond with ONLY a JSON array: "
            '[{"title": str, "summary": "<=1 sentence", "effort": "low|medium|high", '
            '"impact": "low|medium|high"}].'
        )
        user = f"Goal: {title}"
        if rationale:
            user += f"\nWhy it matters: {rationale}"
        raw = llm_generate(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            mode="planning", temperature=0.5, max_tokens=600,
        )
    except Exception:
        logger.debug("approach variants LLM failed", exc_info=True)
        return fallback

    data = _extract_json_array(raw)
    if not data:
        return fallback
    out: list[dict] = []

    def _lvl(v):
        s = str(v or "").strip().lower()
        return s if s in ("low", "medium", "high") else "medium"

    for i, item in enumerate(data[:3]):
        if not isinstance(item, dict):
            continue
        t = str(item.get("title", "")).strip()
        if not t:
            continue
        out.append({
            "id": f"approach-{len(out) + 1}",
            "title": t,
            "summary": str(item.get("summary", "")).strip(),
            "effort": _lvl(item.get("effort")),
            "impact": _lvl(item.get("impact")),
        })
    return out or fallback


async def handle_plan_step_refine(request: "web.Request") -> "web.Response":
    """Propose approach variants for a chosen plan step."""
    body = await _json(request) or {}
    title = str(body.get("title", "")).strip()
    if not title:
        return _err("title is required", 400)
    rationale = str(body.get("rationale", "")).strip()
    try:
        variants = await _in_executor(_approach_variants_sync, title, rationale)
        return _ok({"variants": variants})
    except Exception as exc:
        logger.exception("plan-step refine failed")
        return _err(str(exc), 500)


async def handle_plan_step_execute(request: "web.Request") -> "web.Response":
    """Decompose the chosen approach into a board goal + card chain and run it."""
    body = await _json(request) or {}
    title = str(body.get("title", "")).strip()
    if not title:
        return _err("title is required", 400)
    approach = str(body.get("approach", "")).strip()
    rationale = str(body.get("rationale", "")).strip()
    space = body.get("space") or None
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    store = _store()

    try:
        goal = await _in_executor(
            store.create_goal, title, description=(approach or rationale), space=space
        )
        # Decompose into ordered sub-tasks (LLM; fall back to a single task).
        try:
            tasks = await _in_executor(_generate_tasks_sync, title, approach or rationale, space)
        except Exception:
            logger.debug("plan-step decomposition failed; using single task", exc_info=True)
            tasks = []
        if not tasks:
            tasks = [{"title": title, "notes": approach or rationale, "priority": "normal",
                      "ai_mode": "inherit"}]

        specs = [{
            "title": t.get("title", "Task"),
            "notes": t.get("notes", ""),
            "priority": t.get("priority", "normal"),
            "ai_mode": t.get("ai_mode", "inherit"),
            "goal_id": goal["id"],
        } for t in tasks]
        cards = await _in_executor(store.create_chain, specs)
        await _emit_update(request, {"kind": "goal_created", "id": goal["id"], "count": len(cards)})

        triggered = await _run_goal_cascade(gateway, store, goal["id"])
        await _emit_update(request, {"kind": "goal_ran", "id": goal["id"], "count": len(triggered)})
        return _ok({"goal_id": goal["id"], "cards": cards, "triggered": triggered})
    except Exception as exc:
        logger.exception("plan-step execute failed")
        return _err(str(exc), 500)
