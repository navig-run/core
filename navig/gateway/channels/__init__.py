"""
NAVIG Gateway Channels Package

Channel adapters for multi-platform messaging.

Inspired by channel registry patterns.

Supported Channels:
- Telegram (Bot API)
- WhatsApp (whatsapp-web.js)
- Discord (Bot API)
- Slack (Socket Mode) - planned
- Signal (signal-cli) - planned
- iMessage (BlueBubbles) - planned
- Google Chat (Chat API) - planned
"""

from typing import TYPE_CHECKING

# Lazy imports to avoid dependency issues
_discord_channel = None
_whatsapp_channel = None
_telegram_channel = None
_matrix_channel = None


def get_matrix_channel():
    """Get Matrix channel module (lazy load)."""
    global _matrix_channel
    if _matrix_channel is None:
        try:
            from . import matrix as _matrix_channel
        except ImportError:
            _matrix_channel = None
    return _matrix_channel


def is_matrix_available() -> bool:
    """Check if Matrix integration is available."""
    try:
        import nio

        return True
    except ImportError:
        return False


def get_discord_channel():
    """Get Discord channel module (lazy load)."""
    global _discord_channel
    if _discord_channel is None:
        try:
            from . import discord as _discord_channel
        except ImportError:
            _discord_channel = None
    return _discord_channel


def get_whatsapp_channel():
    """Get WhatsApp channel module (lazy load)."""
    global _whatsapp_channel
    if _whatsapp_channel is None:
        try:
            from . import whatsapp as _whatsapp_channel
        except ImportError:
            _whatsapp_channel = None
    return _whatsapp_channel


def get_telegram_channel():
    """Get Telegram channel module (lazy load)."""
    global _telegram_channel
    if _telegram_channel is None:
        try:
            from . import telegram as _telegram_channel
        except ImportError:
            _telegram_channel = None
    return _telegram_channel


def is_discord_available() -> bool:
    """Check if Discord integration is available."""
    try:
        import discord

        return True
    except ImportError:
        return False


def is_whatsapp_available() -> bool:
    """Check if WhatsApp integration is available."""
    try:
        import aiohttp

        return True
    except ImportError:
        return False


def is_telegram_available() -> bool:
    """Check if Telegram integration is available."""
    try:
        import aiohttp

        return True
    except ImportError:
        return False


# Registry exports (new agent-inspired pattern)
def get_channel_registry():
    """Get the channel registry for unified channel management."""
    from .registry import get_channel_registry as _get_registry

    return _get_registry()


def list_channels(available_only: bool = False):
    """List registered channels."""
    from .registry import list_channels as _list

    return _list(available_only)


def get_channel(channel_id: str):
    """Get channel by ID or alias."""
    from .registry import get_channel as _get

    return _get(channel_id)


__all__ = [
    # Legacy adapters
    "get_discord_channel",
    "get_whatsapp_channel",
    "get_telegram_channel",
    "get_matrix_channel",
    "is_discord_available",
    "is_whatsapp_available",
    "is_telegram_available",
    "is_matrix_available",
    # New registry pattern
    "get_channel_registry",
    "list_channels",
    "get_channel",
]
