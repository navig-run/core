"""
Persona settings handlers for the Deck API.

Exposes the persona switcher that used to be invoked via /persona and /personas
in chat. The slash commands are deleted; users now switch via the Deck (or via
natural language phrases like "switch persona to tyler", handled by the NL
settings-intent module).

Routes:
    GET  /api/deck/persona    → active persona + list of available personas
    POST /api/deck/persona    → switch to a named persona
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _resolve_user_id(request: "web.Request") -> int:
    return int(request.get("deck_user_id", 0) or 0)


def _resolve_chat_id(request: "web.Request") -> int:
    """Best-effort chat_id resolution. Falls back to user_id when unknown."""
    val = request.get("deck_chat_id")
    if val:
        try:
            return int(val)
        except Exception:
            pass
    return _resolve_user_id(request)


async def handle_deck_persona_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/persona — active persona + list of available ones."""
    user_id = _resolve_user_id(request)
    try:
        from navig.personas.manager import list_personas
        from navig.personas.store import get_active_persona

        active = get_active_persona(user_id) or "default"
        configs = list_personas()
        personas: list[dict[str, Any]] = [
            {
                "name": p.name,
                "display_name": p.display_name,
                "tone": p.tone,
                "model_hint": p.model_hint,
            }
            for p in configs
        ]
        return web.json_response({"active": active, "personas": personas})
    except Exception as exc:
        logger.warning("handle_deck_persona_get failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_persona_post(request: "web.Request") -> "web.Response":
    """POST /api/deck/persona — switch persona for the calling user.

    Body: { "name": "<persona-name>" }. Returns the new active persona or an
    error if the named persona doesn't exist / failed to load.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"ok": False, "error": "name required"}, status=400)

    user_id = _resolve_user_id(request)
    chat_id = _resolve_chat_id(request)

    try:
        from navig.personas.manager import switch_persona

        cfg = await switch_persona(
            name,
            user_id=user_id,
            chat_id=chat_id,
            deliver_assets=False,  # Deck doesn't deliver wallpapers/sounds
            bot_client=None,
        )
        return web.json_response(
            {
                "ok": True,
                "active": cfg.name,
                "display_name": cfg.display_name,
                "tone": cfg.tone,
            }
        )
    except Exception as exc:
        logger.warning("handle_deck_persona_post failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
