"""Deck routes for the decision model (Cloudflare Clef) — read and flip its switches.

``GET  /api/deck/decider``  → the same status ``navig decider status --json`` prints.
``POST /api/deck/decider``  → body with any of ``enabled`` · ``model`` · ``shadow`` ·
``uses: {<use id>: "on" | "shadow" | "off" (or a bool)}``; persisted to config.yaml, returns the new status.
``POST /api/deck/decider/guard`` → body ``{tool, command?, arguments?, project_directory?}``;
the Guarded-mode risk check (:mod:`navig.agent.tool_guard`). ``{"ok": true, "checked": false}``
whenever there is no usable answer — the caller then asks the user.

No restart is needed: :mod:`navig.agent.decide` re-reads its config section whenever
config.yaml changes on disk.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


def _status() -> dict[str, Any]:
    from navig.agent import decide

    return decide.status()


def _apply(body: dict[str, Any]) -> dict[str, Any]:
    """Validate the whole body first, then write — a bad field changes nothing."""
    from navig.agent.decide import MODELS, MODES, USES
    from navig.config import get_config_manager
    from navig.core.coerce import coerce_bool

    writes: list[tuple[str, Any]] = []
    if "enabled" in body:
        writes.append(("decide.enabled", coerce_bool(body["enabled"], default=False)))
    if "shadow" in body:
        writes.append(("decide.shadow", coerce_bool(body["shadow"], default=False)))
    if "model" in body:
        model = str(body["model"] or "").strip().lower()
        if model not in MODELS:
            raise ValueError(f"unknown model {model!r}; one of {', '.join(MODELS)}")
        writes.append(("decide.model", model))
    uses = body.get("uses")
    if uses is not None:
        if not isinstance(uses, dict):
            raise ValueError("'uses' must be an object of {use id: bool}")
        for uid, value in uses.items():
            if uid not in USES:
                raise ValueError(f"unknown use {uid!r}; one of {', '.join(USES)}")
            if isinstance(value, str) and value.strip().lower() in MODES:
                writes.append((f"decide.use.{uid}", value.strip().lower()))
            else:
                writes.append((f"decide.use.{uid}", "on" if coerce_bool(value, default=False) else "off"))
    if not writes:
        raise ValueError("nothing to change: send enabled, model, shadow or uses")
    cm = get_config_manager()
    for key, value in writes:
        cm.set_global(key, value)
    return _status()


async def handle_deck_decider_get(request: web.Request) -> web.Response:
    try:
        return web.json_response({"ok": True, **await asyncio.to_thread(_status)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("decider status failed: %r", exc)
        return web.json_response({"ok": False, "error": "status_unavailable"}, status=500)


async def handle_deck_decider_guard(request: web.Request) -> web.Response:
    """Guarded-mode risk check for one tool call. Never a 5xx for "no answer": that is
    ``checked: false``, which NAVIG OS turns into a prompt (fail closed)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("tool"), str) or not body["tool"].strip():
        return web.json_response({"ok": False, "error": "'tool' is required"}, status=400)
    from navig.agent.tool_guard import judge_tool_call

    verdict = await judge_tool_call(body)
    if verdict is None:
        return web.json_response({"ok": True, "checked": False})
    return web.json_response({"ok": True, "checked": True, **verdict})


async def handle_deck_decider_set(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    try:
        status = await asyncio.to_thread(_apply, body)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("decider update failed: %r", exc)
        return web.json_response({"ok": False, "error": "update_failed"}, status=500)
    return web.json_response({"ok": True, **status})
