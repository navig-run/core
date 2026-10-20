"""Planner deck routes — tasks, agenda, reminders, lists and categories.

    GET    /api/deck/planner/today
    GET    /api/deck/planner/lists
    PATCH  /api/deck/planner/lists/{id}
    GET    /api/deck/planner/categories            ?archived=1
    POST   /api/deck/planner/categories            {name, emoji?, label?, color?}
    POST   /api/deck/planner/categories/reorder    {names: [...]}
    PATCH  /api/deck/planner/categories/{name}     {name?, label?, emoji?, color?, archived?}
    DELETE /api/deck/planner/categories/{name}     409 when still in use
    GET    /api/deck/planner/tasks                 ?list=me|tg:<id>|*  &category=  &include_done=1
    POST   /api/deck/planner/tasks                 {text} | {title, list_id?, category?, due_at?, leads?, recur?, notes?}
    PATCH  /api/deck/planner/tasks/{id}
    DELETE /api/deck/planner/tasks/{id}
    POST   /api/deck/planner/tasks/{id}/done | /reopen | /move {list_id}
    GET    /api/deck/planner/agenda                ?days=30 &proposed=1|0
    POST   /api/deck/planner/agenda                {title, start_at, end_at?, location?, with_whom?, leads?, notes?}
    PATCH  /api/deck/planner/agenda/{id}
    DELETE /api/deck/planner/agenda/{id}
    POST   /api/deck/planner/agenda/{id}/accept
    GET    /api/deck/planner/reminders
    POST   /api/deck/planner/reminders             {text}  — a one-off ping, typed language
    POST   /api/deck/planner/reminders/{source}/{id}/snooze  {seconds}
    DELETE /api/deck/planner/reminders/{source}/{id}

Envelope ``{"ok": true, "data": …}`` / ``{"ok": false, "error": …}`` — the board's.
Every route is OPERATOR-ONLY: this is the operator's personal data, and a Mini App
user who merely passed the deck's allowlist must still be the operator to read it.
All writes go through `navig.pim.service`, the one write path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _run(fn, *args, **kwargs):  # noqa: ANN001, ANN202
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def _json(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


def _is_operator(request: "web.Request") -> bool:
    """Local desktop / Bearer key, or a Telegram user who IS the operator.

    The deck middleware already refused anyone outside ``allowed_users`` when an
    allowlist is set. With no allowlist it lets every valid Telegram user through, so
    this falls back to the one id the rest of NAVIG treats as the operator.
    """
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
        logger.debug("planner: deck config unreadable", exc_info=True)
    try:
        from navig.messaging.notify_operator import resolve_operator_chat_id  # noqa: PLC0415

        operator = resolve_operator_chat_id()
        return bool(operator) and int(str(operator).strip()) == int(user_id)
    except Exception:  # noqa: BLE001
        return False


def _guard(handler):  # noqa: ANN001, ANN202
    """Operator check + one place that maps the service's errors to HTTP."""

    async def wrapped(request: "web.Request") -> "web.Response":
        from navig.pim import service  # noqa: PLC0415

        if not _is_operator(request):
            return _err("forbidden", 403)
        try:
            return await handler(request)
        except service.NotFound as exc:
            return _err(str(exc), 404)
        except service.Forbidden as exc:
            return _err(str(exc), 403)
        except service.PlannerError as exc:
            return _err(str(exc), 400)
        except Exception:  # noqa: BLE001
            logger.exception("planner route %s failed", request.path)
            return _err("internal error", 500)

    wrapped.__name__ = handler.__name__
    return wrapped


def _flag(request: "web.Request", key: str) -> bool:
    return str(request.query.get(key, "")).lower() in ("1", "true", "yes")


def _svc():  # noqa: ANN202
    from navig.pim import service  # noqa: PLC0415

    return service


# ── today / lists / categories ───────────────────────────────────────────────


@_guard
async def handle_planner_today(request: "web.Request") -> "web.Response":
    return _ok(await _run(_svc().today))


@_guard
async def handle_planner_lists(request: "web.Request") -> "web.Response":
    return _ok(await _run(_svc().lists))


@_guard
async def handle_planner_list_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    return _ok(await _run(_svc().update_list, request.match_info["id"], body))


@_guard
async def handle_planner_categories(request: "web.Request") -> "web.Response":
    return _ok(await _run(_svc().categories, include_archived=_flag(request, "archived")))


@_guard
async def handle_planner_category_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    out = await _run(
        _svc().create_category,
        str(body.get("name") or ""),
        emoji=str(body.get("emoji") or ""),
        label=body.get("label") or None,
        color=str(body.get("color") or ""),
    )
    return _ok(out, 201)


@_guard
async def handle_planner_category_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    return _ok(await _run(_svc().update_category, request.match_info["name"], body))


@_guard
async def handle_planner_category_delete(request: "web.Request") -> "web.Response":
    name = request.match_info["name"]
    service = _svc()
    try:
        await _run(service.delete_category, name)
    except service.NotFound:
        raise
    except service.PlannerError as exc:
        return _err(str(exc), 409)
    return _ok({"deleted": name})


@_guard
async def handle_planner_category_reorder(request: "web.Request") -> "web.Response":
    body = await _json(request)
    names = (body or {}).get("names")
    if not isinstance(names, list):
        return _err("names must be a list")
    return _ok(await _run(_svc().reorder_categories, [str(n) for n in names]))


# ── tasks ────────────────────────────────────────────────────────────────────


@_guard
async def handle_planner_tasks(request: "web.Request") -> "web.Response":
    list_id = request.query.get("list", "me")
    return _ok(await _run(
        _svc().tasks,
        list_id=list_id,
        category=request.query.get("category") or None,
        include_done=_flag(request, "include_done"),
    ))


@_guard
async def handle_planner_task_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    if not (body.get("text") or body.get("title")):
        return _err("text or title is required")
    service = _svc()
    item = await _run(
        service.create_task,
        str(body.get("text") or "") or None,
        title=str(body.get("title") or "") or None,
        list_id=body.get("list_id") or "me",
        category=body.get("category") or None,
        due_at=body.get("due_at"),
        leads=body.get("leads"),
        recur=body.get("recur") or None,
        notes=str(body.get("notes") or ""),
        priority=str(body.get("priority") or "normal"),
    )
    return _ok(service.present(item), 201)


@_guard
async def handle_planner_item_update(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    service = _svc()
    fields = dict(body)
    if "leads" in fields and "remind_before" not in fields:
        fields["remind_before"] = fields.pop("leads")
    item = await _run(service.update, request.match_info["id"], fields)
    return _ok(service.present(item))


@_guard
async def handle_planner_item_delete(request: "web.Request") -> "web.Response":
    item_id = request.match_info["id"]
    await _run(_svc().delete, item_id)
    return _ok({"deleted": item_id})


@_guard
async def handle_planner_task_done(request: "web.Request") -> "web.Response":
    service = _svc()
    return _ok(service.present(await _run(service.complete, request.match_info["id"])))


@_guard
async def handle_planner_task_reopen(request: "web.Request") -> "web.Response":
    service = _svc()
    return _ok(service.present(await _run(service.reopen, request.match_info["id"])))


@_guard
async def handle_planner_task_move(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if not body or not body.get("list_id"):
        return _err("list_id is required")
    service = _svc()
    return _ok(service.present(await _run(service.move, request.match_info["id"], str(body["list_id"]))))


# ── agenda ───────────────────────────────────────────────────────────────────


@_guard
async def handle_planner_agenda(request: "web.Request") -> "web.Response":
    from navig.pim.clock import local_now  # noqa: PLC0415

    try:
        days = max(1, min(366, int(request.query.get("days", "30"))))
    except ValueError:
        return _err("days must be a number")
    proposed = request.query.get("proposed")
    now = local_now()
    return _ok(await _run(
        _svc().agenda,
        start=now - timedelta(hours=2),
        end=now + timedelta(days=days),
        include_done=_flag(request, "include_done"),
        proposed=None if proposed is None else proposed in ("1", "true"),
    ))


@_guard
async def handle_planner_agenda_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("Invalid JSON")
    service = _svc()
    item = await _run(
        service.create_appointment,
        str(body.get("title") or ""),
        body.get("start_at"),
        end_at=body.get("end_at"),
        location=body.get("location"),
        with_whom=body.get("with_whom"),
        notes=str(body.get("notes") or ""),
        leads=body.get("leads", service.APPOINTMENT_LEADS),
    )
    return _ok(service.present(item), 201)


@_guard
async def handle_planner_agenda_accept(request: "web.Request") -> "web.Response":
    service = _svc()
    return _ok(service.present(await _run(service.accept, request.match_info["id"])))


# ── reminders ────────────────────────────────────────────────────────────────


@_guard
async def handle_planner_reminders(request: "web.Request") -> "web.Response":
    return _ok(await _run(_svc().reminders))


@_guard
async def handle_planner_reminder_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if not body or not str(body.get("text") or "").strip():
        return _err("text is required — e.g. “in 30 min call mom”")
    return _ok(await _run(_svc().add_reminder, str(body["text"])), 201)


@_guard
async def handle_planner_reminder_snooze(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    try:
        seconds = int(body.get("seconds") or 3600)
    except (TypeError, ValueError):
        return _err("seconds must be a number")
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    channel = (getattr(gateway, "channels", None) or {}).get("telegram") if gateway else None
    return _ok(await _run(
        _svc().snooze_reminder,
        request.match_info["source"],
        request.match_info["id"],
        seconds,
        channel=channel,
    ))


@_guard
async def handle_planner_reminder_cancel(request: "web.Request") -> "web.Response":
    source, rid = request.match_info["source"], request.match_info["id"]
    if source not in ("runtime", "biz"):
        return _err("unknown reminder source", 404)
    await _run(_svc().cancel_reminder, source, rid)
    return _ok({"cancelled": rid})
