"""
Media-generation catalog handler for the Deck API.

Exposes the image / video / audio generation provider catalog so the Deck can
build a picker showing every provider, its models, whether a key is configured,
its free-tier status, and where to get a key. Read-only; never returns secrets.

Routes:
    GET  /api/deck/media/providers  → { modalities: { image:[…], video:[…], audio:[…] } }
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


async def handle_deck_media_providers_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/media/providers — provider catalog + per-provider configured flag."""
    try:
        from navig.tools.media_providers import catalog_payload

        return web.json_response(catalog_payload())
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_deck_media_providers_get failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
