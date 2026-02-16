"""
Ears - Input Listeners Component

The Ears receive input from various sources:
- Telegram bot messages
- MCP (Model Context Protocol) requests
- REST API calls
- Webhooks
- WebSocket connections

All inputs are normalized into events for the Brain to process.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from navig.agent.component import Component
from navig.agent.config import EarsConfig
from navig.agent.nervous_system import EventType, NervousSystem


@dataclass
class InputMessage:
    """Normalized input message from any source."""
    
    source: str  # telegram, mcp, api, webhook
    content: str
    user_id: Optional[str] = None
    channel_id: Optional[str] = None
    metadata: Dict[str, Any] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        self.metadata = self.metadata or {}
        self.timestamp = self.timestamp or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'source': self.source,
            'content': self.content,
            'user_id': self.user_id,
            'channel_id': self.channel_id,
            'metadata': self.metadata,
            'timestamp': self.timestamp.isoformat(),
        }


class InputListener(ABC):
    """Base class for input listeners."""
    
    def __init__(self, name: str):
        self.name = name
        self._running = False
        self._message_callback: Optional[Callable[[InputMessage], Any]] = None
    
    def set_callback(self, callback: Callable[[InputMessage], Any]) -> None:
        """Set callback for received messages."""
        self._message_callback = callback
    
    @abstractmethod
    async def start(self) -> None:
        """Start listening for input."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop listening."""
        pass
    
    async def on_message(self, message: InputMessage) -> None:
        """Handle received message."""
        if self._message_callback:
            result = self._message_callback(message)
            if asyncio.iscoroutine(result):
                await result


class TelegramListener(InputListener):
    """Telegram bot input listener."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("telegram")
        self.config = config
        self._bot = None
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start Telegram bot polling."""
        if not self.config.get('enabled'):
            return
        
        try:
            from navig_bot import NavigBot
            
            # Create callback to emit messages
            async def message_handler(text: str, user_id: int, chat_id: int):
                msg = InputMessage(
                    source='telegram',
                    content=text,
                    user_id=str(user_id),
                    channel_id=str(chat_id),
                    metadata={'platform': 'telegram'},
                )
                await self.on_message(msg)
            
            # Note: Integration with actual bot would happen here
            self._running = True
            
        except ImportError:
            pass
    
    async def stop(self) -> None:
        """Stop Telegram bot."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class MCPListener(InputListener):
    """MCP (Model Context Protocol) server listener."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("mcp")
        self.config = config
        self._server = None
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start MCP server."""
        if not self.config.get('enabled', True):
            return
        
        port = self.config.get('port', 8765)
        host = self.config.get('host', '127.0.0.1')
        
        try:
            # Integration with existing MCP server
            self._running = True
        except Exception:
            pass
    
    async def stop(self) -> None:
        """Stop MCP server."""
        self._running = False
        if self._task:
            self._task.cancel()


class APIListener(InputListener):
    """REST API listener for direct agent communication."""
    
    def __init__(self, port: int = 8790, host: str = "127.0.0.1"):
        super().__init__("api")
        self.port = port
        self.host = host
        self._server = None
        self._app = None
    
    async def start(self) -> None:
        """Start REST API server."""
        try:
            from aiohttp import web
            
            self._app = web.Application()
            self._app.router.add_post('/message', self._handle_message)
            self._app.router.add_post('/command', self._handle_command)
            self._app.router.add_get('/health', self._handle_health)
            
            runner = web.AppRunner(self._app)
            await runner.setup()
            self._server = web.TCPSite(runner, self.host, self.port)
            await self._server.start()
            self._running = True
            
        except ImportError:
            # aiohttp not installed
            pass
        except Exception:
            pass
    
    async def stop(self) -> None:
        """Stop API server."""
        self._running = False
        if self._server:
            await self._server.stop()
    
    async def _handle_message(self, request) -> Any:
        """Handle incoming message."""
        try:
            from aiohttp import web
            
            data = await request.json()
            msg = InputMessage(
                source='api',
                content=data.get('message', ''),
                user_id=data.get('user_id'),
                metadata=data.get('metadata', {}),
            )
            await self.on_message(msg)
            return web.json_response({'status': 'ok'})
        except Exception as e:
            from aiohttp import web
            return web.json_response({'error': str(e)}, status=400)
    
    async def _handle_command(self, request) -> Any:
        """Handle incoming command."""
        try:
            from aiohttp import web
            
            data = await request.json()
            msg = InputMessage(
                source='api',
                content=data.get('command', ''),
                user_id=data.get('user_id'),
                metadata={'type': 'command', **data.get('metadata', {})},
            )
            await self.on_message(msg)
            return web.json_response({'status': 'ok'})
        except Exception as e:
            from aiohttp import web
            return web.json_response({'error': str(e)}, status=400)
    
    async def _handle_health(self, request) -> Any:
        """Health check endpoint."""
        from aiohttp import web
        return web.json_response({'status': 'healthy', 'listener': 'api'})


class EmailListener(InputListener):
    """Email inbox listener using IMAP. Polls for unread messages."""

    def __init__(self, account_config: dict):
        label = account_config.get('label', account_config.get('address', 'email'))
        super().__init__(f"email:{label}")
        self.account = account_config
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self.account.get('enabled', True):
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        """Poll for new unread emails on a timer."""
        interval = self.account.get('check_interval', 60)
        seen_ids: set = set()

        while self._running:
            try:
                from navig.agent.proactive.imap_email import get_email_provider
                provider = get_email_provider(
                    self.account.get('provider', 'gmail'),
                    self.account['address'],
                    self.account['password'],
                    host=self.account.get('imap_host'),
                    port=self.account.get('imap_port'),
                )
                messages = await provider.list_unread(limit=10)
                for msg in messages:
                    if msg.id in seen_ids:
                        continue
                    seen_ids.add(msg.id)
                    await self.on_message(InputMessage(
                        source='email',
                        content=f"[{msg.sender}] {msg.subject}: {msg.snippet}",
                        user_id=msg.sender,
                        channel_id=self.account.get('address'),
                        metadata={
                            'type': 'email',
                            'label': self.account.get('label', ''),
                            'category': self.account.get('category', ''),
                            'subject': msg.subject,
                            'sender': msg.sender,
                            'email_id': msg.id,
                            'account': self.account.get('address'),
                        },
                    ))
            except Exception:
                pass  # Silently retry on next interval

            await asyncio.sleep(interval)


class WebhookListener(InputListener):
    """Webhook receiver for external service integration."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("webhook")
        self.config = config
        self._server = None
        self._app = None
    
    async def start(self) -> None:
        """Start webhook server."""
        if not self.config.get('enabled'):
            return
        
        try:
            from aiohttp import web
            
            port = self.config.get('port', 9000)
            host = self.config.get('host', '127.0.0.1')
            
            self._app = web.Application()
            self._app.router.add_post('/webhook/{service}', self._handle_webhook)
            
            runner = web.AppRunner(self._app)
            await runner.setup()
            self._server = web.TCPSite(runner, host, port)
            await self._server.start()
            self._running = True
            
        except ImportError:
            pass
        except Exception:
            pass
    
    async def stop(self) -> None:
        """Stop webhook server."""
        self._running = False
        if self._server:
            await self._server.stop()
    
    async def _handle_webhook(self, request) -> Any:
        """Handle incoming webhook."""
        try:
            from aiohttp import web
            
            service = request.match_info['service']
            data = await request.json()
            
            msg = InputMessage(
                source='webhook',
                content=json.dumps(data),
                metadata={
                    'service': service,
                    'headers': dict(request.headers),
                },
            )
            await self.on_message(msg)
            return web.json_response({'status': 'ok'})
        except Exception as e:
            from aiohttp import web
            return web.json_response({'error': str(e)}, status=400)


class Ears(Component):
    """
    Input listener component.
    
    The Ears manage multiple input sources and normalize
    all incoming messages into events for the Brain.
    """
    
    def __init__(
        self,
        config: EarsConfig,
        nervous_system: Optional[NervousSystem] = None,
    ):
        super().__init__("ears", nervous_system)
        self.config = config
        
        # Input listeners
        self._listeners: Dict[str, InputListener] = {}
        
        # Message queue for the brain
        self._message_queue: asyncio.Queue[InputMessage] = asyncio.Queue()
        
        # Statistics
        self._message_counts: Dict[str, int] = {}
    
    async def _on_start(self) -> None:
        """Start all configured listeners."""
        # Initialize listeners based on config
        if self.config.telegram.enabled:
            telegram = TelegramListener({
                'enabled': True,
                'bot_token': self.config.telegram.bot_token,
                'allowed_users': self.config.telegram.allowed_users,
            })
            telegram.set_callback(self._on_message_received)
            self._listeners['telegram'] = telegram
        
        if self.config.mcp.enabled:
            mcp = MCPListener({
                'enabled': True,
                'port': self.config.mcp.port,
                'host': self.config.mcp.host,
            })
            mcp.set_callback(self._on_message_received)
            self._listeners['mcp'] = mcp
        
        if self.config.api_enabled:
            api = APIListener(
                port=self.config.api_port,
                host='127.0.0.1',
            )
            api.set_callback(self._on_message_received)
            self._listeners['api'] = api
        
        if self.config.webhooks.enabled:
            webhook = WebhookListener({
                'enabled': True,
                'port': self.config.webhooks.port,
                'host': self.config.webhooks.host,
                'secret': self.config.webhooks.secret,
            })
            webhook.set_callback(self._on_message_received)
            self._listeners['webhook'] = webhook
        
        # Email listeners (multi-account)
        for acct in getattr(self.config, 'email_accounts', []):
            if not getattr(acct, 'enabled', True):
                continue
            acct_dict = acct.to_dict() if hasattr(acct, 'to_dict') else acct.__dict__
            email_listener = EmailListener(acct_dict)
            email_listener.set_callback(self._on_message_received)
            key = f"email:{acct_dict.get('label', acct_dict.get('address', 'unknown'))}"
            self._listeners[key] = email_listener
        
        # Start all listeners
        for name, listener in self._listeners.items():
            try:
                await listener.start()
            except Exception:
                pass
    
    async def _on_stop(self) -> None:
        """Stop all listeners."""
        for listener in self._listeners.values():
            try:
                await listener.stop()
            except Exception:
                pass
    
    async def _on_health_check(self) -> Dict[str, Any]:
        """Health check for ears."""
        return {
            'listeners': {
                name: listener._running
                for name, listener in self._listeners.items()
            },
            'message_counts': self._message_counts,
            'queue_size': self._message_queue.qsize(),
        }
    
    async def _on_message_received(self, message: InputMessage) -> None:
        """Handle received message from any listener."""
        # Update stats
        source = message.source
        self._message_counts[source] = self._message_counts.get(source, 0) + 1
        
        # Add to queue
        await self._message_queue.put(message)
        
        # Emit event
        await self.emit(
            EventType.MESSAGE_RECEIVED,
            {
                'message': message.to_dict(),
                'source': source,
            }
        )
    
    async def get_next_message(self, timeout: Optional[float] = None) -> Optional[InputMessage]:
        """Get next message from queue."""
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=timeout
                )
            else:
                return self._message_queue.get_nowait()
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None
    
    def has_messages(self) -> bool:
        """Check if there are pending messages."""
        return not self._message_queue.empty()
    
    def get_listener_status(self) -> Dict[str, bool]:
        """Get status of all listeners."""
        return {
            name: listener._running
            for name, listener in self._listeners.items()
        }
