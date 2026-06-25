"""
Board routes for the Deck API — the NAVIG Tasks "pipeline chain" Kanban.

Backed by :class:`navig.store.board.BoardStore` (``~/.navig/data/store/board.db``).
Exposes goals, cards, the dependency chain, subtasks, AI task-generation,
AI mission execution (draft / approval-gated / autonomous), and an AI briefing.

Routes (registered in navig/gateway/deck/__init__.py, prefix /api/deck/board):
    GET    /                          → full board snapshot
    POST   /goals                     → create goal
    PATCH  /goals/{id}                → update goal
    DELETE /goals/{id}                → delete goal
    POST   /goals/{id}/generate       → AI: candidate tasks for a goal (not persisted)
    POST   /cards                     → create one card, or many (with optional chain)
    PATCH  /cards/{id}                → update card properties
    DELETE /cards/{id}                → delete card
    POST   /cards/{id}/deps           → add a dependency edge (cycle-checked)
    DELETE /cards/{id}/deps/{dep}     → remove a dependency edge
    POST   /cards/{id}/move           → change stage / order (drag-drop); cascades on done
    POST   /cards/{id}/run            → run AI mission per ai_mode
    POST   /cards/{id}/approve        → approve a parked mission → done (+ cascade)
    POST   /cards/{id}/reject         → reject a parked mission → back to in_progress
    POST   /cards/{id}/subtasks       → add subtask
    PATCH  /subtasks/{id}             → update subtask
    DELETE /subtasks/{id}             → delete subtask
    GET    /briefing                  → AI briefing across all cards/goals
    GET    /settings                  → board settings
    POST   /settings                  → update board settings
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Cap on how many cards a single done-cascade may auto-fire (loop backstop).
_MAX_CASCADE = 25


# ── Helpers ──────────────────────────────────────────────────────────────────

def _store():
    from navig.store.board import get_board_store
    return get_board_store()


async def _in_executor(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# Bounded execution for the LLM-heavy card runs. When the gateway's
# MissionExecutor is live we funnel through ITS shared semaphore (one execution
# path for board + missions); otherwise a local fallback bound prevents the
# previously-unbounded default thread pool from saturating under a cascade.
_FALLBACK_CARD_SEM = asyncio.Semaphore(3)


def _card_summary(card: dict[str, Any]) -> str:
    """Short text for the mission record — the card's structured result, trimmed."""
    try:
        ar = card.get("agent_result")
        if ar:
            txt = (json.loads(ar) or {}).get("result") or ""
            return (txt[:280] + "…") if len(txt) > 280 else txt
    except Exception:  # noqa: BLE001
        pass
    return card.get("title", "")


async def _run_card(gateway, card: dict[str, Any], mode: str, actor: str) -> dict[str, Any]:
    """Run a card's AI mission. When the MissionExecutor is live, the run is
    recorded as a TRACKED Mission (receipt + mission_state stream) so board
    automation shows up live in the Activity/Missions cockpit — the board's own
    structured result, lane moves, and board_update SSE are unchanged."""
    ex = getattr(gateway, "mission_executor", None)
    if ex is not None and hasattr(ex, "run_tracked"):
        result = await ex.run_tracked(
            title=f"Run task: {card.get('title', 'task')}",
            capability="board_card",
            fn=_run_card_sync,
            args=(card, mode, actor),
            metadata={
                "card_id": card.get("id"),
                "goal_id": card.get("goal_id"),
                "mode": mode,
                "actor": actor,
            },
            ok=lambda r: bool(r) and r.get("agent_status") != "failed",
            summary=_card_summary,
        )
    else:
        # Fallback when no executor (e.g. tests): still bound the pool.
        async with _FALLBACK_CARD_SEM:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: _run_card_sync(card, mode, actor))

    # When a card parks awaiting approval, also surface it as an Inbox ask so the
    # decision reaches the user wherever they are (not only the board Agent lane).
    if result and result.get("agent_status") == "awaiting_approval":
        await _surface_card_approval_ask(gateway, result)
    return result


async def _surface_card_approval_ask(gateway, card: dict[str, Any]) -> None:
    """Create an Inbox question whose answer approves (→ done + cascade) or
    redoes (→ in_progress) a parked board card. Replaces any prior ask for the
    same card so re-runs don't stack."""
    reg = getattr(gateway, "request_registry", None)
    if reg is None or not hasattr(reg, "create"):
        return
    card_id = card["id"]

    body = (card.get("notes") or "").strip()
    try:
        data = json.loads(card.get("agent_result") or "{}")
        body = (data.get("result") or body or "").strip()
    except Exception:
        pass
    if len(body) > 400:
        body = body[:400].rstrip() + "…"

    async def _on_answer(answer: dict) -> None:
        choice = answer.get("choice")
        if isinstance(choice, list):
            choice = choice[0] if choice else None
        approved = bool(answer.get("auto")) or choice == "approve"
        store = _store()
        try:
            if approved:
                await _in_executor(store.update_card, card_id, {"agent_status": "done"})
                await _in_executor(store.move_card, card_id, "done", actor="user")
                await _cascade(card_id, actor="agent", gateway=gateway)
            else:
                await _in_executor(store.update_card, card_id, {"agent_status": "idle"})
                await _in_executor(store.move_card, card_id, "in_progress", actor="user")
            queue = getattr(gateway, "system_events", None)
            if queue and hasattr(queue, "emit"):
                await queue.emit("board_update", {"kind": "card_approval_answered", "id": card_id})
        except Exception:
            logger.exception("card approval ask answer failed for %s", card_id)

    try:
        if hasattr(reg, "replace_pending_by_source"):
            await reg.replace_pending_by_source(f"card:{card_id}")
        await reg.create(
            kind="question",
            title=f"Approve: {card.get('title', 'task')}",
            body=body or "navig finished this step and is waiting for your go-ahead.",
            options=[{"id": "approve", "label": "Approve"}, {"id": "reject", "label": "Redo"}],
            allow_custom=False,
            source=f"card:{card_id}",
            priority="normal",
            on_answer=_on_answer,
        )
    except Exception:
        logger.debug("failed to surface card approval ask", exc_info=True)


def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _emit_update(request: "web.Request", payload: dict | None = None) -> None:
    """Broadcast a board_update SSE event so the Deck refreshes live."""
    try:
        gateway = request.app.get("gateway") if hasattr(request, "app") else None
        queue = getattr(gateway, "system_events", None) if gateway else None
        if queue and hasattr(queue, "emit"):
            await queue.emit("board_update", payload or {})
    except Exception as exc:  # never fail a mutation because of telemetry
        logger.debug("board_update emit failed: %s", exc)


async def _json(request: "web.Request") -> dict | None:
    try:
        return await request.json()
    except Exception:
        return None


# ── Snapshot ─────────────────────────────────────────────────────────────────

async def handle_board_get(request: "web.Request") -> "web.Response":
    try:
        snap = await _in_executor(_store().snapshot)
        return _ok(snap)
    except Exception as exc:
        logger.exception("board snapshot failed")
        return _err(str(exc), 500)


# ── Goals ────────────────────────────────────────────────────────────────────

async def handle_board_goal_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title is required")
    goal = await _in_executor(
        _store().create_goal,
        title,
        description=(body.get("description") or "").strip(),
        color=body.get("color") or "#6366f1",
        space=body.get("space") or None,
    )
    await _emit_update(request, {"kind": "goal_created", "id": goal["id"]})
    return _ok(goal, 201)


async def handle_board_goal_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    goal_id = request.match_info["id"]
    goal = await _in_executor(_store().update_goal, goal_id, body)
    if goal is None:
        return _err("Goal not found", 404)
    await _emit_update(request, {"kind": "goal_updated", "id": goal_id})
    return _ok(goal)


async def handle_board_goal_delete(request: "web.Request") -> "web.Response":
    goal_id = request.match_info["id"]
    await _in_executor(_store().delete_goal, goal_id)
    await _emit_update(request, {"kind": "goal_deleted", "id": goal_id})
    return _ok({"deleted": goal_id})


# ── AI task generation ───────────────────────────────────────────────────────

def _space_context(space: str | None) -> str:
    """Short context block from a linked space's VISION/ROADMAP, if any."""
    if not space:
        return ""
    try:
        from navig.gateway.deck.routes.apps import (
            _get_spaces_dir,
            _read_file_content,
        )
        sdir = _get_spaces_dir() / space
        vision = _read_file_content(sdir / "VISION.md", 800)
        roadmap = _read_file_content(sdir / "ROADMAP.md", 800)
        parts = []
        if vision.strip():
            parts.append(f"Space vision:\n{vision.strip()}")
        if roadmap.strip():
            parts.append(f"Space roadmap:\n{roadmap.strip()}")
        return "\n\n".join(parts)
    except Exception:
        return ""


def _parse_task_list(raw: str) -> list[dict[str, Any]]:
    """Robustly parse an LLM response into [{title, notes, priority}]."""
    text = (raw or "").strip()
    # Strip code fences.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Try JSON first.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("tasks") or data.get("items") or []
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, str):
                    out.append({"title": item.strip(), "notes": "", "priority": "normal"})
                elif isinstance(item, dict) and item.get("title"):
                    pr = str(item.get("priority", "normal")).lower()
                    if pr not in {"low", "normal", "high", "urgent"}:
                        pr = "normal"
                    am = str(item.get("ai_mode", "inherit")).lower()
                    if am not in {"inherit", "draft", "approval", "auto"}:
                        am = "inherit"
                    out.append({
                        "title": str(item["title"]).strip(),
                        "notes": str(item.get("notes", "")).strip(),
                        "priority": pr,
                        "ai_mode": am,
                    })
            if out:
                return out
    except Exception:
        pass
    # Fallback: split lines, strip bullets/numbering.
    out = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"^[\-\*\d\.\)\]\s]+", "", line).strip()
        if line and len(line) > 2:
            out.append({"title": line[:200], "notes": "", "priority": "normal"})
    return out[:15]


def _generate_tasks_sync(title: str, description: str, space: str | None) -> list[dict[str, Any]]:
    from navig.llm_generate import llm_generate
    ctx = _space_context(space)
    sys = (
        "You are a planning assistant. Break a high-level goal into a short, "
        "ordered list of concrete, actionable tasks (5-10). Each task is one "
        "step toward finishing the goal. Respond with ONLY a JSON array of "
        'objects: [{"title": str, "notes": str, "priority": "low|normal|high|urgent", '
        '"ai_mode": "inherit|approval"}]. '
        'Set "ai_mode":"approval" on any task that is a real human decision '
        "(spends money, contacts a person, or is hard to reverse) so it pauses "
        'for confirmation; otherwise use "inherit". '
        "Order them so each builds on the previous (a pipeline)."
    )
    user = f"Goal: {title}"
    if description:
        user += f"\nDetails: {description}"
    if ctx:
        user += f"\n\nContext:\n{ctx}"
    raw = llm_generate(
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        mode="planning",
        temperature=0.4,
        max_tokens=900,
    )
    return _parse_task_list(raw)


async def handle_board_goal_generate(request: "web.Request") -> "web.Response":
    goal_id = request.match_info["id"]
    goal = await _in_executor(_store().get_goal, goal_id)
    if goal is None:
        return _err("Goal not found", 404)
    try:
        tasks = await _in_executor(
            _generate_tasks_sync,
            goal["title"],
            goal.get("description") or "",
            goal.get("space"),
        )
    except Exception as exc:
        logger.warning("task generation failed: %s", exc)
        return _err(f"AI task generation failed: {exc}", 502)
    return _ok({"goal_id": goal_id, "tasks": tasks})


async def _run_goal_cascade(gateway, store, goal_id: str) -> list[dict]:
    """Run every ready (unblocked) task in a goal as a mission, cascading until
    none remain ready (auto-mode tasks complete and unlock dependents;
    approval/draft tasks run once and pause the chain). Shared by the goal-run
    endpoint and the plan-step decompose-and-execute flow."""
    triggered: list[dict] = []
    seen: set[str] = set()
    while len(triggered) < _MAX_CASCADE:
        cards = await _in_executor(store.list_cards)
        ready = [
            c for c in cards
            if c.get("goal_id") == goal_id
            and c["stage"] != "done"
            and c["gate"] == "ready"
            and c.get("agent_status") not in ("running", "awaiting_approval")
            and c["id"] not in seen
        ]
        if not ready:
            break
        for c in ready:
            if len(triggered) >= _MAX_CASCADE:
                break
            seen.add(c["id"])
            mode = store.resolve_ai_mode(c.get("ai_mode"))
            ran = await _run_card(gateway, c, mode, "agent")
            triggered.append(ran)
    return triggered


async def handle_board_goal_run(request: "web.Request") -> "web.Response":
    """Automate a goal: repeatedly run every ready (unblocked) task in the goal
    as a mission until none remain ready (auto-mode tasks complete and unlock
    their dependents; approval/draft tasks run once and pause the chain)."""
    goal_id = request.match_info["id"]
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    store = _store()
    goal = await _in_executor(store.get_goal, goal_id)
    if goal is None:
        return _err("Goal not found", 404)

    triggered = await _run_goal_cascade(gateway, store, goal_id)
    await _emit_update(request, {"kind": "goal_ran", "id": goal_id, "count": len(triggered)})
    return _ok({"goal_id": goal_id, "triggered": triggered})


# ── Cards ────────────────────────────────────────────────────────────────────

def _card_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal_id": spec.get("goal_id"),
        "notes": (spec.get("notes") or ""),
        "stage": spec.get("stage") or "backlog",
        "priority": spec.get("priority") or "normal",
        "due_at": spec.get("due_at"),
        "ai_mode": spec.get("ai_mode") or "inherit",
        "auto_advance": bool(spec.get("auto_advance")),
    }


async def handle_board_card_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")

    # Bulk: {cards: [...], chain?: bool}  or a raw list.
    cards_in = None
    chain = False
    if isinstance(body, list):
        cards_in = body
    elif isinstance(body.get("cards"), list):
        cards_in = body["cards"]
        chain = bool(body.get("chain"))

    store = _store()
    if cards_in is not None:
        specs = []
        for c in cards_in:
            if not isinstance(c, dict) or not (c.get("title") or "").strip():
                continue
            specs.append({"title": c["title"].strip(), **_card_kwargs(c)})
        if not specs:
            return _err("no valid cards")
        if chain:
            created = await _in_executor(store.create_chain, specs)
        else:
            created = []
            for s in specs:
                created.append(await _in_executor(store.create_card, s.pop("title"), **s))
        await _emit_update(request, {"kind": "cards_created", "count": len(created)})
        return _ok({"cards": created}, 201)

    # Single card.
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title is required")
    card = await _in_executor(store.create_card, title, **_card_kwargs(body))
    await _emit_update(request, {"kind": "card_created", "id": card["id"]})
    return _ok(card, 201)


async def handle_board_card_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    card_id = request.match_info["id"]
    card = await _in_executor(_store().update_card, card_id, body)
    if card is None:
        return _err("Card not found", 404)
    await _emit_update(request, {"kind": "card_updated", "id": card_id})
    return _ok(card)


async def handle_board_card_delete(request: "web.Request") -> "web.Response":
    card_id = request.match_info["id"]
    await _in_executor(_store().delete_card, card_id)
    await _emit_update(request, {"kind": "card_deleted", "id": card_id})
    return _ok({"deleted": card_id})


async def handle_board_card_move(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    card_id = request.match_info["id"]
    to_stage = body.get("stage")
    if not to_stage:
        return _err("stage is required")
    sort_order = body.get("sort_order")
    card = await _in_executor(
        _store().move_card, card_id, to_stage, sort_order=sort_order, actor="user"
    )
    if card is None:
        return _err("Card not found", 404)

    triggered: list[dict] = []
    if to_stage == "done":
        triggered = await _cascade(card_id, actor="user")

    await _emit_update(request, {"kind": "card_moved", "id": card_id, "stage": to_stage})
    return _ok({"card": card, "triggered": triggered})


# ── Dependencies ─────────────────────────────────────────────────────────────

async def handle_board_dep_add(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    card_id = request.match_info["id"]
    depends_on_id = body.get("depends_on_id")
    if not depends_on_id:
        return _err("depends_on_id is required")
    ok = await _in_executor(_store().add_dependency, card_id, depends_on_id)
    if not ok:
        return _err("Invalid dependency (self-reference or would create a cycle)", 409)
    card = await _in_executor(_store().get_card, card_id)
    await _emit_update(request, {"kind": "dep_added", "id": card_id})
    return _ok(card)


async def handle_board_dep_remove(request: "web.Request") -> "web.Response":
    card_id = request.match_info["id"]
    dep = request.match_info["dep"]
    await _in_executor(_store().remove_dependency, card_id, dep)
    card = await _in_executor(_store().get_card, card_id)
    await _emit_update(request, {"kind": "dep_removed", "id": card_id})
    return _ok(card)


# ── AI mission execution ─────────────────────────────────────────────────────

def _card_context(card: dict[str, Any]) -> str:
    """Describe the parent goal + sibling tasks so the model can reason about
    how this task relates to the rest of the pipeline."""
    store = _store()
    parts: list[str] = []
    goal = store.get_goal(card["goal_id"]) if card.get("goal_id") else None
    if goal:
        parts.append(f"Parent goal: {goal['title']}")
        if goal.get("description"):
            parts.append(f"Goal details: {goal['description']}")
        sctx = _space_context(goal.get("space"))
        if sctx:
            parts.append(sctx)
        siblings = [
            c for c in store.list_cards()
            if c.get("goal_id") == goal["id"] and c["id"] != card["id"]
        ]
        if siblings:
            parts.append("Other tasks in this goal:")
            for s in siblings[:12]:
                parts.append(f"- [{s['stage']}] {s['title']}")
    return "\n".join(parts)


def _research(query: str, max_results: int = 4) -> tuple[str, list[str]]:
    """Best-effort web search to ground a mission in real facts.

    Returns (context_text, source_urls). Degrades to ("", []) when no search
    provider / network is available (DuckDuckGo is the keyless default)."""
    try:
        from navig.tools.web import web_search, web_fetch
    except Exception:
        return "", []
    lines: list[str] = []
    sources: list[str] = []
    try:
        res = web_search(query, count=max_results)
        if not getattr(res, "success", False) or not res.results:
            return "", []
        for r in res.results[:max_results]:
            lines.append(f"- {r.title} — {r.snippet} ({r.url})")
            if r.url:
                sources.append(r.url)
        # Pull the top result for richer detail.
        top = res.results[0]
        if top.url:
            try:
                f = web_fetch(top.url, max_chars=2500)
                if getattr(f, "success", False) and f.text:
                    lines.append("")
                    lines.append(f"Excerpt from {top.url}:")
                    lines.append(f.text[:2500])
            except Exception:
                pass
    except Exception as exc:
        logger.debug("research failed: %s", exc)
        return "", []
    return "\n".join(lines), sources


def _parse_result(raw: str, fallback_sources: list[str]) -> dict[str, Any]:
    """Parse the mission LLM output into a structured result. Robust to code
    fences / surrounding prose; falls back to treating the whole text as the
    deliverable."""
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        b, e = text.find("{"), text.rfind("}")
        if b != -1 and e > b:
            try:
                data = json.loads(text[b:e + 1])
            except Exception:
                data = None
    if not isinstance(data, dict) or "result" not in data:
        return {"result": (raw or "").strip(), "highlights": [], "next_task": None,
                "relation": "", "sources": fallback_sources}
    hl = data.get("highlights") or []
    if not isinstance(hl, list):
        hl = [str(hl)]
    nt = data.get("next_task")
    if isinstance(nt, dict) and (nt.get("title") or "").strip():
        nt = {"title": str(nt["title"]).strip(), "notes": str(nt.get("notes", "")).strip()}
    else:
        nt = None
    src = data.get("sources")
    if not isinstance(src, list):
        src = []
    merged = list(dict.fromkeys([*[str(s) for s in src], *fallback_sources]))
    return {
        "result": str(data.get("result", "")).strip(),
        "highlights": [str(x).strip() for x in hl if str(x).strip()][:6],
        "next_task": nt,
        "relation": str(data.get("relation", "")).strip(),
        "sources": merged[:8],
    }


def _run_card_sync(card: dict[str, Any], mode: str, actor: str = "user") -> dict[str, Any]:
    """Execute the AI mission for a card and store a STRUCTURED, web-grounded
    result (JSON in agent_result). Returns the updated card.

    draft    → attach the result, card stays put.
    approval → run, park in 'agent' lane awaiting approval.
    auto     → run, move to 'done'.
    """
    from navig.llm_generate import llm_generate
    store = _store()

    # Mark running.
    store.update_card(card["id"], {"agent_status": "running"})

    context = _card_context(card)
    research_text, sources = _research(card["title"])

    sys = (
        "You are NAVIG, an autonomous operator. You COMPLETE the task and report "
        "the finished deliverable — never a description of the steps you would take. "
        "Use the research provided to give concrete specifics: real names, numbers, "
        "prices, dates, and links. If a fact isn't supported by the research, say so "
        "plainly and do NOT invent sources. Respond with ONLY a JSON object:\n"
        '{\n'
        '  "result": "<the concrete deliverable in markdown — the actual answer/output, specific>",\n'
        '  "highlights": ["<3-5 of the most important facts/numbers>"],\n'
        '  "next_task": {"title": "<a concrete, useful follow-up task toward the goal>", "notes": "<why it matters / what to do>"},\n'
        '  "relation": "<1-2 sentences: how this task connects to the parent goal and the other tasks>",\n'
        '  "sources": ["<url>", ...]\n'
        '}'
    )
    user_parts = [f"Task: {card['title']}"]
    if card.get("notes"):
        user_parts.append(f"Task notes: {card['notes']}")
    if context:
        user_parts.append("\nContext:\n" + context)
    if research_text:
        user_parts.append("\nResearch (use these real facts and cite the URLs):\n" + research_text)
    else:
        user_parts.append("\n(No web research was available — answer from your own knowledge and set sources to [].)")
    user = "\n".join(user_parts)

    try:
        raw = llm_generate(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            mode="research" if research_text else "planning",
            temperature=0.4,
            max_tokens=1500,
        )
    except Exception as exc:
        logger.warning("mission run failed for %s: %s", card["id"], exc)
        store.update_card(card["id"], {
            "agent_status": "failed",
            "agent_result": json.dumps({"result": f"Run failed: {exc}", "highlights": [],
                                        "next_task": None, "relation": "", "sources": []}),
        })
        return store.get_card(card["id"])  # type: ignore[return-value]

    payload = _parse_result(raw, sources)
    result_json = json.dumps(payload, ensure_ascii=False)

    if mode == "draft":
        store.update_card(card["id"], {"agent_status": "idle", "agent_result": result_json})
    elif mode == "auto":
        store.update_card(card["id"], {"agent_status": "done", "agent_result": result_json})
        store.move_card(card["id"], "done", actor="agent")
    else:  # approval (default)
        store.update_card(card["id"], {"agent_status": "awaiting_approval", "agent_result": result_json})
        store.move_card(card["id"], "agent", actor="agent")

    return store.get_card(card["id"])  # type: ignore[return-value]


async def _cascade(done_card_id: str, actor: str = "user", gateway=None) -> list[dict[str, Any]]:
    """After a card is done, unlock dependents and auto-run those flagged
    ``auto_advance``. Iterative with a seen-set + hard cap to bound work."""
    store = _store()
    triggered: list[dict[str, Any]] = []
    worklist = [done_card_id]
    seen: set[str] = set()
    while worklist and len(triggered) < _MAX_CASCADE:
        cur = worklist.pop()
        unlocked = await _in_executor(store.unlock_after_done, cur)
        for dep_card in unlocked:
            if dep_card["id"] in seen:
                continue
            seen.add(dep_card["id"])
            if not dep_card.get("auto_advance"):
                continue
            mode = store.resolve_ai_mode(dep_card.get("ai_mode"))
            ran = await _run_card(gateway, dep_card, mode, "agent")
            triggered.append(ran)
            if ran and ran.get("stage") == "done":
                worklist.append(ran["id"])
    return triggered


async def handle_board_card_run(request: "web.Request") -> "web.Response":
    card_id = request.match_info["id"]
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    store = _store()
    card = await _in_executor(store.get_card, card_id)
    if card is None:
        return _err("Card not found", 404)
    if card["gate"] == "waiting":
        return _err("Card is waiting on unfinished dependencies", 409)

    mode = store.resolve_ai_mode(card.get("ai_mode"))
    ran = await _run_card(gateway, card, mode, "user")
    triggered: list[dict] = []
    if ran and ran.get("stage") == "done":
        triggered = await _cascade(card_id, actor="agent", gateway=gateway)

    await _emit_update(request, {"kind": "card_ran", "id": card_id, "mode": mode})
    return _ok({"card": ran, "mode": mode, "triggered": triggered})


async def handle_board_card_approve(request: "web.Request") -> "web.Response":
    card_id = request.match_info["id"]
    store = _store()
    card = await _in_executor(store.get_card, card_id)
    if card is None:
        return _err("Card not found", 404)
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    await _in_executor(store.update_card, card_id, {"agent_status": "done"})
    moved = await _in_executor(store.move_card, card_id, "done", actor="user")
    triggered = await _cascade(card_id, actor="agent", gateway=gateway)
    await _emit_update(request, {"kind": "card_approved", "id": card_id})
    return _ok({"card": moved, "triggered": triggered})


async def handle_board_card_reject(request: "web.Request") -> "web.Response":
    card_id = request.match_info["id"]
    store = _store()
    card = await _in_executor(store.get_card, card_id)
    if card is None:
        return _err("Card not found", 404)
    await _in_executor(store.update_card, card_id, {"agent_status": "idle"})
    moved = await _in_executor(store.move_card, card_id, "in_progress", actor="user")
    await _emit_update(request, {"kind": "card_rejected", "id": card_id})
    return _ok({"card": moved})


# ── Subtasks ─────────────────────────────────────────────────────────────────

async def handle_board_subtask_add(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    card_id = request.match_info["id"]
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title is required")
    sub = await _in_executor(_store().add_subtask, card_id, title)
    await _emit_update(request, {"kind": "subtask_added", "card_id": card_id})
    return _ok(sub, 201)


async def handle_board_subtask_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    sid = request.match_info["id"]
    await _in_executor(_store().update_subtask, sid, body)
    await _emit_update(request, {"kind": "subtask_updated", "id": sid})
    return _ok({"id": sid})


async def handle_board_subtask_delete(request: "web.Request") -> "web.Response":
    sid = request.match_info["id"]
    await _in_executor(_store().delete_subtask, sid)
    await _emit_update(request, {"kind": "subtask_deleted", "id": sid})
    return _ok({"deleted": sid})


# ── Briefing ─────────────────────────────────────────────────────────────────

def _build_briefing_sync() -> str:
    from navig.llm_generate import llm_generate
    store = _store()
    goals = store.list_goals()
    cards = store.list_cards()
    history = store.recent_history(20)

    by_goal: dict[str, dict] = {g["id"]: {"title": g["title"], "done": 0, "total": 0}
                                for g in goals}
    in_progress, agent_cards, upcoming = [], [], []
    for c in cards:
        gid = c.get("goal_id")
        if gid in by_goal:
            by_goal[gid]["total"] += 1
            if c["stage"] == "done":
                by_goal[gid]["done"] += 1
        if c["stage"] == "in_progress":
            in_progress.append(c["title"])
        if c["stage"] == "agent" or c.get("agent_status") == "awaiting_approval":
            agent_cards.append(c["title"])
        if c.get("due_at") and c["stage"] != "done":
            upcoming.append((c["due_at"], c["title"]))
    upcoming.sort()

    facts = ["Board state:"]
    for g in by_goal.values():
        if g["total"]:
            pct = int(g["done"] / g["total"] * 100)
            facts.append(f"- Goal '{g['title']}': {g['done']}/{g['total']} done ({pct}%)")
    if in_progress:
        facts.append(f"- In progress: {', '.join(in_progress[:8])}")
    if agent_cards:
        facts.append(f"- Awaiting your approval: {', '.join(agent_cards[:8])}")
    if upcoming:
        facts.append("- Soonest due: " + "; ".join(f"{t} ({d})" for d, t in upcoming[:5]))
    if history:
        facts.append(f"- Recent activity: {len(history)} stage changes")

    # Fold in linked spaces' progress.
    try:
        from navig.spaces.briefing import build_spaces_briefing_lines
        facts.extend(build_spaces_briefing_lines())
    except Exception:
        pass

    context = "\n".join(facts)
    sys = (
        "You are NAVIG. Write a short, energizing daily briefing (3-5 sentences) "
        "from the board state. Highlight progress, the single most important next "
        "action, and anything awaiting approval. Be concrete and motivating."
    )
    try:
        return llm_generate(
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": context}],
            mode="chat",
            temperature=0.6,
            max_tokens=400,
        ).strip()
    except Exception as exc:
        logger.debug("briefing LLM failed, returning raw facts: %s", exc)
        return context


async def handle_board_briefing(request: "web.Request") -> "web.Response":
    try:
        text = await _in_executor(_build_briefing_sync)
        return _ok({"briefing": text})
    except Exception as exc:
        logger.exception("briefing failed")
        return _err(str(exc), 500)


# ── Settings ─────────────────────────────────────────────────────────────────

async def handle_board_settings_get(request: "web.Request") -> "web.Response":
    settings = await _in_executor(_store().get_settings)
    return _ok(settings)


async def handle_board_settings_post(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    settings = await _in_executor(_store().save_settings, body)
    await _emit_update(request, {"kind": "settings_updated"})
    return _ok(settings)
