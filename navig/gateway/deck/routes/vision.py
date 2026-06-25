"""
Vision-model settings handler for the Deck API.

Replaces the in-chat /provider_vision picker. Vision config is stored at
`media_engine.vision_provider` + `media_engine.vision_model` in the global
config; reads/writes are routed through the same ConfigManager used by the
rest of the Deck.

Routes:
    GET  /api/deck/vision   → current provider+model + provider/model catalog
    POST /api/deck/vision   → write { provider, model } (partial OK)
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _get_config_manager():
    try:
        from navig.config import get_config_manager

        return get_config_manager()
    except Exception as exc:
        logger.debug("config manager unavailable: %s", exc)
        return None


def _read_vision() -> dict:
    cfg = _get_config_manager()
    if cfg is None:
        return {"provider": "", "model": ""}
    try:
        gcfg = cfg.global_config or {}
        me = gcfg.get("media_engine") or {}
        return {
            "provider": str(me.get("vision_provider") or ""),
            "model": str(me.get("vision_model") or ""),
        }
    except Exception as exc:
        logger.debug("vision read failed: %s", exc)
        return {"provider": "", "model": ""}


def _write_vision(patch: dict) -> tuple[bool, str]:
    """Persist vision_provider / vision_model. Returns (ok, error)."""
    cfg = _get_config_manager()
    if cfg is None:
        return False, "config manager unavailable"
    try:
        gcfg = dict(cfg.global_config or {})
        me = dict(gcfg.get("media_engine") or {})
        if "provider" in patch:
            me["vision_provider"] = str(patch["provider"] or "")
        if "model" in patch:
            me["vision_model"] = str(patch["model"] or "")
        gcfg["media_engine"] = me
        cfg.update_global_config(gcfg)
        return True, ""
    except Exception as exc:
        logger.warning("vision write failed: %s", exc)
        return False, str(exc)


def _vision_catalog() -> dict:
    """Return providers + their vision-capable models so the Deck can render a picker."""
    try:
        from navig.providers.discovery import list_connected_providers
        from navig.vault import get_vault
    except Exception as exc:
        logger.debug("vision catalog imports failed: %s", exc)
        return {"providers": []}

    catalog: list[dict] = []
    try:
        for prov in list_connected_providers():
            vision_models = list(getattr(prov, "vision_models", []) or [])
            if not vision_models:
                continue
            # vision_models items are typically (model_name, label) tuples or plain strings.
            models: list[str] = []
            for entry in vision_models:
                if isinstance(entry, tuple) and entry:
                    models.append(str(entry[0]))
                elif isinstance(entry, str):
                    models.append(entry)
            catalog.append({
                "id": prov.provider_id if hasattr(prov, "provider_id") else getattr(prov, "id", ""),
                "label": getattr(prov, "label", "") or getattr(prov, "provider_id", ""),
                "connected": bool(getattr(prov, "connected", False)),
                "models": models,
            })
    except Exception as exc:
        logger.debug("vision catalog enumeration failed: %s", exc)

    # Fallback: surface a static list of common vision-capable providers/models
    # so the UI is usable even when the discovery layer hasn't enumerated them.
    if not catalog:
        catalog = [
            {"id": "openai", "label": "OpenAI", "connected": False, "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-vision-preview"]},
            {"id": "anthropic", "label": "Anthropic", "connected": False, "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]},
            {"id": "google", "label": "Google", "connected": False, "models": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash-exp"]},
            {"id": "openrouter", "label": "OpenRouter", "connected": False, "models": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet"]},
        ]

    return {"providers": catalog}


async def handle_deck_vision_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/vision — current vision settings + catalog of choices."""
    payload = _read_vision()
    payload["catalog"] = _vision_catalog()
    return web.json_response(payload)


async def handle_deck_vision_post(request: "web.Request") -> "web.Response":
    """POST /api/deck/vision — patch { provider?, model? }."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    patch: dict = {}
    if "provider" in body:
        if not isinstance(body["provider"], str):
            return web.json_response({"ok": False, "error": "provider: expected string"}, status=400)
        patch["provider"] = body["provider"]
    if "model" in body:
        if not isinstance(body["model"], str):
            return web.json_response({"ok": False, "error": "model: expected string"}, status=400)
        patch["model"] = body["model"]
    if not patch:
        return web.json_response({"ok": False, "error": "no editable keys in body"}, status=400)

    ok, err = _write_vision(patch)
    if not ok:
        return web.json_response({"ok": False, "error": err}, status=500)

    current = _read_vision()
    return web.json_response({"ok": True, **current})
