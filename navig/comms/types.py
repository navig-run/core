"""Unified comms types — channel-agnostic notification primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

# ---- Channel selection --------------------------------------------------

CommsChannel = Literal["telegram", "matrix", "both", "none", "auto"]
"""Which channel(s) a notification should be delivered on."""


# ---- Delivery target ----------------------------------------------------


@dataclass
class NotificationTarget:
    """Where to send the notification.

    Exactly one of ``telegram_chat_id`` or ``matrix_room_id`` must be set
    (or both, when channel="both").  When channel="auto", the dispatcher
    resolves the concrete IDs from the identity store.
    """

    telegram_chat_id: int | None = None
    matrix_room_id: str | None = None
    user_id: str | None = None  # opaque identity key for "auto"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def telegram(cls, chat_id: int) -> NotificationTarget:
        return cls(telegram_chat_id=chat_id)

    @classmethod
    def matrix(cls, room_id: str) -> NotificationTarget:
        return cls(matrix_room_id=room_id)

    @classmethod
    def auto(cls, user_id: str) -> NotificationTarget:
        return cls(user_id=user_id)


# ---- Delivery options ---------------------------------------------------


class DeliveryPriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class NotificationOptions:
    """Knobs for the send path."""

    priority: DeliveryPriority = DeliveryPriority.NORMAL
    silent: bool = False  # Telegram silent / Matrix low-priority
    ttl_seconds: int = 0  # 0 = forever
    retry_count: int = 2  # max retries on transient failure
    parse_mode: str = "HTML"  # Telegram parse mode
    metadata: dict[str, Any] = field(default_factory=dict)


# ---- Delivery result ----------------------------------------------------


@dataclass
class DeliveryResult:
    """Outcome of a notification dispatch."""

    ok: bool
    channel: str  # which channel actually delivered
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, channel: str, message_id: str | None = None) -> DeliveryResult:
        return cls(ok=True, channel=channel, message_id=message_id)

    @classmethod
    def failure(cls, channel: str, error: str) -> DeliveryResult:
        return cls(ok=False, channel=channel, error=error)


# ---- Fan-out result (for "both") ----------------------------------------


@dataclass
class FanoutResult:
    """Aggregated results when sending to multiple channels."""

    results: list[DeliveryResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def any_ok(self) -> bool:
        return any(r.ok for r in self.results)
