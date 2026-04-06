"""
Discord Messaging Adapter — Wraps the existing DiscordChannelAdapter for outbound sends.

Compliance: **official** — uses discord.py bot (official Bot API).
Identity:   **bot** — messages come from the NAVIG bot account.

Reuses :class:`~navig.gateway.channels.discord.DiscordChannelAdapter` for
the underlying connection and ``discord.py`` client.  This adapter adds the
:class:`~navig.messaging.adapter.ChannelAdapter` protocol surface for
deterministic routing and delivery tracking.

Config (``adapters.discord`` section)::

    adapters:
      discord:
        enabled: true
        bot_token: vault:discord_bot_token
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

try:
    import discord as _discord

    DISCORD_AVAILABLE = True
except ImportError:
    _discord = None  # type: ignore[assignment]
    DISCORD_AVAILABLE = False


class DiscordMessagingAdapter:
    """
    Discord messaging adapter for the unified messaging layer.

    Satisfies the :class:`~navig.messaging.adapter.ChannelAdapter` protocol.

    ``thread_id`` is a Discord channel or DM channel ID (snowflake string).
    To send to a user, resolve their DM channel first via ``get_or_create_thread``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._bot_token = self._config.get("bot_token", "")
        self._client: Any = None

    # ── Protocol properties ───────────────────────────────────

    @property
    def name(self) -> str:
        return "discord"

    @property
    def capabilities(self) -> list[str]:
        return ["text", "media", "reactions", "threads"]

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
        """Send a message to a Discord channel or DM."""
        if not DISCORD_AVAILABLE:
            return DeliveryReceipt.failure("discord.py not installed")

        try:
            client = self._get_client()
            channel = client.get_channel(int(thread_id))
            if channel is None:
                # Try fetching (may be a DM or uncached channel)
                channel = await client.fetch_channel(int(thread_id))

            if channel is None:
                return DeliveryReceipt.failure(f"Discord channel {thread_id} not found")

            msg = await channel.send(text)
            return DeliveryReceipt.success(
                message_id=str(msg.id),
                status=DeliveryStatus.SENT,
            )
        except Exception as exc:
            logger.error("discord_send_failed | channel=%s | error=%s", thread_id, exc)
            return DeliveryReceipt.failure(str(exc))

    # ── Resolve ───────────────────────────────────────────────

    def resolve_target(self, route: str) -> ResolvedTarget:
        """Parse ``discord:<channel_or_user_id>`` into a target."""
        if ":" in route:
            _, _, address = route.partition(":")
        else:
            address = route
        address = address.strip()
        return ResolvedTarget(adapter="discord", address=address)

    async def get_or_create_thread(self, route: str) -> Thread:
        """
        Get or create a thread.

        If the address is a user ID, open a DM channel first.
        """
        target = self.resolve_target(route)
        address = target.address

        if DISCORD_AVAILABLE and self._client:
            try:
                # Try as user → open DM
                user = await self._client.fetch_user(int(address))
                dm = await user.create_dm()
                address = str(dm.id)
            except Exception:
                pass  # Not a user ID, treat as channel ID

        from navig.store.threads import get_thread_store

        store = get_thread_store()
        return store.get_or_create("discord", address)

    # ── Inbound ───────────────────────────────────────────────

    async def receive_webhook(self, payload: dict[str, Any]) -> InboundEvent:
        """Parse an inbound event from the existing Discord gateway adapter."""
        return InboundEvent(
            adapter="discord",
            remote_conversation_id=str(payload.get("channel_id", "")),
            sender=str(payload.get("author_id", "")),
            text=payload.get("content", ""),
            raw=payload,
        )

    async def ingest_event(self, event: InboundEvent) -> None:
        """Process an inbound Discord message."""
        from navig.store.threads import get_thread_store

        store = get_thread_store()
        thread = store.get_or_create("discord", event.remote_conversation_id)
        store.touch(thread.id)
        logger.info(
            "discord_inbound | from=%s | channel=%s | thread=%d",
            event.sender,
            event.remote_conversation_id,
            thread.id,
        )

    # ── Internal ──────────────────────────────────────────────

    def _get_client(self) -> Any:
        """Return the discord.py Client (lazy init not possible in async context)."""
        if self._client is not None:
            return self._client
        raise RuntimeError(
            "Discord client not initialised. "
            "Call set_client() with the running discord.Client instance."
        )

    def set_client(self, client: Any) -> None:
        """Inject the running discord.py Client from the gateway adapter."""
        self._client = client
