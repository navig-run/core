"""Deck routes for check-ins — the Mini App's Settings → Telegram → Check-ins.

    GET    /api/deck/checkins          every check-in with today's state
    POST   /api/deck/checkins          add · update · pause/resume (see below)
    DELETE /api/deck/checkins/{id}     remove one (its recorded answers are kept)

POST body:

* ``{"template": "pills", "name", "dose", "times": ["08:00"], "missed_after"?}``
  or ``{"template": "water", "target", "every_hours", "from_hour", "to_hour"}``
  — add (no ``id``) or replace the settings of ``id``;
* ``{"id": "<id>", "enabled": true|false}`` — pause / resume (``weigh`` included:
  it maps onto the weigh-in cadence's on/off).

Validation is :func:`navig.checkins.defs.validate` — the same function the CLI
uses, so the two cannot accept different things. Every write is READ BACK, and
the response is what persisted, never what was asked for.

Responses are ``{"ok": true, "data": …}`` / ``{"ok": false, "error": …}``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

_EDITABLE = {
    "pills": ("name", "dose", "times", "missed_after", "enabled"),
    "water": ("target", "every_hours", "from_hour", "to_hour", "enabled"),
}


def _ok(data: Any, status: int = 200) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": False, "error": msg}, status=status)


def _entry(item_id: str) -> dict[str, Any] | None:
    from navig.checkins import engine
    from navig.checkins.jobs import _default_chat

    for e in engine.today_entries(_default_chat()):
        if e["id"] == item_id:
            return e
    return None


async def handle_checkins_list(request: "web.Request") -> "web.Response":
    from navig.checkins import engine
    from navig.checkins.jobs import _default_chat

    try:
        entries = await asyncio.to_thread(lambda: engine.today_entries(_default_chat()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-ins could not be listed: %s", exc)
        return _err("couldn't read the check-ins", 500)
    return _ok({"checkins": entries})


def _apply(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """The write, synchronously (run in a thread). Raises ValueError / KeyError."""
    from navig.checkins import defs as d
    from navig.checkins import jobs

    item_id = body.get("id")
    if item_id is not None and not isinstance(item_id, str):
        raise ValueError("'id' must be a string")
    chat = jobs._default_chat()

    # Pause / resume only.
    if item_id and set(body) <= {"id", "enabled"} and "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("'enabled' must be true or false")
        if d.get_item(item_id) is None:
            raise KeyError(item_id)
        saved = d.set_enabled(item_id, body["enabled"])
        if saved.template != "weigh":
            if saved.enabled and chat:
                jobs.sync_jobs(saved, chat)
            else:
                jobs.set_jobs_enabled(saved.id, saved.enabled)
        if saved.enabled != body["enabled"]:
            raise ValueError("did not persist: enabled")
        return _entry(saved.id) or saved.as_dict(), 200

    if item_id == d.WEIGH_ID:
        raise ValueError("the weigh-in's schedule is set under /telegram/bot/weigh-in")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ValueError("'enabled' must be true or false")
    if not chat:
        raise ValueError("no Telegram chat is configured (telegram.allowed_users)")

    if item_id:
        existing = d.get_item(item_id)
        if existing is None:
            raise KeyError(item_id)
        template = existing.template
        merged = existing.to_config()
        for key in _EDITABLE[template]:
            if key in body:
                merged[key] = body[key]
        item = d.validate(template, merged, item_id=existing.id)
        item = replace(item, created=existing.created)
        status = 200
    else:
        template = body.get("template")
        if template not in _EDITABLE:
            raise ValueError("'template' must be 'pills' or 'water'")
        unknown = set(body) - set(_EDITABLE[template]) - {"template"}
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
        if template == "water" and d.get_item(d.WATER_ID) is not None:
            raise ValueError("a water check-in already exists")
        item = d.validate(template, body)
        item = replace(item, id=d.unique_id(item.id, {i.id for i in d.load_items()}))
        status = 201
    saved = d.save_item(item)
    jobs.sync_jobs(saved, chat)
    return _entry(saved.id) or saved.as_dict(), status


async def handle_checkins_save(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("body must be a JSON object")
    if not isinstance(body, dict):
        return _err("body must be a JSON object")
    try:
        data, status = await asyncio.to_thread(_apply, body)
    except KeyError as exc:
        return _err(f"no check-in {exc.args[0]!r}", 404)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-in could not be saved: %s", exc)
        return _err("couldn't save the check-in", 500)
    return _ok(data, status)


async def handle_checkins_delete(request: "web.Request") -> "web.Response":
    from navig.checkins import defs as d
    from navig.checkins import jobs, state

    item_id = request.match_info.get("id", "")
    if item_id == d.WEIGH_ID:
        return _err("the weigh-in is built in — pause it instead")
    if d.get_item(item_id) is None:
        return _err(f"no check-in {item_id!r}", 404)

    def _remove() -> int:
        removed = jobs.remove_jobs(item_id)
        d.remove_item(item_id)
        state.forget(item_id)
        return removed

    try:
        removed = await asyncio.to_thread(_remove)
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-in %s could not be removed: %s", item_id, exc)
        return _err("couldn't remove the check-in", 500)
    if d.get_item(item_id) is not None:
        return _err("did not persist: the check-in is still configured", 500)
    return _ok({"removed": item_id, "jobs_removed": removed})


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/checkins", handle_checkins_list)
    app.router.add_post("/api/deck/checkins", handle_checkins_save)
    app.router.add_delete("/api/deck/checkins/{id}", handle_checkins_delete)
