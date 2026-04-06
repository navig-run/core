"""
SMS Adapter — Twilio-first, Vonage fallback.

Compliance: **official** — uses provider REST APIs exclusively.
Identity:   **business** — messages come from a registered sender number.

Config (``adapters.sms`` section)::

    adapters:
      sms:
        enabled: true
        provider: twilio          # twilio | vonage
        twilio:
          account_sid: vault:twilio_account_sid
          auth_token: vault:twilio_auth_token
          from_number: "+1234567890"
        vonage:
          api_key: vault:vonage_api_key
          api_secret: vault:vonage_api_secret
          from_number: "+1234567890"
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


class SmsAdapter:
    """
    SMS transport adapter (Twilio / Vonage).

    Satisfies the :class:`~navig.messaging.adapter.ChannelAdapter` protocol.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._provider = self._config.get("provider", "twilio")
        self._from_number = self._resolve_from_number()
        self._client: Any = None

    # ── Protocol properties ───────────────────────────────────

    @property
    def name(self) -> str:
        return "sms"

    @property
    def capabilities(self) -> list[str]:
        return ["text"]

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
        """Send an SMS to the phone number encoded in ``thread_id``."""
        to_number = thread_id  # thread_id == phone number for SMS
        try:
            client = self._get_client()
            if self._provider == "twilio":
                msg = client.messages.create(
                    body=text,
                    from_=self._from_number,
                    to=to_number,
                )
                return DeliveryReceipt.success(
                    message_id=msg.sid,
                    status=DeliveryStatus.SENT,
                )
            elif self._provider == "vonage":
                resp = client.sms.send_message(
                    {
                        "from": self._from_number,
                        "to": to_number.lstrip("+"),
                        "text": text,
                    }
                )
                msg_data = resp["messages"][0]
                if msg_data["status"] == "0":
                    return DeliveryReceipt.success(
                        message_id=msg_data.get("message-id"),
                        status=DeliveryStatus.SENT,
                    )
                return DeliveryReceipt.failure(
                    f"Vonage error: {msg_data.get('error-text', 'unknown')}"
                )
            else:
                return DeliveryReceipt.failure(f"Unknown SMS provider: {self._provider}")
        except Exception as exc:
            logger.error("sms_send_failed | to=%s | error=%s", to_number, exc)
            return DeliveryReceipt.failure(str(exc))

    # ── Resolve ───────────────────────────────────────────────

    def resolve_target(self, route: str) -> ResolvedTarget:
        """Parse ``sms:+33612345678`` into a target."""
        if ":" in route:
            _, _, address = route.partition(":")
        else:
            address = route
        address = address.strip()
        return ResolvedTarget(adapter="sms", address=address)

    async def get_or_create_thread(self, route: str) -> Thread:
        """SMS threads are keyed by phone number."""
        target = self.resolve_target(route)
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        return store.get_or_create("sms", target.address)

    # ── Inbound ───────────────────────────────────────────────

    async def receive_webhook(self, payload: dict[str, Any]) -> InboundEvent:
        """Parse a Twilio/Vonage inbound webhook."""
        if self._provider == "twilio":
            return InboundEvent(
                adapter="sms",
                remote_conversation_id=payload.get("From", ""),
                sender=payload.get("From", ""),
                text=payload.get("Body", ""),
                raw=payload,
            )
        # Vonage
        return InboundEvent(
            adapter="sms",
            remote_conversation_id=payload.get("msisdn", ""),
            sender=payload.get("msisdn", ""),
            text=payload.get("text", ""),
            raw=payload,
        )

    async def ingest_event(self, event: InboundEvent) -> None:
        """Process an inbound SMS — update thread, notify operator."""
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        thread = store.get_or_create("sms", event.remote_conversation_id)
        store.touch(thread.id)
        logger.info(
            "sms_inbound | from=%s | thread=%d | len=%d",
            event.sender,
            thread.id,
            len(event.text),
        )

    # ── Internal ──────────────────────────────────────────────

    def _get_client(self) -> Any:
        """Lazy-init the provider SDK client."""
        if self._client is not None:
            return self._client

        if self._provider == "twilio":
            try:
                from twilio.rest import Client  # type: ignore[import-untyped]
            except ImportError:
                raise ImportError("twilio package required: pip install twilio") from None
            cfg = self._config.get("twilio", {})
            self._client = Client(
                cfg.get("account_sid", ""),
                cfg.get("auth_token", ""),
            )
        elif self._provider == "vonage":
            try:
                import vonage  # type: ignore[import-untyped]
            except ImportError:
                raise ImportError("vonage package required: pip install vonage") from None
            cfg = self._config.get("vonage", {})
            self._client = vonage.Client(
                key=cfg.get("api_key", ""),
                secret=cfg.get("api_secret", ""),
            )
        else:
            raise ValueError(f"Unknown SMS provider: {self._provider}")

        return self._client

    def _resolve_from_number(self) -> str:
        """Resolve the sender phone number from config."""
        provider_cfg = self._config.get(self._provider, {})
        return provider_cfg.get("from_number", "")
