"""
NAVIG WhatsApp Channel Adapter

WhatsApp integration for NAVIG Gateway using whatsapp-web.js bridge.
Supports:
- QR code authentication
- Direct messages
- Group messages (with @mentions)
- Media handling (text-only for now)

Requires external whatsapp-web.js bridge server.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    import aiohttp
    import websockets

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore[assignment]
    WEBSOCKETS_AVAILABLE = False

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class WhatsAppChannelConfig:
    """Configuration for WhatsApp channel."""

    def __init__(
        self,
        bridge_url: Optional[str] = None,
        bridge_ws_url: Optional[str] = None,
        api_key: Optional[str] = None,
        allowed_numbers: Optional[List[str]] = None,
        allowed_groups: Optional[List[str]] = None,
        respond_to_groups: bool = True,
        respond_to_dms: bool = True,
        mention_required_in_groups: bool = True,
    ):
        """
        Initialize WhatsApp config.
        
        Args:
            bridge_url: HTTP URL for WhatsApp bridge (for sending)
            bridge_ws_url: WebSocket URL for bridge (for receiving)
            api_key: API key for bridge authentication
            allowed_numbers: List of allowed phone numbers (None = all)
            allowed_groups: List of allowed group IDs (None = all)
            respond_to_groups: Whether to respond in groups
            respond_to_dms: Whether to respond to direct messages
            mention_required_in_groups: Require @mention in groups
        """
        self.bridge_url = bridge_url or os.environ.get(
            "WHATSAPP_BRIDGE_URL", "http://localhost:3000"
        )
        self.bridge_ws_url = bridge_ws_url or os.environ.get(
            "WHATSAPP_BRIDGE_WS_URL", "ws://localhost:3000/ws"
        )
        self.api_key = api_key or os.environ.get("WHATSAPP_BRIDGE_API_KEY", "")
        self.allowed_numbers = allowed_numbers
        self.allowed_groups = allowed_groups
        self.respond_to_groups = respond_to_groups
        self.respond_to_dms = respond_to_dms
        self.mention_required_in_groups = mention_required_in_groups

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WhatsAppChannelConfig':
        """Create config from dictionary."""
        return cls(
            bridge_url=data.get("bridge_url"),
            bridge_ws_url=data.get("bridge_ws_url"),
            api_key=data.get("api_key"),
            allowed_numbers=data.get("allowed_numbers"),
            allowed_groups=data.get("allowed_groups"),
            respond_to_groups=data.get("respond_to_groups", True),
            respond_to_dms=data.get("respond_to_dms", True),
            mention_required_in_groups=data.get("mention_required_in_groups", True),
        )


class WhatsAppMessage:
    """Parsed WhatsApp message."""

    def __init__(
        self,
        message_id: str,
        from_number: str,
        content: str,
        timestamp: datetime,
        is_group: bool = False,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        sender_name: Optional[str] = None,
        is_mentioned: bool = False,
        quoted_message: Optional[str] = None,
    ):
        self.message_id = message_id
        self.from_number = from_number
        self.content = content
        self.timestamp = timestamp
        self.is_group = is_group
        self.group_id = group_id
        self.group_name = group_name
        self.sender_name = sender_name
        self.is_mentioned = is_mentioned
        self.quoted_message = quoted_message

    @classmethod
    def from_bridge_payload(cls, data: Dict[str, Any]) -> 'WhatsAppMessage':
        """Create from bridge webhook payload."""
        return cls(
            message_id=data.get("id", ""),
            from_number=data.get("from", "").split("@")[0],  # Remove @c.us suffix
            content=data.get("body", ""),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
            is_group=data.get("isGroup", False),
            group_id=data.get("groupId"),
            group_name=data.get("groupName"),
            sender_name=data.get("senderName"),
            is_mentioned=data.get("isMentioned", False),
            quoted_message=data.get("quotedMessage"),
        )


class WhatsAppChannel:
    """
    WhatsApp channel adapter for NAVIG Gateway.
    
    Uses whatsapp-web.js based bridge for WhatsApp Web connectivity.
    
    Bridge setup:
    1. Install whatsapp-web.js bridge: https://github.com/nichuanfang/whatsapp-bridge
    2. Run bridge server on localhost:3000
    3. Scan QR code to authenticate
    """

    def __init__(
        self,
        config: WhatsAppChannelConfig,
        message_handler: Callable[[str, str, str, Dict[str, Any]], asyncio.Future],
    ):
        """
        Initialize WhatsApp channel.
        
        Args:
            config: WhatsApp channel configuration
            message_handler: Async callback for message routing
                            (channel, user_id, message, metadata) -> response
        """
        if not AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp is required for WhatsApp channel. "
                "Install: pip install aiohttp"
            )

        self.config = config
        self.message_handler = message_handler

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[Any] = None  # WebSocket connection
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._bot_number: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"

            self._session = aiohttp.ClientSession(headers=headers)

        return self._session

    async def _send_message(self, to: str, content: str) -> bool:
        """
        Send a message via the bridge.
        
        Args:
            to: Recipient number (with or without @c.us suffix)
            content: Message content
            
        Returns:
            True if sent successfully
        """
        session = await self._get_session()

        # Ensure proper format
        if not to.endswith("@c.us") and not to.endswith("@g.us"):
            to = f"{to}@c.us"

        try:
            payload = {
                "to": to,
                "message": content,
            }

            async with session.post(
                f"{self.config.bridge_url}/api/send",
                json=payload,
            ) as response:
                if response.status == 200:
                    return True
                else:
                    error = await response.text()
                    logger.error(f"Failed to send WhatsApp message: {error}")
                    return False

        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}")
            return False

    async def _handle_incoming_message(self, message: WhatsAppMessage):
        """Handle an incoming WhatsApp message."""
        # Check if we should respond
        if not self._should_respond(message):
            return

        # Extract clean content
        content = message.content
        if message.is_mentioned and self._bot_number:
            # Remove mention from content
            content = content.replace(f"@{self._bot_number}", "").strip()

        if not content:
            return

        # Build metadata
        metadata = self._build_metadata(message)

        try:
            # Route to handler
            response = await self.message_handler(
                "whatsapp",
                message.from_number,
                content,
                metadata,
            )

            # Send response
            reply_to = message.group_id if message.is_group else message.from_number
            await self._send_message(reply_to, response)

        except Exception as e:
            logger.error(f"Error handling WhatsApp message: {e}")
            reply_to = message.group_id if message.is_group else message.from_number
            await self._send_message(
                reply_to,
                "❌ Sorry, I encountered an error processing your request."
            )

    def _should_respond(self, message: WhatsAppMessage) -> bool:
        """Check if we should respond to this message."""
        # Check permissions
        if not self._check_permissions(message):
            return False

        # DM handling
        if not message.is_group:
            return self.config.respond_to_dms

        # Group handling
        if not self.config.respond_to_groups:
            return False

        # Check if mention required
        if self.config.mention_required_in_groups:
            return message.is_mentioned

        return True

    def _check_permissions(self, message: WhatsAppMessage) -> bool:
        """Check if message sender is allowed."""
        # Check number permission
        if self.config.allowed_numbers is not None:
            if message.from_number not in self.config.allowed_numbers:
                return False

        # Check group permission
        if message.is_group and self.config.allowed_groups is not None:
            if message.group_id not in self.config.allowed_groups:
                return False

        return True

    def _build_metadata(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Build metadata from WhatsApp message."""
        metadata = {
            "message_id": message.message_id,
            "sender_name": message.sender_name,
            "timestamp": message.timestamp.isoformat(),
        }

        if message.is_group:
            metadata["group_id"] = message.group_id
            metadata["group_name"] = message.group_name

        if message.quoted_message:
            metadata["quoted_message"] = message.quoted_message

        return metadata

    async def _connect_websocket(self):
        """Connect to bridge WebSocket for receiving messages."""
        if not WEBSOCKETS_AVAILABLE:
            logger.warning(
                "websockets not available, using polling mode. "
                "Install: pip install websockets"
            )
            return

        reconnect_delay = self._reconnect_delay

        while self._running:
            try:
                logger.info(f"Connecting to WhatsApp bridge at {self.config.bridge_ws_url}")

                headers = {}
                if self.config.api_key:
                    headers["Authorization"] = f"Bearer {self.config.api_key}"

                async with websockets.connect(
                    self.config.bridge_ws_url,
                    extra_headers=headers,
                ) as ws:
                    self._ws = ws
                    reconnect_delay = self._reconnect_delay  # Reset on success

                    logger.info("Connected to WhatsApp bridge")

                    async for raw_message in ws:
                        try:
                            data = json.loads(raw_message)

                            # Handle different event types
                            event_type = data.get("type", "message")

                            if event_type == "qr":
                                # QR code for authentication
                                logger.info("WhatsApp QR code received - scan to authenticate")
                                # Could emit event for UI display

                            elif event_type == "authenticated":
                                logger.info("WhatsApp authenticated successfully")
                                self._bot_number = data.get("number")

                            elif event_type == "ready":
                                logger.info("WhatsApp client ready")

                            elif event_type == "message":
                                message = WhatsAppMessage.from_bridge_payload(data)
                                await self._handle_incoming_message(message)

                            elif event_type == "disconnected":
                                logger.warning("WhatsApp disconnected")

                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from bridge: {raw_message[:100]}")
                        except Exception as e:
                            logger.error(f"Error processing bridge message: {e}")

            except websockets.ConnectionClosed:
                logger.warning("WhatsApp bridge connection closed")
            except Exception as e:
                logger.error(f"WhatsApp bridge error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self._max_reconnect_delay)

    async def start(self):
        """Start the WhatsApp channel."""
        logger.info("Starting WhatsApp channel...")
        self._running = True

        # Start WebSocket listener
        asyncio.create_task(self._connect_websocket())

    async def stop(self):
        """Stop the WhatsApp channel."""
        logger.info("Stopping WhatsApp channel...")
        self._running = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

    @property
    def is_running(self) -> bool:
        """Check if channel is running."""
        return self._running

    async def get_qr_code(self) -> Optional[str]:
        """
        Get QR code for WhatsApp authentication.
        
        Returns:
            QR code data URL or None if not available
        """
        session = await self._get_session()

        try:
            async with session.get(f"{self.config.bridge_url}/api/qr") as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("qr")
        except Exception as e:
            logger.error(f"Error getting QR code: {e}")

        return None

    async def get_status(self) -> Dict[str, Any]:
        """
        Get WhatsApp connection status.
        
        Returns:
            Status dict with connection info
        """
        session = await self._get_session()

        try:
            async with session.get(f"{self.config.bridge_url}/api/status") as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"Error getting status: {e}")

        return {"connected": False, "error": "Unable to reach bridge"}


def is_whatsapp_available() -> bool:
    """Check if WhatsApp integration is available."""
    return AIOHTTP_AVAILABLE


def create_whatsapp_channel(
    config: Optional[WhatsAppChannelConfig] = None,
    message_handler: Optional[Callable] = None,
) -> WhatsAppChannel:
    """
    Create a WhatsApp channel adapter.
    
    Args:
        config: WhatsApp configuration (uses defaults if not provided)
        message_handler: Message routing callback
        
    Returns:
        Configured WhatsAppChannel instance
    """
    if config is None:
        config = WhatsAppChannelConfig()

    if message_handler is None:
        # Default handler that echoes
        async def echo_handler(channel, user_id, message, metadata):
            return f"Echo: {message}"
        message_handler = echo_handler

    return WhatsAppChannel(config, message_handler)


# Bridge server setup instructions
WHATSAPP_BRIDGE_SETUP = """
# WhatsApp Bridge Setup

NAVIG uses a whatsapp-web.js based bridge for WhatsApp connectivity.

## Option 1: Docker (Recommended)

```bash
docker run -d \\
  --name whatsapp-bridge \\
  -p 3000:3000 \\
  -v whatsapp-session:/app/session \\
  ghcr.io/nichuanfang/whatsapp-bridge:latest
```

## Option 2: Manual Setup

1. Clone the bridge repo:
   ```bash
   git clone https://github.com/nichuanfang/whatsapp-bridge.git
   cd whatsapp-bridge
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Start the bridge:
   ```bash
   npm start
   ```

4. Open http://localhost:3000 and scan the QR code with WhatsApp

## Environment Variables

```bash
# In your .env file
WHATSAPP_BRIDGE_URL=http://localhost:3000
WHATSAPP_BRIDGE_WS_URL=ws://localhost:3000/ws
WHATSAPP_BRIDGE_API_KEY=your-optional-api-key
```

## Alternative Bridges

- baileys: https://github.com/whiskeysockets/baileys
- whatsapp-web.js: https://github.com/pedroslopez/whatsapp-web.js
"""
