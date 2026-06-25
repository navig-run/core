"""PublishDispatcher — fan a composed post out to many targets.

Routing:
- ``telegram`` targets go straight to the live ``TelegramChannel`` (text via
  ``send_message``, media via ``send_photo``/``send_video``/``send_document``),
  reusing the existing send path.
- other messaging networks (``discord``/``whatsapp``/``sms``) go through the
  messaging adapter registry's ``send_message(thread_id, text, attachments)``.
- everything else is a :class:`SocialPublisher` from the publisher registry.

Each target yields one :class:`PublishReceipt`; the dispatcher never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.social.types import PostContent, PublishReceipt

logger = logging.getLogger(__name__)

MESSAGING_NETWORKS = {"telegram", "discord", "whatsapp", "sms"}


class PublishDispatcher:
    def __init__(
        self,
        *,
        telegram_channel: Any | None = None,
        adapter_registry: Any | None = None,
        publisher_registry: Any | None = None,
    ) -> None:
        self._tg = telegram_channel
        self._adapters = adapter_registry
        self._publishers = publisher_registry

    def _adapter_reg(self):
        if self._adapters is None:
            from navig.messaging.adapter_registry import get_adapter_registry

            self._adapters = get_adapter_registry()
        return self._adapters

    def _publisher_reg(self):
        if self._publishers is None:
            from navig.social.registry import get_publisher_registry

            self._publishers = get_publisher_registry()
        return self._publishers

    async def publish(self, content: PostContent, targets: list[dict[str, Any]]) -> list[PublishReceipt]:
        """Publish *content* to each ``{network, target}`` in *targets*."""
        receipts: list[PublishReceipt] = []
        for t in targets:
            network = (t.get("network") or "").strip()
            addr = (t.get("target") or "").strip()
            if not network:
                continue
            try:
                receipts.append(await self._publish_one(network, addr, content))
            except Exception as exc:  # noqa: BLE001
                logger.exception("publish to %s failed", network)
                receipts.append(PublishReceipt.failure(network, addr, str(exc)))
        return receipts

    async def _publish_one(self, network: str, addr: str, content: PostContent) -> PublishReceipt:
        rendered = content.render(network)
        if network == "telegram" and self._tg is not None:
            return await self._publish_telegram(addr, rendered)
        if network in MESSAGING_NETWORKS:
            return await self._publish_messaging(network, addr, rendered)
        publisher = self._publisher_reg().get(network)
        if publisher is None:
            return PublishReceipt.failure(network, addr, f"unknown network '{network}'")
        return await publisher.publish(addr, rendered)

    async def _publish_telegram(self, addr: str, rendered) -> PublishReceipt:
        try:
            chat_id = int(addr)
        except (TypeError, ValueError):
            return PublishReceipt.failure("telegram", addr, "target must be a chat id")
        bot = self._tg
        try:
            if rendered.media:
                m = rendered.media[0]
                data = await _media_bytes(m, getattr(bot, "_session", None))
                kind = (m.get("kind") or "").lower()
                if data is not None and kind == "photo" and hasattr(bot, "send_photo"):
                    msg = await bot.send_photo(chat_id, data, caption=rendered.text[:1024] or None)
                elif data is not None and kind == "video" and hasattr(bot, "send_video"):
                    msg = await bot.send_video(chat_id, data, caption=rendered.text[:1024] or None)
                elif data is not None and hasattr(bot, "send_document"):
                    msg = await bot.send_document(chat_id, data, filename=m.get("filename") or "file",
                                                  caption=rendered.text[:1024] or None)
                else:
                    msg = await bot.send_message(chat_id, rendered.text)
            else:
                msg = await bot.send_message(chat_id, rendered.text)
            mid = msg.get("message_id") if isinstance(msg, dict) else getattr(msg, "message_id", None)
            return PublishReceipt.success("telegram", addr, id=str(mid) if mid else None)
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure("telegram", addr, str(exc))

    async def _publish_messaging(self, network: str, addr: str, rendered) -> PublishReceipt:
        adapter = self._adapter_reg().get(network)
        if adapter is None:
            return PublishReceipt.failure(network, addr, f"{network} adapter not registered")
        try:
            receipt = await adapter.send_message(addr, rendered.text, rendered.media or None)
            if getattr(receipt, "ok", False):
                return PublishReceipt.success(network, addr, id=getattr(receipt, "message_id", None))
            return PublishReceipt.failure(network, addr, getattr(receipt, "error", None) or "send failed")
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(network, addr, str(exc))


async def _media_bytes(att: dict[str, Any], session: Any) -> bytes | None:
    from navig.messaging.attachments import attachment_bytes

    return await attachment_bytes(att, session)
