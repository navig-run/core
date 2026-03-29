"""Messaging provider abstraction layer.

This package isolates channel-specific implementations (for now: Telegram)
behind a small provider contract so gateway/worker startup does not need to
import channel modules directly.
"""

from .provider import IMessagingProvider
from .registry import create_channel_for_provider, get_active_provider_name, is_provider_enabled

__all__ = [
    "IMessagingProvider",
    "create_channel_for_provider",
    "get_active_provider_name",
    "is_provider_enabled",
]
