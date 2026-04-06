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
        """Send a Telegram message to a chat ID."""
        if self._bot is None:
            return DeliveryReceipt.failure("Telegram bot not initialised")

        try:
            msg = await self._bot.send_message(
                chat_id=int(thread_id),
                text=text,
                parse_mode="HTML",
            )
            return DeliveryReceipt.success(
                message_id=str(msg.message_id),
                status=DeliveryStatus.SENT,
            )
        except Exception as exc:
            logger.error("telegram_send_failed | chat=%s | error=%s", thread_id, exc)
            return DeliveryReceipt.failure(str(exc))

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
