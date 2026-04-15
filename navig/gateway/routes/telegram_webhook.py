"""Telegram webhook route: POST /telegram/webhook

Receives updates pushed by Telegram when the bot is in webhook mode.
Forwards each update to the TelegramChannel for processing.

Hardenings (Hermes-style):
  - Payload size guard: requests >1 MB are rejected with 413 before parsing.
  - Update-ID idempotency: replayed update_ids are ACKed but not processed.
  - Channel-lookup failure is logged at DEBUG rather than silently swallowed.

Telegram sends a ``X-Telegram-Bot-Api-Secret-Token`` header when a
``secret_token`` was configured via ``setWebhook``.  We validate it
before accepting the update.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Maximum allowed request body size (bytes).  Telegram update payloads are
# typically <10 KB; 1 MB is a generous limit that prevents memory exhaustion
# from malformed or oversized requests.
_MAX_PAYLOAD_BYTES: int = 1 * 1024 * 1024  # 1 MB

# LRU idempotency cache: stores the last N update_ids we have already accepted.
# Telegram may replay the same update when it does not receive a timely 200 OK.
_SEEN_UPDATE_IDS_CAPACITY: int = 1000


def _make_seen_update_ids() -> collections.OrderedDict:
    """Return a new ordered-dict used as a bounded LRU set."""
    return collections.OrderedDict()


def register(app: web.Application, gateway: NavigGateway) -> None:
    """Register the /telegram/webhook endpoint."""
    seen: collections.OrderedDict = _make_seen_update_ids()
    app.router.add_post("/telegram/webhook", _webhook_handler(gateway, seen))


def _webhook_handler(gw: NavigGateway, seen_update_ids: collections.OrderedDict):
    """
    POST /telegram/webhook

    Accepts a Telegram Update JSON body.  Validates the secret token
    header and forwards to the TelegramChannel for processing.

    Returns 200 on success (Telegram requires a fast 200 response).
    Payload size limit: requests >1 MB are rejected with 413 before parsing.
    """

    async def handler(request: web.Request) -> web.Response:
        # ── Payload size guard ────────────────────────────────────────────────
        content_length = request.content_length
        if content_length is not None and content_length > _MAX_PAYLOAD_BYTES:
            logger.warning(
                "Webhook payload too large: %d bytes (limit %d) — rejecting",
                content_length,
                _MAX_PAYLOAD_BYTES,
            )
            return web.json_response({"error": "Payload too large"}, status=413)

        # Get the Telegram channel from the gateway
        telegram_channel = None
        try:
            channels = getattr(gw, "channels", {})
            telegram_channel = channels.get("telegram")
            if telegram_channel is None:
                # Try getting from channel registry
                registry = getattr(gw, "channel_registry", None)
                if registry:
                    telegram_channel = getattr(registry, "telegram", None) or registry.get(
                        "telegram"
                    )
        except Exception as _ch_exc:  # noqa: BLE001
            logger.debug("Webhook channel lookup error (non-critical): %s", _ch_exc)

        if telegram_channel is None:
            return web.json_response(
                {"error": "Telegram channel not configured"},
                status=503,
            )

        # ── Parse update body ─────────────────────────────────────────────────
        try:
            body = await request.read()
            # Second size check on actual body (Content-Length may be absent)
            if len(body) > _MAX_PAYLOAD_BYTES:
                logger.warning(
                    "Webhook body too large: %d bytes (limit %d) — rejecting",
                    len(body),
                    _MAX_PAYLOAD_BYTES,
                )
                return web.json_response({"error": "Payload too large"}, status=413)
            import json as _json
            update = _json.loads(body)
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        # ── Update-ID idempotency ─────────────────────────────────────────────
        update_id = update.get("update_id")
        if update_id is not None:
            if update_id in seen_update_ids:
                # Already processed — ACK so Telegram stops retrying but don't dispatch.
                logger.debug("Webhook duplicate update_id=%s — skipping replay", update_id)
                return web.json_response({"ok": True})
            # Record in LRU cache; evict oldest if over capacity.
            seen_update_ids[update_id] = True
            if len(seen_update_ids) > _SEEN_UPDATE_IDS_CAPACITY:
                seen_update_ids.popitem(last=False)  # pop oldest

        # ── Secret token validation + dispatch ───────────────────────────────
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

        try:
            accepted = await telegram_channel.handle_webhook_update(update, secret_header)
            if not accepted:
                return web.json_response({"error": "Rejected"}, status=403)
        except Exception as e:
            logger.error("Webhook handler error: %s", e)
            # Still return 200 so Telegram doesn't retry (compatibility mode).

        # Telegram expects a fast 200 OK
        return web.json_response({"ok": True})

    return handler
