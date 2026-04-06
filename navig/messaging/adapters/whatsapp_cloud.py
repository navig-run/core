"""
WhatsApp Cloud API Adapter — Official Meta Business Platform integration.

Compliance: **official** — uses Meta Cloud API (Graph API v18+).
Identity:   **business** — messages come from a verified WABA number.

The existing ``navig.gateway.channels.whatsapp`` adapter uses whatsapp-web.js
(browser automation, **experimental**).  This adapter is the **official**
replacement for production use.

Config (``adapters.whatsapp_cloud`` section)::

    adapters:
      whatsapp_cloud:
        enabled: true
        phone_number_id: "123456789012345"
        access_token: vault:whatsapp_cloud_token
        verify_token: "my-verify-token"      # webhook verification
        api_version: "v18.0"
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

_GRAPH_API = "https://graph.facebook.com"


class WhatsAppCloudAdapter:
    """
    WhatsApp Cloud API adapter for the unified messaging layer.

    Satisfies the :class:`~navig.messaging.adapter.ChannelAdapter` protocol.
    Uses the Meta Graph API to send and receive messages via the official
    WhatsApp Business Platform.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._phone_number_id = self._config.get("phone_number_id", "")
        self._access_token = self._config.get("access_token", "")
        self._api_version = self._config.get("api_version", "v18.0")
        self._session: Any = None  # aiohttp.ClientSession (lazy)

    # ── Protocol properties ───────────────────────────────────

    @property
    def name(self) -> str:
        return "whatsapp"

    @property
    def capabilities(self) -> list[str]:
        return ["text", "media", "reactions"]

    @property
    def identity_mode(self) -> str:
        return "business"

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
        """Send a WhatsApp message via Cloud API."""
        to_number = thread_id  # thread_id == phone number for WhatsApp
        url = f"{_GRAPH_API}/{self._api_version}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": text},
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200 and "messages" in data:
                    msg_id = data["messages"][0].get("id", "")
                    return DeliveryReceipt.success(
                        message_id=msg_id,
                        status=DeliveryStatus.SENT,
                    )
                error_msg = data.get("error", {}).get("message", str(data))
                return DeliveryReceipt.failure(f"WhatsApp API error: {error_msg}")
        except Exception as exc:
            logger.error("whatsapp_cloud_send_failed | to=%s | error=%s", to_number, exc)
            return DeliveryReceipt.failure(str(exc))

    # ── Resolve ───────────────────────────────────────────────

    def resolve_target(self, route: str) -> ResolvedTarget:
        """Parse ``whatsapp:+33612345678`` into a target."""
        if ":" in route:
            _, _, address = route.partition(":")
        else:
            address = route
        address = address.strip()
        return ResolvedTarget(adapter="whatsapp_cloud", address=address)

    async def get_or_create_thread(self, route: str) -> Thread:
        """WhatsApp threads are keyed by phone number."""
        target = self.resolve_target(route)
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        return store.get_or_create("whatsapp_cloud", target.address)

    # ── Inbound (webhook) ────────────────────────────────────

    async def receive_webhook(self, payload: dict[str, Any]) -> InboundEvent:
        """
        Parse a WhatsApp Cloud API webhook notification.

        Meta sends a nested structure::

            {
              "entry": [{
                "changes": [{
                  "value": {
                    "messages": [{
                      "from": "33612345678",
                      "text": {"body": "hello"},
                      ...
                    }]
                  }
                }]
              }]
            }
        """
        try:
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [{}])
            msg = messages[0] if messages else {}

            return InboundEvent(
                adapter="whatsapp_cloud",
                remote_conversation_id=msg.get("from", ""),
                sender=msg.get("from", ""),
                text=msg.get("text", {}).get("body", ""),
                raw=payload,
            )
        except (IndexError, KeyError) as exc:
            logger.warning("whatsapp_cloud_webhook_parse_error | %s", exc)
            return InboundEvent(
                adapter="whatsapp_cloud",
                remote_conversation_id="unknown",
                sender="unknown",
                text="",
                raw=payload,
            )

    async def ingest_event(self, event: InboundEvent) -> None:
        """Process an inbound WhatsApp message."""
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        thread = store.get_or_create("whatsapp_cloud", event.remote_conversation_id)
        store.touch(thread.id)
        logger.info(
            "whatsapp_cloud_inbound | from=%s | thread=%d | len=%d",
            event.sender,
            thread.id,
            len(event.text),
        )

    # ── Internal ──────────────────────────────────────────────

    async def _get_session(self) -> Any:
        """Lazy-init aiohttp session."""
        if self._session is not None:
            return self._session
        try:
            import aiohttp
        except ImportError:
            raise ImportError("aiohttp required for WhatsApp Cloud adapter") from None
        self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
