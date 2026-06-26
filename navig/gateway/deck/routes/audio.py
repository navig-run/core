"""
Voice & Audio settings handlers for the Deck API.

Exposes read/write access to the per-user AudioConfig that used to be edited
via the in-chat /voice and /audio menus. The menus are deleted; the Deck owns
this surface now, and natural-language phrases ("switch voice to nova", "turn
voice replies on") still work via the NL settings-intent module.

Routes:
    GET  /api/deck/audio    → current AudioConfig + provider/model/voice catalog
    POST /api/deck/audio    → partial update of AudioConfig fields
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


_AUDIO_STR_KEYS: frozenset[str] = frozenset({"provider", "model", "voice", "format"})
_AUDIO_BOOL_KEYS: frozenset[str] = frozenset({"auto", "active", "voice_replies_enabled"})
_AUDIO_FLOAT_KEYS: frozenset[str] = frozenset({"speed"})


def _resolve_user_id(request: "web.Request") -> int:
    """Pull the user_id the auth middleware stamped onto the request."""
    return int(request.get("deck_user_id", 0) or 0)


def _audio_catalog() -> dict[str, Any]:
    """Return the provider/model/voice catalog so the Deck can build pickers."""
    try:
        from navig.gateway.channels.audio_menu.config import (
            FORMATS,
            PROVIDERS,
            SPEEDS,
        )

        return {
            "providers": [
                {
                    "id": pid,
                    "label": pdata.get("label", pid),
                    "models": [
                        {
                            "id": mid,
                            "label": mdata.get("label", mid),
                            "voices": list(mdata.get("voices") or []),
                        }
                        for mid, mdata in (pdata.get("models") or {}).items()
                    ],
                }
                for pid, pdata in PROVIDERS.items()
            ],
            "speeds": list(SPEEDS),
            "formats": list(FORMATS),
        }
    except Exception as exc:
        logger.debug("audio catalog unavailable: %s", exc)
        return {"providers": [], "speeds": [], "formats": []}


async def handle_deck_audio_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/audio — return the user's AudioConfig + provider catalog."""
    user_id = _resolve_user_id(request)
    try:
        from navig.gateway.channels.audio_menu.state import load_config

        cfg = load_config(user_id)
        payload = {
            "provider": cfg.provider,
            "model": cfg.model,
            "voice": cfg.voice,
            "speed": cfg.speed,
            "format": cfg.format,
            "auto": cfg.auto,
            "active": cfg.active,
            "voice_replies_enabled": cfg.voice_replies_enabled,
            "catalog": _audio_catalog(),
        }
        return web.json_response(payload)
    except Exception as exc:
        logger.warning("handle_deck_audio_get failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_audio_post(request: "web.Request") -> "web.Response":
    """POST /api/deck/audio — partial update of AudioConfig.

    Accepts a JSON object with any subset of editable keys. Unknown / invalid
    keys are skipped silently. Returns the updated payload on success.
    """
    user_id = _resolve_user_id(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    try:
        from navig.gateway.channels.audio_menu.state import load_config, save_config

        cfg = load_config(user_id)
        updated: list[str] = []
        errors: list[str] = []

        for key, value in body.items():
            if key in _AUDIO_STR_KEYS:
                if not isinstance(value, str):
                    errors.append(f"{key}: expected string")
                    continue
                setattr(cfg, key, value)
                updated.append(key)
            elif key in _AUDIO_BOOL_KEYS:
                if not isinstance(value, bool):
                    errors.append(f"{key}: expected bool")
                    continue
                setattr(cfg, key, value)
                updated.append(key)
            elif key in _AUDIO_FLOAT_KEYS:
                if not isinstance(value, (int, float)):
                    errors.append(f"{key}: expected number")
                    continue
                setattr(cfg, key, float(value))
                updated.append(key)
            else:
                continue  # skip unknown keys silently

        if updated:
            save_config(user_id, cfg)

        return web.json_response(
            {
                "ok": True,
                "updated": updated,
                "errors": errors,
                "provider": cfg.provider,
                "model": cfg.model,
                "voice": cfg.voice,
                "speed": cfg.speed,
                "format": cfg.format,
                "auto": cfg.auto,
                "active": cfg.active,
                "voice_replies_enabled": cfg.voice_replies_enabled,
            }
        )
    except Exception as exc:
        logger.warning("handle_deck_audio_post failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
