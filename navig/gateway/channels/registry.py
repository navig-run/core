"""
Channel Registry for NAVIG Gateway

Provides a unified registry for messaging channels (Telegram, WhatsApp, Discord, etc.),
inspired by modular channel registry patterns.

Features:
- Unified channel discovery and metadata
- Channel capability detection
- Alias support for channel names
- Plugin-based channel loading
- Status and health checking

Usage:
    from navig.gateway.channels.registry import (
        get_channel_registry,
        ChannelId,
        ChannelMeta,
    )

    registry = get_channel_registry()

    # List available channels
    for channel in registry.list_channels():
        print(f"{channel.label}: {channel.status}")

    # Get specific channel
    telegram = registry.get_channel("telegram")
    if telegram.is_available():
        adapter = telegram.get_adapter()
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# =============================================================================
# Types
# =============================================================================


class ChannelId(str, Enum):
    """Supported messaging channel IDs."""

    TELEGRAM = "telegram"
    MATRIX = "matrix"
    WHATSAPP = "whatsapp"
    DISCORD = "discord"
    SLACK = "slack"
    SIGNAL = "signal"
    IMESSAGE = "imessage"
    GOOGLECHAT = "googlechat"
    SMS = "sms"
    MESSENGER = "messenger"  # Facebook Messenger
    CLI = "cli"  # Local CLI channel
    WEB = "web"  # Web interface


class ChannelStatus(str, Enum):
    """Channel status states."""

    AVAILABLE = "available"  # Ready to use
    UNAVAILABLE = "unavailable"  # Dependencies missing
    CONFIGURED = "configured"  # Has valid config
    CONNECTED = "connected"  # Actively connected
    DISCONNECTED = "disconnected"  # Was connected, now disconnected
    ERROR = "error"  # Error state


class ChannelCapability(str, Enum):
    """Channel capabilities."""

    TEXT = "text"  # Text messages
    VOICE = "voice"  # Voice messages
    IMAGES = "images"  # Image attachments
    FILES = "files"  # File attachments
    REACTIONS = "reactions"  # Message reactions
    THREADS = "threads"  # Threaded replies
    BUTTONS = "buttons"  # Interactive buttons
    GROUPS = "groups"  # Group chats
    DMS = "dms"  # Direct messages
    WEBHOOKS = "webhooks"  # Webhook support
    TYPING = "typing"  # Typing indicators
    E2EE = "e2ee"  # End-to-end encryption
    MENTIONS = "mentions"  # User mentions
    MEDIA = "media"  # Rich media (audio, video)


# =============================================================================
# Channel Metadata
# =============================================================================


@dataclass
class ChannelMeta:
    """Metadata for a messaging channel."""

    id: ChannelId
    label: str
    description: str
    docs_url: str | None = None
    icon: str = "💬"

    # Capabilities
    capabilities: list[ChannelCapability] = field(default_factory=list)

    # Status
    status: ChannelStatus = ChannelStatus.UNAVAILABLE
    status_message: str | None = None

    # Config requirements
    required_config: list[str] = field(default_factory=list)
    optional_config: list[str] = field(default_factory=list)

    # Module info
    module_path: str | None = None
    adapter_class: str | None = None

    def is_available(self) -> bool:
        """Check if channel is available for use."""
        return self.status in (
            ChannelStatus.AVAILABLE,
            ChannelStatus.CONFIGURED,
            ChannelStatus.CONNECTED,
        )

    def has_capability(self, cap: ChannelCapability) -> bool:
        """Check if channel has a specific capability."""
        return cap in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id.value,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "status": self.status.value,
            "status_message": self.status_message,
            "capabilities": [c.value for c in self.capabilities],
            "available": self.is_available(),
        }


# =============================================================================
# Channel Definitions
# =============================================================================

# Default channel order (for display)
CHANNEL_ORDER = [
    ChannelId.TELEGRAM,
    ChannelId.MATRIX,
    ChannelId.WHATSAPP,
    ChannelId.DISCORD,
    ChannelId.SLACK,
    ChannelId.SIGNAL,
    ChannelId.SMS,
    ChannelId.MESSENGER,
    ChannelId.IMESSAGE,
    ChannelId.GOOGLECHAT,
    ChannelId.WEB,
    ChannelId.CLI,
]

# Channel aliases for flexible lookup
CHANNEL_ALIASES: dict[str, ChannelId] = {
    "tg": ChannelId.TELEGRAM,
    "mx": ChannelId.MATRIX,
    "wa": ChannelId.WHATSAPP,
    "imsg": ChannelId.IMESSAGE,
    "gchat": ChannelId.GOOGLECHAT,
    "google-chat": ChannelId.GOOGLECHAT,
    "txt": ChannelId.SMS,
    "text": ChannelId.SMS,
    "fb": ChannelId.MESSENGER,
    "fbm": ChannelId.MESSENGER,
    "facebook": ChannelId.MESSENGER,
}

# Default channel metadata
DEFAULT_CHANNEL_META: dict[ChannelId, ChannelMeta] = {
    ChannelId.MATRIX: ChannelMeta(
        id=ChannelId.MATRIX,
        label="Matrix",
        description="Matrix protocol via matrix-nio (Conduit/Synapse/Dendrite)",
        icon="🔗",
        docs_url="/docs/channels/matrix",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.REACTIONS,
            ChannelCapability.THREADS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
            ChannelCapability.E2EE,
            ChannelCapability.MENTIONS,
            ChannelCapability.MEDIA,
        ],
        required_config=["homeserver_url", "user_id"],
        module_path="navig.gateway.channels.matrix",
        adapter_class="MatrixChannelAdapter",
    ),
    ChannelId.TELEGRAM: ChannelMeta(
        id=ChannelId.TELEGRAM,
        label="Telegram",
        description="Telegram Bot API - simple setup with @BotFather",
        icon="📱",
        docs_url="/docs/channels/telegram",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.VOICE,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.REACTIONS,
            ChannelCapability.BUTTONS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["telegram_bot_token"],
        module_path="navig.gateway.channels.telegram",
        adapter_class="TelegramChannelAdapter",
    ),
    ChannelId.WHATSAPP: ChannelMeta(
        id=ChannelId.WHATSAPP,
        label="WhatsApp",
        description="WhatsApp Web via whatsapp-web.js",
        icon="📲",
        docs_url="/docs/channels/whatsapp",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.VOICE,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["whatsapp_session_path"],
        module_path="navig.gateway.channels.whatsapp",
        adapter_class="WhatsAppChannelAdapter",
    ),
    ChannelId.DISCORD: ChannelMeta(
        id=ChannelId.DISCORD,
        label="Discord",
        description="Discord Bot API with slash commands",
        icon="🎮",
        docs_url="/docs/channels/discord",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.REACTIONS,
            ChannelCapability.THREADS,
            ChannelCapability.BUTTONS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["discord_bot_token"],
        module_path="navig.gateway.channels.discord",
        adapter_class="DiscordChannelAdapter",
    ),
    ChannelId.SLACK: ChannelMeta(
        id=ChannelId.SLACK,
        label="Slack",
        description="Slack Socket Mode bot",
        icon="💼",
        docs_url="/docs/channels/slack",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.REACTIONS,
            ChannelCapability.THREADS,
            ChannelCapability.BUTTONS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["slack_bot_token", "slack_app_token"],
        status=ChannelStatus.UNAVAILABLE,
        status_message="Slack adapter not yet implemented",
    ),
    ChannelId.SIGNAL: ChannelMeta(
        id=ChannelId.SIGNAL,
        label="Signal",
        description="Signal via signal-cli REST API",
        icon="🔐",
        docs_url="/docs/channels/signal",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["signal_cli_url"],
        status=ChannelStatus.UNAVAILABLE,
        status_message="Signal adapter not yet implemented",
    ),
    ChannelId.IMESSAGE: ChannelMeta(
        id=ChannelId.IMESSAGE,
        label="iMessage",
        description="iMessage via BlueBubbles/macOS (beta)",
        icon="🍎",
        docs_url="/docs/channels/imessage",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.REACTIONS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["imessage_server_url"],
        status=ChannelStatus.UNAVAILABLE,
        status_message="iMessage adapter not yet implemented",
    ),
    ChannelId.GOOGLECHAT: ChannelMeta(
        id=ChannelId.GOOGLECHAT,
        label="Google Chat",
        description="Google Workspace Chat API",
        icon="💬",
        docs_url="/docs/channels/googlechat",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.THREADS,
            ChannelCapability.GROUPS,
            ChannelCapability.DMS,
        ],
        required_config=["google_chat_credentials"],
        status=ChannelStatus.UNAVAILABLE,
        status_message="Google Chat adapter not yet implemented",
    ),
    ChannelId.WEB: ChannelMeta(
        id=ChannelId.WEB,
        label="Web UI",
        description="Browser-based chat interface",
        icon="🌐",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.BUTTONS,
        ],
        status=ChannelStatus.AVAILABLE,
    ),
    ChannelId.CLI: ChannelMeta(
        id=ChannelId.CLI,
        label="CLI",
        description="Local command-line interface",
        icon="⌨️",
        capabilities=[
            ChannelCapability.TEXT,
        ],
        status=ChannelStatus.AVAILABLE,
    ),
    ChannelId.SMS: ChannelMeta(
        id=ChannelId.SMS,
        label="SMS",
        description="SMS via Twilio / Vonage / gateway API",
        icon="📟",
        docs_url="/docs/channels/sms",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.DMS,
        ],
        required_config=["sms_provider", "sms_api_key"],
        module_path="navig.messaging.adapters.sms",
        adapter_class="SmsAdapter",
        status=ChannelStatus.UNAVAILABLE,
        status_message="SMS adapter requires provider configuration",
    ),
    ChannelId.MESSENGER: ChannelMeta(
        id=ChannelId.MESSENGER,
        label="Messenger",
        description="Facebook Messenger via Graph API",
        icon="💬",
        docs_url="/docs/channels/messenger",
        capabilities=[
            ChannelCapability.TEXT,
            ChannelCapability.IMAGES,
            ChannelCapability.FILES,
            ChannelCapability.BUTTONS,
            ChannelCapability.DMS,
        ],
        required_config=["messenger_page_token", "messenger_app_secret"],
        status=ChannelStatus.UNAVAILABLE,
        status_message="Messenger adapter not yet implemented",
    ),
}


# =============================================================================
# Channel Registry
# =============================================================================


class ChannelRegistry:
    """
    Registry for messaging channel adapters.

    Manages channel discovery, metadata, and adapter loading.
    """

    def __init__(self):
        self._channels: dict[ChannelId, ChannelMeta] = {}
        self._adapters: dict[ChannelId, Any] = {}
        self._initialized = False

    def initialize(self) -> None:
        """Initialize registry with default channels."""
        if self._initialized:
            return

        # Load default channel metadata
        for channel_id, meta in DEFAULT_CHANNEL_META.items():
            self._channels[channel_id] = meta

        # Check availability of each channel
        self._check_channel_availability()

        self._initialized = True

    def _check_channel_availability(self) -> None:
        """Check which channels are available based on dependencies."""
        for _channel_id, meta in self._channels.items():
            if meta.module_path:
                try:
                    # Try to import the module
                    __import__(meta.module_path)
                    if meta.status == ChannelStatus.UNAVAILABLE:
                        meta.status = ChannelStatus.AVAILABLE
                        meta.status_message = None
                except ImportError as e:
                    meta.status = ChannelStatus.UNAVAILABLE
                    meta.status_message = f"Module not available: {e}"

    def get_channel(self, channel_id: str | ChannelId) -> ChannelMeta | None:
        """
        Get channel metadata by ID or alias.

        Args:
            channel_id: Channel ID or alias string

        Returns:
            ChannelMeta or None if not found
        """
        if not self._initialized:
            self.initialize()

        # Normalize to ChannelId
        if isinstance(channel_id, str):
            channel_id = self.normalize_channel_id(channel_id)
            if channel_id is None:
                return None

        return self._channels.get(channel_id)

    def normalize_channel_id(self, raw: str) -> ChannelId | None:
        """
        Normalize a channel name/alias to ChannelId.

        Args:
            raw: Raw channel name or alias

        Returns:
            Normalized ChannelId or None if invalid
        """
        key = raw.strip().lower()

        # Check aliases first
        if key in CHANNEL_ALIASES:
            return CHANNEL_ALIASES[key]

        # Try direct match
        try:
            return ChannelId(key)
        except ValueError:
            pass  # malformed value; skip

        return None

    def list_channels(self, available_only: bool = False) -> list[ChannelMeta]:
        """
        List all registered channels.

        Args:
            available_only: If True, only return available channels

        Returns:
            List of ChannelMeta in display order
        """
        if not self._initialized:
            self.initialize()

        channels = []
        for channel_id in CHANNEL_ORDER:
            if channel_id in self._channels:
                meta = self._channels[channel_id]
                if not available_only or meta.is_available():
                    channels.append(meta)

        return channels

    def list_available_channels(self) -> list[ChannelMeta]:
        """List only available channels."""
        return self.list_channels(available_only=True)

    def get_adapter(self, channel_id: str | ChannelId) -> Any | None:
        """
        Get or load channel adapter.

        Args:
            channel_id: Channel ID

        Returns:
            Adapter instance or None
        """
        if isinstance(channel_id, str):
            channel_id = self.normalize_channel_id(channel_id)
            if channel_id is None:
                return None

        # Check cache
        if channel_id in self._adapters:
            return self._adapters[channel_id]

        # Load adapter
        meta = self.get_channel(channel_id)
        if not meta or not meta.module_path or not meta.adapter_class:
            return None

        try:
            import importlib

            module = importlib.import_module(meta.module_path)
            adapter_cls = getattr(module, meta.adapter_class)
            adapter = adapter_cls()
            self._adapters[channel_id] = adapter
            return adapter
        except (ImportError, AttributeError) as e:
            meta.status = ChannelStatus.ERROR
            meta.status_message = str(e)
            return None

    def register_channel(self, meta: ChannelMeta, adapter: Any | None = None) -> None:
        """
        Register a custom channel.

        Args:
            meta: Channel metadata
            adapter: Optional pre-created adapter instance
        """
        self._channels[meta.id] = meta
        if adapter:
            self._adapters[meta.id] = adapter

    def get_status_summary(self) -> dict[str, Any]:
        """Get summary of all channel statuses."""
        if not self._initialized:
            self.initialize()

        summary = {
            "total": len(self._channels),
            "available": 0,
            "configured": 0,
            "connected": 0,
            "channels": [],
        }

        for channel in self.list_channels():
            info = channel.to_dict()
            summary["channels"].append(info)

            if channel.status == ChannelStatus.AVAILABLE:
                summary["available"] += 1
            elif channel.status == ChannelStatus.CONFIGURED:
                summary["configured"] += 1
            elif channel.status == ChannelStatus.CONNECTED:
                summary["connected"] += 1

        return summary


# =============================================================================
# Global Registry Instance
# =============================================================================

_registry: ChannelRegistry | None = None
_registry_lock = threading.Lock()


def get_channel_registry() -> ChannelRegistry:
    """Get the global channel registry instance."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ChannelRegistry()
                _registry.initialize()
    return _registry


def list_channels(available_only: bool = False) -> list[ChannelMeta]:
    """List registered channels."""
    return get_channel_registry().list_channels(available_only)


def get_channel(channel_id: str) -> ChannelMeta | None:
    """Get channel by ID or alias."""
    return get_channel_registry().get_channel(channel_id)


def normalize_channel(raw: str) -> ChannelId | None:
    """Normalize channel name to ID."""
    return get_channel_registry().normalize_channel_id(raw)
