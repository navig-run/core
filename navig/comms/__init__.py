"""
NAVIG Unified Communications Layer

Provides channel-agnostic notification dispatch:
  - ``send_user_notification()`` — single function for all channels
  - ``CommsChannel`` — type alias for channel selection
  - ``NotificationTarget`` — where to deliver
  - ``NotificationOptions`` — delivery options (retry, ttl, silent, etc.)

Supported channels:
  - "telegram" — wraps existing TelegramNotifier (default)
  - "matrix"   — optional Matrix homeserver (requires navig.comms.matrix)
  - "both"     — fan-out to telegram + matrix
  - "none"     — no-op, useful for tests and dry-runs
  - "auto"     — resolves to user's preferred channel from identity store
"""

from navig.comms.dispatch import send_user_notification
from navig.comms.types import (
    CommsChannel,
    DeliveryResult,
    NotificationOptions,
    NotificationTarget,
)

__all__ = [
    "CommsChannel",
    "NotificationTarget",
    "NotificationOptions",
    "DeliveryResult",
    "send_user_notification",
]
