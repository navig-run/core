"""Coordination deck routes — the operator-facing surface over the durable message store.

    GET  /api/deck/coordination/inbox?recipient=<ref>&states=a,b
    POST /api/deck/coordination/messages
    POST /api/deck/coordination/messages/{id}/deliver|consume|ack|act|cancel

OPERATOR-ONLY: a message may name any participant, so only the operator (or the deck Bearer
on loopback) may author it. The store/executor path is the other writer; nothing else writes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _json(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


def _is_operator(request: web.Request) -> bool:
    if request.get("deck_auth_local"):
        return True
    user_id = request.get("deck_user_id")
    if not user_id:
        return False
    try:
        from navig.gateway.deck.auth import _deck_config  # noqa: PLC0415

        allowed = _deck_config.get("allowed_users") or []
        if _deck_config.get("allowed_users_configured", bool(allowed)):
            return int(user_id) in {int(x) for x in allowed}
    except Exception:  # noqa: BLE001
        logger.debug("coordination: deck config unreadable", exc_info=True)
    try:
        from navig.messaging.notify_operator import resolve_operator_chat_id  # noqa: PLC0415

        operator = resolve_operator_chat_id()
        return bool(operator) and int(str(operator).strip()) == int(user_id)
    except Exception:  # noqa: BLE001
        return False


def _guard(handler):  # noqa: ANN001, ANN202
    async def wrapped(request: web.Request) -> web.Response:
        if not _is_operator(request):
            return _err("forbidden", 403)
        try:
            return await handler(request)
        except ValueError as exc:
            return _err(str(exc), 400)
        except Exception:  # noqa: BLE001
            logger.exception("coordination route %s failed", request.path)
            return _err("internal error", 500)

    wrapped.__name__ = handler.__name__
    return wrapped


@_guard
async def handle_coordination_inbox(request: web.Request) -> web.Response:
    from navig.coordination import list_inbox  # noqa: PLC0415

    recipient = request.query.get("recipient", "")
    states_raw = request.query.get("states", "")
    states = tuple(s for s in (x.strip() for x in states_raw.split(",")) if s) or None
    rows = list_inbox(recipient, states=states)
    return _ok({"recipient": recipient, "count": len(rows), "messages": rows})


@_guard
async def handle_coordination_send(request: web.Request) -> web.Response:
    from navig.coordination import send  # noqa: PLC0415

    body = await _json(request)
    if body is None:
        return _err("invalid JSON")
    if not body.get("sender") or not body.get("recipient"):
        return _err("sender and recipient are required")
    message = send(
        str(body["sender"]),
        str(body["recipient"]),
        str(body.get("kind") or "inform"),
        body.get("content") or {},
        mission_id=body.get("mission_id"),
        task_id=body.get("task_id"),
        session_id=body.get("session_id"),
        correlation_id=body.get("correlation_id"),
        causation_id=body.get("causation_id"),
        reply_to=body.get("reply_to"),
        priority=body.get("priority"),
        evidence_refs=body.get("evidence_refs"),
        idempotency_key=body.get("idempotency_key"),
        expires_at=body.get("expires_at"),
    )
    return _ok(message, 201)


@_guard
async def handle_coordination_transition(request: web.Request) -> web.Response:
    from navig import coordination  # noqa: PLC0415

    message_id = request.match_info["id"]
    verb = request.match_info["verb"]
    if verb not in ("deliver", "consume", "ack", "act", "cancel"):
        return _err("unknown transition", 404)
    transition = getattr(coordination, verb)
    updated = transition(message_id)
    return _ok(updated)


@_guard
async def handle_coordination_tick(request: web.Request) -> web.Response:
    """Run one delivery pass (the same authority the gateway's loop would use)."""
    from navig.coordination import delivery  # noqa: PLC0415

    outcomes = await asyncio.to_thread(delivery.tick)
    return _ok({"count": len(outcomes), "outcomes": [asdict(o) for o in outcomes]})


@_guard
async def handle_coordination_delegate(request: web.Request) -> web.Response:
    """Enqueue a scoped delegation task on the gateway's existing TaskQueue."""
    from navig.coordination.delegation import delegate  # noqa: PLC0415

    body = await _json(request)
    if body is None:
        return _err("invalid JSON")
    if not body.get("parent") or not body.get("assignee") or not body.get("operation"):
        return _err("parent, assignee and operation are required")

    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    task_queue = getattr(gateway, "task_queue", None)
    if task_queue is None:
        return _err("task queue is not available", 503)

    task = delegate(
        str(body["parent"]),
        str(body["assignee"]),
        str(body["operation"]),
        context=body.get("context"),
        depends_on=body.get("depends_on"),
    )
    await task_queue.add(task)
    return _ok({"task_id": task.id, "handler": task.handler, "dependencies": task.dependencies}, 202)


def register(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/deck/coordination/inbox", handle_coordination_inbox)
    r.add_post("/api/deck/coordination/messages", handle_coordination_send)
    r.add_post("/api/deck/coordination/messages/{id}/{verb}", handle_coordination_transition)
    r.add_post("/api/deck/coordination/tick", handle_coordination_tick)
    r.add_post("/api/deck/coordination/delegate", handle_coordination_delegate)
