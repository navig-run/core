"""
ChannelAdapter protocol and messaging types for the unified messaging layer.

Defines the typed contract that every transport adapter must satisfy,
plus shared value objects for delivery receipts, routing targets,
inbound events, and delivery status tracking.

Sits above :class:`IMessagingProvider` (which handles gateway-boot channel
creation).  ``ChannelAdapter`` handles **runtime message I/O** after boot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

# ── Enums ─────────────────────────────────────────────────────


class DeliveryStatus(str, Enum):
    """Lifecycle states for an outbound message."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"

    # Allowed transitions (only forward or to FAILED).
    def can_transition_to(self, target: DeliveryStatus) -> bool:
        _ORDER = {
            DeliveryStatus.QUEUED: 0,
            DeliveryStatus.SENT: 1,
            DeliveryStatus.DELIVERED: 2,
            DeliveryStatus.READ: 3,
            DeliveryStatus.FAILED: 99,
        }
        if target is DeliveryStatus.FAILED:
            return True
        return _ORDER.get(target, -1) > _ORDER.get(self, -1)


class ComplianceMode(str, Enum):
    """Adapter compliance classification."""

    OFFICIAL = "official"
    EXPERIMENTAL = "experimental"
    DISABLED = "disabled"


class IdentityMode(str, Enum):
    """How the adapter presents on the remote network."""

    BOT = "bot"
    BUSINESS = "business"
    BRIDGE_USER = "bridge_user"


# ── Value Objects ─────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedTarget:
    """A resolved delivery target on a specific network."""

    adapter: str  # e.g. "whatsapp", "discord"
    address: str  # e.g. "+33612345678", "123456789"
    display_hint: str = ""  # human-friendly label for UX


@dataclass(frozen=True)
class DeliveryReceipt:
    """Result of an outbound send attempt."""

    ok: bool
    message_id: str | None = None
    timestamp: float = 0.0
    status: DeliveryStatus = DeliveryStatus.SENT
    error: str | None = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            object.__setattr__(self, "timestamp", time.time())

    @classmethod
    def success(
        cls,
        message_id: str | None = None,
        status: DeliveryStatus = DeliveryStatus.SENT,
    ) -> DeliveryReceipt:
        return cls(ok=True, message_id=message_id, status=status)

    @classmethod
    def failure(cls, error: str) -> DeliveryReceipt:
        return cls(ok=False, status=DeliveryStatus.FAILED, error=error)


@dataclass(frozen=True)
class InboundEvent:
    """A message or event received from a remote network."""

    adapter: str
    remote_conversation_id: str
    sender: str
    text: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            object.__setattr__(self, "timestamp", time.time())


@dataclass
class Thread:
    """A NAVIG-local conversation thread bound to one adapter + remote ID."""

    id: int
    adapter: str
    remote_conversation_id: str
    contact_alias: str | None = None
    status: str = "open"
    created_at: str = ""
    last_active: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    """A single transport route for a contact."""

    network: str  # e.g. "whatsapp", "discord", "sms"
    address: str  # e.g. "+33612345678", "123456789"
    priority: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Contact:
    """Resolved contact with ordered routes and fallbacks."""

    alias: str
    display_name: str
    default_network: str | None = None
    routes: list[Route] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class RoutingDecision:
    """Result of the routing engine's deterministic resolution."""

    adapter_name: str
    resolved_target: ResolvedTarget
    compliance_mode: ComplianceMode
    thread: Thread | None = None


# ── ChannelAdapter Protocol ───────────────────────────────────


@runtime_checkable
class ChannelAdapter(Protocol):
    """
    Contract for a transport adapter in the unified messaging layer.

    Every network (SMS, WhatsApp, Discord, etc.) implements this protocol
    to participate in deterministic routing and delivery tracking.
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. ``"whatsapp"``, ``"discord"``, ``"sms"``."""
        ...

    @property
    def capabilities(self) -> list[str]:
        """e.g. ``["text", "media", "reactions"]``."""
        ...

    @property
    def identity_mode(self) -> Literal["bot", "business", "bridge_user"]:
        """How this adapter presents on the remote network."""
        ...

    @property
    def compliance(self) -> Literal["official", "experimental", "disabled"]:
        """Compliance classification — logged on every outbound send."""
        ...

    async def send_message(
        self,
        thread_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> DeliveryReceipt:
        """Send an outbound message and return a delivery receipt."""
        ...

    def resolve_target(self, route: str) -> ResolvedTarget:
        """Parse a route string (e.g. ``sms:+33612345678``) into a target."""
        ...

    async def get_or_create_thread(self, route: str) -> Thread:
        """Look up or create a conversation thread for the given route."""
        ...

    async def receive_webhook(self, payload: dict[str, Any]) -> InboundEvent:
        """Parse an inbound webhook payload into an ``InboundEvent``."""
        ...

    async def ingest_event(self, event: InboundEvent) -> None:
        """Process an inbound event (update thread, notify operator, etc.)."""
        ...
