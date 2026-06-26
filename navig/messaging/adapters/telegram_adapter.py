"""
Telegram Messaging Adapter — Outbound send surface for Telegram.

Compliance: **official** — uses python-telegram-bot (official Bot API).
Identity:   **bot** — messages come from the NAVIG Telegram bot.

This adapter wraps the already-running Telegram bot instance from
:class:`~navig.gateway.channels.telegram.TelegramChannelAdapter` and exposes
the :class:`~navig.messaging.adapter.ChannelAdapter` protocol for the
unified messaging layer's routing engine.

Not instantiated standalone — the gateway injects the running bot.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.messaging.adapter import (
    DeliveryReceipt,
    DeliveryStatus,
    InboundEvent,
    ResolvedTarget,
    Thread,
)

logger = logging.getLogger(__name__)


def _msg_id(msg: Any) -> str:
    """Extract a message id from a dict (channel) or object (PTB Message)."""
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("message_id", ""))
    return str(getattr(msg, "message_id", ""))


class TelegramMessagingAdapter:
    """
    Telegram messaging adapter for the unified messaging layer.

    Satisfies the :class:`~navig.messaging.adapter.ChannelAdapter` protocol.
    ``thread_id`` is a Telegram chat_id (string).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._bot: Any = None  # telegram.Bot instance (injected)

    # ── Protocol properties ───────────────────────────────────

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def capabilities(self) -> list[str]:
        return ["text", "media", "reactions", "buttons"]

    @property
    def identity_mode(self) -> str:
        return "bot"

    @property
    def compliance(self) -> str:
        return "official"

    # ── Send ──────────────────────────────────────────────────

    async def send_message(
        self,
        thread_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> DeliveryReceipt:
        """Send a Telegram message to a chat ID.

        ``attachments`` is a list of ``{path|url|data, kind, filename, mime,
        caption?}`` descriptors. When present, the post *text* rides as the
        caption of the first item (Telegram caps captions at 1024 chars); any
        remaining items are sent as follow-up media. Falls back to a plain text
        message when there are no attachments.
        """
        if self._bot is None:
            return DeliveryReceipt.failure("Telegram bot not initialised")

        chat_id = int(thread_id)
        try:
            if attachments:
                first_id: str | None = None
                for i, att in enumerate(attachments):
                    caption = text if i == 0 else (att.get("caption") or "")
                    mid = await self._send_attachment(chat_id, att, caption[:1024] or None)
                    if i == 0:
                        first_id = mid
                if first_id is None:
                    return DeliveryReceipt.failure("attachment send failed")
                return DeliveryReceipt.success(message_id=first_id, status=DeliveryStatus.SENT)

            msg = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            mid = _msg_id(msg)
            return DeliveryReceipt.success(message_id=mid, status=DeliveryStatus.SENT)
        except Exception as exc:
            logger.error("telegram_send_failed | chat=%s | error=%s", thread_id, exc)
            return DeliveryReceipt.failure(str(exc))

    async def _send_attachment(self, chat_id: int, att: dict[str, Any], caption: str | None) -> str | None:
        """Resolve an attachment's bytes and dispatch to the right Bot API send."""
        data = await self._attachment_bytes(att)
        if data is None:
            logger.warning("telegram attachment had no resolvable bytes: %s", att.get("filename"))
            return None
        kind = (att.get("kind") or "").lower()
        filename = att.get("filename") or "file"
        bot = self._bot
        try:
            if kind == "photo" and hasattr(bot, "send_photo"):
                return _msg_id(await bot.send_photo(chat_id, data, caption=caption))
            if kind == "video" and hasattr(bot, "send_video"):
                return _msg_id(await bot.send_video(chat_id, data, caption=caption))
            if kind == "animation" and hasattr(bot, "send_animation"):
                return _msg_id(await bot.send_animation(chat_id, data, caption=caption))
            if kind == "voice" and hasattr(bot, "send_voice"):
                return _msg_id(await bot.send_voice(chat_id, data))
            # audio / document / anything else → document
            if hasattr(bot, "send_document"):
                return _msg_id(await bot.send_document(chat_id, data, filename=filename, caption=caption))
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram attachment send failed (%s): %s", kind, exc)
        return None

    async def _attachment_bytes(self, att: dict[str, Any]) -> bytes | None:
        """Resolve attachment bytes from a local path, URL, or base64 ``data``."""
        from navig.messaging.attachments import attachment_bytes

        return await attachment_bytes(att, getattr(self._bot, "_session", None))

    # ── Resolve ───────────────────────────────────────────────

    def resolve_target(self, route: str) -> ResolvedTarget:
        """Parse ``telegram:<chat_id>`` into a target."""
        if ":" in route:
            _, _, address = route.partition(":")
        else:
            address = route
        address = address.strip()
        return ResolvedTarget(adapter="telegram", address=address)

    async def get_or_create_thread(self, route: str) -> Thread:
        """Telegram threads are keyed by chat_id."""
        target = self.resolve_target(route)
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        return store.get_or_create("telegram", target.address)

    # ── Inbound ───────────────────────────────────────────────

    async def receive_webhook(self, payload: dict[str, Any]) -> InboundEvent:
        """Parse a Telegram update into an InboundEvent."""
        message = payload.get("message", {})
        chat = message.get("chat", {})
        sender = message.get("from", {})
        return InboundEvent(
            adapter="telegram",
            remote_conversation_id=str(chat.get("id", "")),
            sender=str(sender.get("id", "")),
            text=message.get("text", ""),
            raw=payload,
        )

    async def ingest_event(self, event: InboundEvent) -> None:
        """Process an inbound Telegram message."""
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        thread = store.get_or_create("telegram", event.remote_conversation_id)
        store.touch(thread.id)

    # ── Injection ─────────────────────────────────────────────

    def set_bot(self, bot: Any) -> None:
        """Inject the running ``telegram.Bot`` instance from the gateway."""
        self._bot = bot
