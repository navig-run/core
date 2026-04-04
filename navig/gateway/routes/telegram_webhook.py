"""Telegram webhook route: POST /telegram/webhook

Receives updates pushed by Telegram when the bot is in webhook mode.
Forwards each update to the TelegramChannel for processing.

Telegram sends a ``X-Telegram-Bot-Api-Secret-Token`` header when a
``secret_token`` was configured via ``setWebhook``.  We validate it
before accepting the update.
"""

from __future__ import annotations

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


def register(app: web.Application, gateway: NavigGateway) -> None:
    """Register the /telegram/webhook endpoint."""
    app.router.add_post("/telegram/webhook", _webhook_handler(gateway))


def _webhook_handler(gw: NavigGateway):
    """
    POST /telegram/webhook

    Accepts a Telegram Update JSON body.  Validates the secret token
    header and forwards to the TelegramChannel for processing.

    Returns 200 on success (Telegram requires a fast 200 response).
    """

    async def handler(request: web.Request) -> web.Response:
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
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        if telegram_channel is None:
            return web.json_response(
                {"error": "Telegram channel not configured"},
                status=503,
            )

        # Parse update body
        try:
            update = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        # Extract secret token header
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

        # Forward to the channel
        try:
            accepted = await telegram_channel.handle_webhook_update(update, secret_header)
            if not accepted:
                return web.json_response({"error": "Rejected"}, status=403)
        except Exception as e:
            logger.error("Webhook handler error: %s", e)
            # Still return 200 so Telegram doesn't retry
            pass

        # Telegram expects a fast 200 OK
        return web.json_response({"ok": True})

    return handler
