"""
Telegram Messaging Adapter — Outbound send surface for Telegram.

Compliance: **official** — uses python-telegram-bot (official Bot API).
Identity:   **bot** — messages come from the NAVIG Telegram bot.

This adapter wraps the already-running Telegram bot instance from
:class:`~navig.gateway.channels.telegram.TelegramChannel` and exposes
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


def _sent_id(msg: Any) -> str | None:
    """Message id for a DELIVERED send, or ``None`` when the transport REJECTED it.

    The bot returns ``None`` from a rejected send **without raising** — a 429 the
    retry budget ran out on, an API error, a timeout (``TelegramChannel._api_call``).
    ``_msg_id`` maps that to ``""``, and ``""`` is not ``None``, so every
    ``if mid is not None`` caller counted a rejection as a delivery.

    A returned object with no readable id is a DIFFERENT case and stays a success:
    the message did go out, so reporting failure would make the caller retry and
    duplicate it — the exact hazard ``_send_with_attachments`` was written to avoid.
    """
    if msg is None:
        return None
    return _msg_id(msg)


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
                return await self._send_with_attachments(chat_id, text, attachments)

            msg = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            mid = _sent_id(msg)
            if mid is None:
                # A rejected send does not raise — it returns None. Reporting success
                # here writes a "delivered" row into the delivery tracker
                # (`messaging/send.py` → `tracker.apply_receipt`) for a message that
                # never went out.
                logger.warning(
                    "telegram_send_rejected | chat=%s | the transport returned no message",
                    thread_id,
                )
                return DeliveryReceipt.failure(
                    "Telegram rejected the send (rate limit, API error, or timeout)"
                )
            return DeliveryReceipt.success(message_id=mid, status=DeliveryStatus.SENT)
        except Exception as exc:
            logger.error("telegram_send_failed | chat=%s | error=%s", thread_id, exc)
            return DeliveryReceipt.failure(str(exc))

    async def _send_with_attachments(
        self, chat_id: int, text: str, attachments: list[dict[str, Any]]
    ) -> DeliveryReceipt:
        """Send text + every attachment, and report a receipt that reflects ALL of them.

        The old code decided the whole receipt from attachment[0] only: if the first
        item sent it returned success even when later items were silently dropped, and
        if the first item failed it returned failure even though later items had already
        gone out (→ duplicate media on retry). Now we (1) resolve every attachment's
        bytes UP FRONT — resolution is side-effect-free, so a url that can't be fetched
        (no aiohttp session is ever wired) fails the whole send *before* anything goes
        out, keeping the common failure retry-safe — and (2) require every item to
        actually send before reporting success.
        """
        resolved: list[tuple[dict[str, Any], bytes]] = []
        for att in attachments:
            data = await self._attachment_bytes(att)
            if data is None:
                return DeliveryReceipt.failure(
                    f"could not resolve attachment {att.get('filename') or '?'}; "
                    "refusing to send a partial message"
                )
            resolved.append((att, data))

        first_id: str | None = None
        sent = 0
        for i, (att, data) in enumerate(resolved):
            caption = text if i == 0 else (att.get("caption") or "")
            mid = await self._dispatch_attachment(chat_id, att, data, caption[:1024] or None)
            if mid is not None:
                sent += 1
                if first_id is None:
                    first_id = mid

        if sent != len(attachments):
            # Bytes resolved but the Bot API rejected some mid-send. We can't unsend what
            # already went out, but the caller MUST learn delivery was incomplete rather
            # than see a success that dropped media.
            return DeliveryReceipt.failure(
                f"only {sent} of {len(attachments)} attachment(s) delivered"
            )
        return DeliveryReceipt.success(message_id=first_id, status=DeliveryStatus.SENT)

    async def _dispatch_attachment(
        self, chat_id: int, att: dict[str, Any], data: bytes, caption: str | None
    ) -> str | None:
        """Dispatch already-resolved attachment bytes to the right Bot API send.

        Returns the message id on delivery, or ``None`` when the item did not go out.
        `_sent_id`, not `_msg_id`: a rejected send returns None without raising, and
        `_msg_id` turns that into ``""`` — which the caller's `if mid is not None`
        counts as delivered, so `sent == len(attachments)` and a batch the Bot API
        refused is reported as a full success. That is the precise outcome the
        caller's docstring promises cannot happen.
        """
        kind = (att.get("kind") or "").lower()
        filename = att.get("filename") or "file"
        bot = self._bot
        try:
            if kind == "photo" and hasattr(bot, "send_photo"):
                return _sent_id(await bot.send_photo(chat_id, data, caption=caption))
            if kind == "video" and hasattr(bot, "send_video"):
                return _sent_id(await bot.send_video(chat_id, data, caption=caption))
            if kind == "animation" and hasattr(bot, "send_animation"):
                return _sent_id(await bot.send_animation(chat_id, data, caption=caption))
            if kind == "voice" and hasattr(bot, "send_voice"):
                return _sent_id(await bot.send_voice(chat_id, data))
            # audio / document / anything else → document
            if hasattr(bot, "send_document"):
                return _sent_id(
                    await bot.send_document(chat_id, data, filename=filename, caption=caption)
                )
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
