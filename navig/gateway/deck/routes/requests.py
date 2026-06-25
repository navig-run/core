"""Unified "asks" surface for the Deck API.

Merges two producers into one stream the deck renders as approval cards /
question cards:

    GET  /api/deck/requests              → pending approvals + questions
    POST /api/deck/requests/{id}/respond → resolve one (approve / answer)
    POST /api/deck/requests/next-action  → "ask navig for its next action"

Both producers live on the gateway: ``approval_manager`` (yes/no on commands)
and ``request_registry`` (questions / proposals). Registered in
``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _gateway(request: "web.Request"):
    return request.app.get("gateway") if hasattr(request, "app") else None


def _approval_to_unified(req: dict) -> dict:
    """Map an ApprovalRequest.to_dict() to the unified request shape."""
    level = req.get("level")
    return {
        "id": req.get("id"),
        "kind": "approval",
        "title": req.get("description") or f"Approve: {req.get('command', '')}",
        "body": req.get("command", ""),
        "options": [
            {"id": "approve", "label": "Approve"},
            {"id": "deny", "label": "Deny"},
        ],
        "allowCustom": True,   # a custom note becomes the denial reason
        "allowMulti": False,
        "level": level,
        # Dangerous approvals read as high-signal; everything else is normal.
        "priority": "important" if level == "dangerous" else "normal",
        "auto_dispatched": False,
        "source": f"{req.get('channel', 'cli')}:{req.get('user_id', 'anon')}",
        "created_at": req.get("created_at"),
        "expires_at": req.get("expires_at"),
        "status": req.get("status", "pending"),
    }


async def handle_deck_requests_list(request: "web.Request") -> "web.Response":
    """Return all pending requests (approvals + questions) as one list.

    Always returns a list (never 402) so the Inbox tab renders even on the free
    tier — per-stream gating happens client-side.
    """
    gw = _gateway(request)
    kind_filter = request.rel_url.query.get("kind")
    out: list[dict] = []

    # Approvals
    am = getattr(gw, "approval_manager", None) if gw else None
    if am is not None:
        try:
            for r in am.get_pending():
                out.append(_approval_to_unified(r))
        except Exception:
            logger.debug("approval get_pending failed", exc_info=True)

    # Questions / route-asks / proposals
    reg = getattr(gw, "request_registry", None) if gw else None
    if reg is not None:
        try:
            out.extend(reg.get_pending())
        except Exception:
            logger.debug("request_registry get_pending failed", exc_info=True)

    if kind_filter:
        out = [r for r in out if r.get("kind") == kind_filter]

    # Newest first; expired/None created_at sinks to the bottom.
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return _ok({"requests": out})


async def handle_deck_requests_respond(request: "web.Request") -> "web.Response":
    """Resolve one request. Body: {choice?, custom?, approved?}."""
    request_id = request.match_info.get("request_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    choice = body.get("choice")
    custom = body.get("custom")
    approved = body.get("approved")

    gw = _gateway(request)
    am = getattr(gw, "approval_manager", None) if gw else None
    reg = getattr(gw, "request_registry", None) if gw else None

    # Approval path — resolve the boolean future.
    if am is not None and am.get_request(request_id):
        if approved is None:
            approved = (choice == "approve") or (
                isinstance(choice, list) and "approve" in choice
            )
        ok = await am.respond(request_id, bool(approved), reason=custom)
        return _ok({"resolved": bool(ok)}) if ok else _err("request not found", 404)

    # Registry path — questions / proposals.
    if reg is not None and reg.get(request_id):
        ok = await reg.answer(request_id, choice=choice, custom=custom)
        return _ok({"resolved": bool(ok)}) if ok else _err("request not found", 404)

    return _err("request not found", 404)


async def handle_deck_requests_next_action(request: "web.Request") -> "web.Response":
    """Ask navig for its next action — the header Run button.

    Runs the operator planner (finance + inbox + spaces signals → LLM, with a
    deterministic fallback) and lands a ``plan`` request the deck renders as a
    wizard. Choosing / editing / writing a step dispatches it as an agentic
    mission that runs to completion. Repeated clicks replace the prior plan
    rather than stacking duplicates.
    """
    gw = _gateway(request)
    reg = getattr(gw, "request_registry", None) if gw else None
    if reg is None:
        return _err("request registry unavailable", 503)

    # Dedupe: drop any prior pending operator plan before creating a new one.
    try:
        await reg.replace_pending_by_source("operator")
    except Exception:
        logger.debug("operator dedupe failed", exc_info=True)

    from navig.operator.planner import build_plan

    try:
        plan = await build_plan()
    except Exception:
        logger.exception("operator planner failed")
        return _err("planner failed to build a plan", 500)

    steps = plan.all_steps()

    async def _on_answer(answer: dict) -> None:
        # Resolve the chosen step (across every plan group):
        #   choice=<step id>            → approve that step as-is
        #   choice=<step id> + custom   → run the edited title
        #   custom only (no choice)     → write-your-own
        #   auto (auto-dispatch)        → take the first step
        custom = (answer.get("custom") or "").strip()
        # The RUN popover handles a step via the decompose/execute flow and then
        # dismisses this plan with "__handled__" — don't also one-shot it here.
        if custom == "__handled__" and not answer.get("choice"):
            return
        choice = answer.get("choice")
        if isinstance(choice, list):
            choice = choice[0] if choice else None
        chosen = next((s for s in steps if s.id == choice), None)
        if answer.get("auto") and chosen is None and steps:
            chosen = steps[0]
        title = custom or (chosen.title if chosen else "")
        if not title:
            return

        ex = getattr(gw, "mission_executor", None)
        if ex is None:
            logger.warning("operator plan answered but no mission_executor available")
            return
        try:
            from navig.contracts.mission import Mission

            mission = Mission(
                title=title,
                capability="agentic",
                payload={
                    "message": title,
                    "rationale": chosen.rationale if chosen else "",
                    "plan_summary": plan.summary,
                    "plan_context": plan.context_digest,
                },
                # The wizard pick IS the human approval, and the user chose
                # "run to completion" — so go AUTO (the verifier still gates it)
                # rather than asking for a second confirm.
                metadata={"autonomy": "auto", "source": "operator_plan"},
            )
            await ex.submit(mission)
            logger.info("operator plan → mission %s (%s)", mission.mission_id[:8], title[:60])
        except Exception:
            logger.exception("operator mission dispatch failed")

    try:
        rid = await reg.create(
            kind="plan",
            title="navig's proposed next steps",
            body=plan.summary,
            payload=plan.to_payload(),
            allow_custom=True,
            source="operator",
            priority="normal",
            on_answer=_on_answer,
            timeout=0,  # persistent: don't auto-expire — it lives in Inbox → Asks
        )
    except TypeError as exc:
        # An older request_registry (e.g. a daemon started before this feature's
        # backend landed) may not accept `payload`/`kind="plan"`. Degrade to a
        # plain request so the endpoint still responds instead of 500-ing — and
        # tell the user to restart for the full wizard.
        logger.warning("plan create rejected (%s) — retrying without payload; restart the daemon", exc)
        try:
            rid = await reg.create(
                kind="question",
                title="navig's proposed next steps",
                body=plan.summary,
                allow_custom=True,
                source="operator",
                priority="normal",
                on_answer=_on_answer,
            )
        except Exception as exc2:  # noqa: BLE001
            logger.exception("operator plan create failed")
            return _err(f"could not create plan request: {exc2}", 500)
    except Exception as exc:  # noqa: BLE001
        logger.exception("operator plan create failed")
        return _err(f"could not create plan request: {exc}", 500)

    # Return the full request so the RUN popover can render the wizard without a
    # second round-trip (it's also in the registry/Inbox as a fallback).
    try:
        req_dict = reg.get(rid)
    except Exception:  # noqa: BLE001
        req_dict = None
    return _ok({"request_id": rid, "steps": len(steps), "request": req_dict})
