"""
NAVIG Gateway Server - Main Entry Point

Persistent HTTP/WebSocket server providing:
- 24/7 operation
- Multi-channel coordination  
- Heartbeat scheduling
- Cron job management
- Session persistence

Architecture inspired by autonomous agent patterns.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from aiohttp import web
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    web = None
    aiohttp = None
    AIOHTTP_AVAILABLE = False

# Safe no-op fallback for @web.middleware when aiohttp is not installed.
# Prevents AttributeError at class parse time during unit tests / imports.
def _noop_deco(fn):  # pragma: no cover
    return fn

_web_middleware = web.middleware if AIOHTTP_AVAILABLE else _noop_deco

from navig.config import get_config_manager
from navig.debug_logger import get_debug_logger
from navig.gateway.session_manager import SessionManager, Session
from navig.gateway.channel_router import ChannelRouter
from navig.gateway.system_events import SystemEventQueue
from navig.gateway.config_watcher import ConfigWatcher
from navig.agent.proactive.engine import get_proactive_engine
from navig.workspace_ownership import USER_WORKSPACE_DIR
from navig.gateway.policy_gate import PolicyGate
from navig.gateway.audit_log import AuditLog
from navig.gateway.billing_emitter import BillingEmitter
from navig.gateway.cooldown import CooldownTracker

# Lazy imports for optional modules
_approval_manager = None
_browser_controller = None
_mcp_client_manager = None
_webhook_receiver = None
_task_queue = None
_task_worker = None

logger = get_debug_logger()


class GatewayConfig:
    """Gateway configuration with defaults."""
    
    def __init__(self, raw_config: Dict[str, Any] = None):
        raw_config = raw_config or {}
        gateway_cfg = raw_config.get('gateway', {})
        
        self.enabled = gateway_cfg.get('enabled', True)
        self.port = gateway_cfg.get('port', 8789)
        self.host = gateway_cfg.get('host', '127.0.0.1')
        self.auth_token = gateway_cfg.get('auth', {}).get('token')
        
        # Storage directory
        storage = gateway_cfg.get('storage_dir', '~/.navig')
        self.storage_dir = Path(storage).expanduser()
        
        # Heartbeat defaults
        heartbeat_cfg = raw_config.get('heartbeat', {})
        self.heartbeat_enabled = heartbeat_cfg.get('enabled', True)
        self.heartbeat_interval = heartbeat_cfg.get('interval', '30m')
        
        # Agent config
        agents_cfg = raw_config.get('agents', {})
        self.default_agent = agents_cfg.get('default', 'navig')
        self.agents = agents_cfg.get('list', [])


class NavigGateway:
    """
    NAVIG Autonomous Agent Gateway
    
    Runs continuously, coordinating:
    - Message routing from all channels
    - Periodic heartbeat check-ins
    - Scheduled cron jobs
    - Session persistence
    - System event processing
    """
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        """
        Initialize the gateway.
        
        Args:
            config: Gateway configuration (auto-loaded if None)
        """
        if not AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp is required for gateway. Install with: pip install aiohttp"
            )
        
        # Load config
        self.config_manager = get_config_manager()
        
        if config:
            self.config = config
        else:
            raw_config = self.config_manager.global_config
            self.config = GatewayConfig(raw_config)
        
        # Core components
        self.storage_dir = self.config.storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Session manager
        self.sessions = SessionManager(self.storage_dir)
        
        # Channel router
        self.router = ChannelRouter(self)
        
        # System event queue
        self.system_events = SystemEventQueue(self.storage_dir)
        self.event_queue = self.system_events  # Alias for compatibility
        
        # Config watcher (hot reload) - initialized in start()
        self.config_watcher: Optional[ConfigWatcher] = None
        
        # State
        self.running = False
        self.start_time: Optional[datetime] = None
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        
        # Components initialized later
        self.heartbeat_runner = None
        self.cron_service = None
        self.channels: Dict[str, Any] = {}
        
        # Queue for pending messages
        # Bounded queue — prevents OOM on message floods (P1-2)
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queue_task: Optional[asyncio.Task] = None
        
        # New autonomous modules (lazy initialized)
        self.approval_manager = None
        self.browser_controller = None
        self.mcp_client_manager = None
        self.webhook_receiver = None
        self.task_queue = None
        self.task_worker = None
        
        # Rate limiter state: {ip: [(timestamp, was_auth_failure), ...]}
        self._auth_attempts: Dict[str, list] = defaultdict(list)
        self._rate_limit_window = 60  # seconds
        self._rate_limit_max_failures = 5

        # ── Safety & Audit ─────────────────────────────────────────────────
        raw_cfg = self.config_manager.global_config or {}
        raw_gateway_cfg: Dict[str, Any] = raw_cfg.get("gateway", {}) if isinstance(raw_cfg, dict) else {}
        self.policy_gate      = PolicyGate.from_config(raw_gateway_cfg)
        self.audit_log        = AuditLog()
        self.billing_emitter  = BillingEmitter()
        self.cooldown         = CooldownTracker(default_cooldown_seconds=30.0)
        
        logger.info("NavigGateway initialized", extra={
            "port": self.config.port,
            "host": self.config.host,
            "storage_dir": str(self.storage_dir)
        })
    
    async def start(self):
        """Start the gateway server and all subsystems."""
        if self.running:
            logger.warning("Gateway already running")
            return
        
        self.running = True
        self.start_time = datetime.now()
        
        logger.info("Starting NAVIG Gateway...")
        
        # Initialize config watcher
        self.config_watcher = ConfigWatcher(self)
        
        # Initialize formation registry (loaded once at gateway start)
        try:
            from navig.formations.registry import get_registry
            get_registry().initialize(self.storage_dir / 'workspace')
        except Exception as e:
            logger.error(f"Failed to initialize formation registry: {e}")
        
        # Start HTTP server
        await self._start_http_server()
        
        # Start config watcher
        await self.config_watcher.start()
        
        # Start heartbeat runner
        await self._start_heartbeat()
        
        # Start cron service
        await self._start_cron()
        
        # Start message queue processor
        self._queue_task = asyncio.create_task(self._process_message_queue())
        
        # Ensure mesh_token exists (auto-generate if missing)
        await self._ensure_mesh_token()

        # Initialize autonomous modules
        await self._init_autonomous_modules()
        
        # Wire unified comms dispatcher
        await self._init_comms()
        
        logger.info(f"✅ NAVIG Gateway started on {self.config.host}:{self.config.port}")
        print(f"\n✅ NAVIG Gateway running at http://{self.config.host}:{self.config.port}")
        print(f"   Heartbeat: {'enabled' if self.config.heartbeat_enabled else 'disabled'}")
        print(f"   Storage: {self.storage_dir}")
        print("\n   Press Ctrl+C to stop\n")
        
        # Keep running
        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the gateway and all subsystems."""
        if not self.running:
            return
        
        logger.info("Stopping NAVIG Gateway...")
        self.running = False
        
        # Stop queue processor
        if self._queue_task:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
        
        # Stop heartbeat
        if self.heartbeat_runner:
            await self.heartbeat_runner.stop()
        
        # Stop cron
        if self.cron_service:
            await self.cron_service.stop()
        
        # Stop config watcher
        await self.config_watcher.stop()

        # Stop Flux mesh discovery
        mesh_discovery = getattr(self, "_mesh_discovery", None)
        if mesh_discovery is not None:
            try:
                await mesh_discovery.stop()
            except Exception as e:
                logger.warning("[mesh] Stop error: %s", e)

        # Stop autonomous modules (comms_router, task_worker, etc.) — P1-11
        comms_router = getattr(self, "comms_router", None)
        if comms_router is not None:
            try:
                await comms_router.stop()
            except Exception:
                logger.exception("[comms] Stop error")

        task_worker = getattr(self, "task_worker", None)
        if task_worker is not None:
            try:
                await task_worker.stop()
            except Exception:
                logger.exception("[task_worker] Stop error")

        # Stop HTTP server
        if self._runner:
            await self._runner.cleanup()
        
        # Save sessions
        await self.sessions.save_all()
        
        logger.info("Gateway stopped")
        print("\n Gateway stopped")
    
    def _load_config(self) -> None:
        """Reload gateway config from config manager (called by ConfigWatcher)."""
        raw_config = self.config_manager.global_config
        old_token = self.config.auth_token
        self.config = GatewayConfig(raw_config)
        if self.config.auth_token != old_token:
            logger.info("Auth token updated via config reload")

    async def _start_http_server(self):
        """Start HTTP/WebSocket server."""
        self._app = web.Application(middlewares=[
            self._rate_limit_middleware,
            self._cors_middleware,
        ])
        
        # ── Route registration (extracted to navig.gateway.routes) ──
        from navig.gateway.routes import register_all_routes
        register_all_routes(self._app, self)
        
        # Legacy inline routes kept as fallback reference (commented out).
        # All handlers now live in navig/gateway/routes/*.py.
        # See: core, heartbeat, cron, approval, browser, mcp, tasks,
        #      memory, proactive modules.
        
        # Webhook receiver routes (dynamically added from webhooks module)
        self._setup_webhook_routes()
        
        # Deck (Telegram Mini App) routes — pass auth config
        try:
            from navig.gateway.deck import register_deck_routes
            raw_cfg = self.config_manager.global_config or {}
            tg_cfg = raw_cfg.get("telegram", {}) if isinstance(raw_cfg, dict) else {}
            deck_cfg = raw_cfg.get("deck", {}) if isinstance(raw_cfg, dict) else {}

            # Only register deck if telegram is configured (tightly coupled)
            bot_token = tg_cfg.get("bot_token", "")
            if bot_token and deck_cfg.get("enabled", True):
                # Store gateway reference so Deck API handlers can access channels
                self._app["gateway"] = self
                register_deck_routes(
                    self._app,
                    bot_token=bot_token,
                    allowed_users=tg_cfg.get("allowed_users", []),
                    require_auth=tg_cfg.get("require_auth", True),
                    deck_cfg=deck_cfg,
                )
            elif not bot_token:
                logger.info("Deck not loaded: no Telegram bot_token configured")
            else:
                logger.info("Deck disabled in config")
        except Exception as e:
            logger.debug("Deck API not loaded: %s", e)
        
        # Start server
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
    
    async def _start_heartbeat(self):
        """Start heartbeat runner if enabled."""
        if not self.config.heartbeat_enabled:
            logger.info("Heartbeat disabled")
            return
        
        from navig.heartbeat import HeartbeatRunner, HeartbeatConfig
        
        # Get heartbeat config from global config
        heartbeat_dict = self.config_manager.global_config.get('heartbeat', {})
        heartbeat_config = HeartbeatConfig.from_dict(heartbeat_dict)
        
        self.heartbeat_runner = HeartbeatRunner(self, heartbeat_config)
        await self.heartbeat_runner.start()
    
    async def _start_cron(self):
        """Start cron service."""
        from navig.scheduler import CronService, CronConfig
        
        # Get cron config from global config
        cron_dict = self.config_manager.global_config.get('cron', {})
        cron_config = CronConfig.from_dict(cron_dict)
        
        # Cron service needs storage path
        storage_path = self.config_manager.global_config_dir / 'scheduler'
        storage_path.mkdir(exist_ok=True)
        
        self.cron_service = CronService(self, storage_path, cron_config)
        await self.cron_service.start()
    
    async def _on_config_reload(self, new_config: Dict[str, Any]):
        """Handle config file changes (hot reload)."""
        logger.info("Config changed, reloading...")
        
        # Reload gateway config
        old_config = self.config
        self.config = GatewayConfig(new_config)
        
        # Restart heartbeat if interval changed
        if self.heartbeat_runner:
            old_interval = old_config.heartbeat_interval
            new_interval = self.config.heartbeat_interval
            
            if old_interval != new_interval:
                logger.info(f"Heartbeat interval changed: {old_interval} → {new_interval}")
                await self.heartbeat_runner.update_config()
    
    async def _process_message_queue(self):
        """Process queued messages."""
        while self.running:
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(), 
                    timeout=1.0
                )
                await self._process_message(message)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error processing message from queue")  # P1-5
    
    async def _process_message(self, message: Dict[str, Any]):
        """Process a single message (with 60 s timeout — P1-1)."""
        try:
            response = await asyncio.wait_for(
                self.router.route_message(
                    channel=message['channel'],
                    user_id=message['user_id'],
                    message=message['message'],
                    metadata=message.get('metadata', {})
                ),
                timeout=60.0,
            )
            
            # Store response callback if provided
            if 'callback' in message:
                message['callback'](response)

        except asyncio.TimeoutError:
            logger.error(
                "route_message timed out after 60 s for channel=%s user=%s",
                message.get('channel'), message.get('user_id'),
            )
            if 'callback' in message:
                message['callback']({"error": "Request timed out"})
        except Exception:
            logger.exception("Failed to process message")  # P1-5
    
    # ==================
    # Rate Limiting Middleware
    # ==================

    @_web_middleware
    async def _rate_limit_middleware(self, request, handler):
        """Block IPs with too many failed auth attempts."""
        peer = request.remote or '127.0.0.1'
        now = time.monotonic()
        window = self._rate_limit_window

        # Prune old entries
        attempts = self._auth_attempts[peer]
        self._auth_attempts[peer] = [
            (ts, failed) for ts, failed in attempts if now - ts < window
        ]
        attempts = self._auth_attempts[peer]

        # Count recent failures
        recent_failures = sum(1 for _, failed in attempts if failed)
        if recent_failures >= self._rate_limit_max_failures:
            logger.warning(f"Rate-limited {peer}: {recent_failures} auth failures in {window}s")
            return web.json_response(
                {"ok": False, "error": "Too many failed attempts. Try again later.",
                 "error_code": "rate_limited"},
                status=429,
            )

        resp = await handler(request)

        # Track auth failures (401 responses)
        is_failure = resp.status == 401
        self._auth_attempts[peer].append((now, is_failure))

        return resp

    # ==================
    # CORS Middleware
    # ==================

    @_web_middleware
    async def _cors_middleware(self, request, handler):
        """Allow cross-origin requests for Deck API (Telegram WebApp iframe)."""
        if request.method == "OPTIONS":
            resp = web.Response(status=200)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as e:
                resp = e
        # Add CORS headers only for Deck API routes
        path = request.path
        if path.startswith("/api/deck") or path.startswith("/deck"):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data, X-Telegram-User"
            resp.headers["Access-Control-Max-Age"] = "3600"
        return resp
    
    # ==================
    # HTTP Handlers
    # ==================
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "timestamp": datetime.now().isoformat()
        })
    
    async def _handle_shutdown(self, request: web.Request) -> web.Response:
        """
        Graceful shutdown endpoint.
        
        POST /shutdown
        
        Initiates graceful shutdown of the gateway server.
        """
        logger.info("Shutdown requested via API")
        
        # Send response before shutting down
        response = web.json_response({
            "status": "shutting_down",
            "message": "Gateway shutdown initiated"
        })
        
        # Schedule shutdown after response is sent
        asyncio.create_task(self._delayed_shutdown())
        
        return response
    
    async def _delayed_shutdown(self):
        """Shutdown after a brief delay to allow response to be sent."""
        await asyncio.sleep(0.5)
        await self.stop()
        # Force exit if stop doesn't terminate the event loop
        sys.exit(0)
    
    async def _handle_status(self, request: web.Request) -> web.Response:
        """Detailed status endpoint."""
        try:
            uptime = None
            if self.start_time:
                uptime = (datetime.now() - self.start_time).total_seconds()
            
            # Get heartbeat info safely
            heartbeat_info = None
            if self.heartbeat_runner:
                heartbeat_info = {
                    "running": self.heartbeat_runner.running,
                    "last_run": self.heartbeat_runner._last_heartbeat.isoformat() if self.heartbeat_runner._last_heartbeat else None,
                    "next_run": self.heartbeat_runner._next_heartbeat.isoformat() if self.heartbeat_runner._next_heartbeat else None,
                }
            
            # Get cron info safely
            cron_info = None
            if self.cron_service:
                cron_info = {
                    "jobs": len(self.cron_service.jobs),
                    "enabled_jobs": sum(1 for j in self.cron_service.jobs.values() if j.enabled),
                }
            
            return web.json_response({
                "status": "running" if self.running else "stopped",
                "uptime_seconds": uptime,
                "config": {
                    "port": self.config.port,
                    "host": self.config.host,
                    "heartbeat_enabled": self.config.heartbeat_enabled,
                    "heartbeat_interval": self.config.heartbeat_interval,
                },
                "sessions": {
                    "active": len(self.sessions.sessions),
                },
                "heartbeat": heartbeat_info,
                "cron": cron_info,
            })
        except Exception as e:
            logger.exception("Status endpoint error")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_message(self, request: web.Request) -> web.Response:
        """Handle incoming message from any channel."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        
        # Validate required fields
        required = ['channel', 'user_id', 'message']
        for field in required:
            if field not in data:
                return web.json_response(
                    {"error": f"Missing required field: {field}"},
                    status=400
                )
        
        # Check queue capacity before enqueuing (P1-2 backpressure)
        if self._message_queue.full():
            return web.json_response(
                {"error": "Server is busy. Message queue full.", "error_code": "queue_full"},
                status=503,
            )

        # Route message (60 s timeout — P1-1)
        try:
            response = await asyncio.wait_for(
                self.router.route_message(
                    channel=data['channel'],
                    user_id=data['user_id'],
                    message=data['message'],
                    metadata=data.get('metadata', {})
                ),
                timeout=60.0,
            )
            
            return web.json_response({
                "success": True,
                "response": response
            })

        except asyncio.TimeoutError:
            logger.error(
                "route_message HTTP timed out for channel=%s user=%s",
                data.get('channel'), data.get('user_id'),
            )
            return web.json_response(
                {"error": "Request timed out", "error_code": "timeout"},
                status=504,
            )
        except Exception:
            logger.exception("Error handling message")  # P1-5
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
            )
    
    async def _handle_event(self, request: web.Request) -> web.Response:
        """Handle system event injection."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        
        text = data.get('text')
        if not text:
            return web.json_response(
                {"error": "Missing 'text' field"},
                status=400
            )
        
        agent_id = data.get('agent_id', 'default')
        wake_now = data.get('wake_now', False)
        
        # Enqueue event
        await self.system_events.enqueue(
            text=text,
            agent_id=agent_id
        )
        
        # Wake heartbeat if requested
        if wake_now and self.heartbeat_runner:
            await self.heartbeat_runner.request_run_now()
        
        return web.json_response({
            "success": True,
            "message": "Event queued"
        })
    
    async def _handle_list_sessions(self, request: web.Request) -> web.Response:
        """List active sessions."""
        sessions = []
        for key, session in self.sessions.sessions.items():
            sessions.append({
                "key": key,
                "message_count": len(session.messages),
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            })
        
        return web.json_response({
            "sessions": sessions,
            "total": len(sessions)
        })
    
    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket connection for real-time updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        # Register client
        client_id = id(ws)
        self.__dict__.setdefault("_ws_subscriptions", {})[client_id] = set()
        logger.info(f"WebSocket client connected: {client_id}")
        
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        # Handle WebSocket messages
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        await ws.send_json({"error": "Invalid JSON"})
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    
        finally:
            self.__dict__.setdefault("_ws_subscriptions", {}).pop(client_id, None)
            logger.info(f"WebSocket client disconnected: {client_id}")
        
        return ws
    
    async def _handle_ws_message(self, ws: web.WebSocketResponse, data: Dict[str, Any]):
        """Handle WebSocket message."""
        action = data.get('action')
        
        if action == 'ping':
            await ws.send_json({"action": "pong"})
            
        elif action == 'subscribe':
            topic = data.get("topic")
            if not topic:
                await ws.send_json({"error": "Missing required field: topic"})
                return

            client_id = id(ws)
            subscriptions = self.__dict__.setdefault("_ws_subscriptions", {})
            subscriptions.setdefault(client_id, set()).add(str(topic))
            await ws.send_json(
                {
                    "action": "subscribed",
                    "topic": topic,
                    "subscriptions": sorted(subscriptions[client_id]),
                }
            )
            
        elif action == 'message':
            if data.get("message") is None:
                await ws.send_json({"error": "Missing required field: message"})
                return

            # Route message (60 s timeout — P1-1)
            try:
                response = await asyncio.wait_for(
                    self.router.route_message(
                        channel=data.get('channel', 'ws'),
                        user_id=data.get('user_id', 'anonymous'),
                        message=data.get('message', ''),
                        metadata=data.get('metadata', {})
                    ),
                    timeout=60.0,
                )
                await ws.send_json({"action": "response", "response": response})
            except asyncio.TimeoutError:
                await ws.send_json({"action": "error", "error": "Request timed out"})
        else:
            await ws.send_json({"error": "Unsupported websocket action"})
    
    # ==================
    # Heartbeat Handlers
    # ==================
    
    async def _handle_heartbeat_trigger(self, request: web.Request) -> web.Response:
        """Handle POST /heartbeat/trigger - trigger immediate heartbeat."""
        if not self.heartbeat_runner:
            return web.json_response({"error": "Heartbeat not enabled"}, status=503)
        
        try:
            # Trigger heartbeat
            result = await self.heartbeat_runner.trigger_now()
            
            return web.json_response({
                "success": result.success,
                "suppressed": result.suppressed,
                "response": result.response,
                "issues": result.issues_found,
                "timestamp": result.timestamp.isoformat()
            })
        except Exception as e:
            logger.exception("Heartbeat trigger failed")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_heartbeat_history(self, request: web.Request) -> web.Response:
        """Handle GET /heartbeat/history - get heartbeat history."""
        if not self.heartbeat_runner:
            return web.json_response({"error": "Heartbeat not enabled"}, status=503)
        
        try:
            limit = min(int(request.query.get('limit', 10)), 1000)  # P1-8: cap at 1000
            history = self.heartbeat_runner.get_history(limit=limit)
            
            return web.json_response({"history": history})
        except Exception:
            logger.exception("Failed to get heartbeat history")  # P1-5
            return web.json_response({"error": "Failed to get heartbeat history"}, status=500)
    
    async def _handle_heartbeat_status(self, request: web.Request) -> web.Response:
        """Handle GET /heartbeat/status - get heartbeat status."""
        if not self.heartbeat_runner:
            return web.json_response({"error": "Heartbeat not enabled"}, status=503)
        
        try:
            status = self.heartbeat_runner.get_status()
            return web.json_response(status)
        except Exception as e:
            logger.exception("Failed to get heartbeat status")
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Cron Handlers
    # ==================
    
    async def _handle_cron_list(self, request: web.Request) -> web.Response:
        """Handle GET /cron/jobs - list all jobs."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            jobs = self.cron_service.list_jobs()
            return web.json_response({
                "jobs": [job.to_dict() for job in jobs]
            })
        except Exception as e:
            logger.exception("Failed to list cron jobs")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_add(self, request: web.Request) -> web.Response:
        """Handle POST /cron/jobs - create new job."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            data = await request.json()
            job = self.cron_service.add_job(
                name=data['name'],
                schedule=data['schedule'],
                command=data['command'],
                enabled=data.get('enabled', True),
                timeout_seconds=data.get('timeout', 300)
            )
            return web.json_response(job.to_dict())
        except Exception as e:
            logger.exception("Failed to create cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_get(self, request: web.Request) -> web.Response:
        """Handle GET /cron/jobs/{job_id} - get job details."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            job_id = request.match_info['job_id']
            job = self.cron_service.get_job(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response(job.to_dict())
        except Exception as e:
            logger.exception("Failed to get cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_delete(self, request: web.Request) -> web.Response:
        """Handle DELETE /cron/jobs/{job_id} - delete job."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            job_id = request.match_info['job_id']
            success = self.cron_service.remove_job(job_id)
            if not success:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"success": True})
        except Exception as e:
            logger.exception("Failed to delete cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_enable(self, request: web.Request) -> web.Response:
        """Handle POST /cron/jobs/{job_id}/enable - enable job."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            job_id = request.match_info['job_id']
            success = self.cron_service.enable_job(job_id)
            if not success:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"success": True})
        except Exception as e:
            logger.exception("Failed to enable cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_disable(self, request: web.Request) -> web.Response:
        """Handle POST /cron/jobs/{job_id}/disable - disable job."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            job_id = request.match_info['job_id']
            success = self.cron_service.disable_job(job_id)
            if not success:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"success": True})
        except Exception as e:
            logger.exception("Failed to disable cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_cron_run(self, request: web.Request) -> web.Response:
        """Handle POST /cron/jobs/{job_id}/run - run job immediately."""
        if not self.cron_service:
            return web.json_response({"error": "Cron service not enabled"}, status=503)
        
        try:
            job_id = request.match_info['job_id']
            result = await self.cron_service.run_job_now(job_id)
            if not result:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({
                "success": result.get('success', False),
                "output": result.get('output', ''),
                "error": result.get('error')
            })
        except Exception as e:
            logger.exception("Failed to run cron job")
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Autonomous Modules Setup
    # ==================
    
    async def _ensure_mesh_token(self) -> None:
        """Auto-generate mesh_token if not already set in global config."""
        import secrets
        gw_cfg = dict(self.config_manager.global_config.get("gateway", {}))
        if not gw_cfg.get("mesh_token"):
            token = secrets.token_hex(32)
            gw_cfg["mesh_token"] = token
            updater = getattr(self.config_manager, "update_global_config", None)
            if callable(updater):
                updater({"gateway": gw_cfg})
                logger.info("[Gateway] mesh_token auto-generated and saved to persistent config")
            else:
                self.config_manager.global_config["gateway"] = gw_cfg
                logger.info("[Gateway] mesh_token auto-generated and updated in-memory config")

    # ------------------------------------------------------------------
    # Policy / Audit helpers
    # ------------------------------------------------------------------

    async def policy_check(
        self,
        action: str,
        actor: str,
        raw_input: str = "",
    ) -> "Optional[web.Response]":
        """
        Run PolicyGate + CooldownTracker for a privileged action.

        Returns ``None`` when the request is allowed (caller proceeds normally).
        Returns a 403/429 ``web.Response`` when the request must be blocked.

        Always writes an audit record.

        Usage in a route handler::

            block = await gw.policy_check("mission.create", actor, raw_input=str(body))
            if block is not None:
                return block
        """
        from navig.gateway.policy_gate import PolicyDecision

        result = self.policy_gate.check(action, actor=actor)

        if result.is_denied:
            self.audit_log.record(
                actor=actor,
                action=action,
                policy=result.decision.value,
                status="denied",
                raw_input=raw_input,
                metadata={"matched_rule": result.matched_rule},
            )
            return web.json_response(
                {"ok": False, "error": f"Action '{action}' is denied by policy", "error_code": "policy_denied"},
                status=403,
            )

        if result.needs_approval:
            # Check cooldown first — approval-required actions also get a cooldown
            allowed, wait_s = self.cooldown.check_and_consume(action, actor=actor)
            if not allowed:
                self.audit_log.record(
                    actor=actor,
                    action=action,
                    policy=result.decision.value,
                    status="denied",
                    raw_input=raw_input,
                    metadata={"reason": "cooldown", "wait_s": round(wait_s, 1)},
                )
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"Cooldown active for '{action}' — retry in {wait_s:.0f}s",
                        "error_code": "cooldown",
                        "retry_after": round(wait_s, 1),
                    },
                    status=429,
                )
            self.audit_log.record(
                actor=actor,
                action=action,
                policy=result.decision.value,
                status="pending_approval",
                raw_input=raw_input,
                metadata={"matched_rule": result.matched_rule},
            )
            # Currently: log + allow. Future: queue for human approval UI.
            logger.warning(
                "[PolicyGate] Action '%s' by %s requires approval (logged, proceeding)", action, actor
            )
            return None

        # ALLOW — audit + emit billing event
        self.audit_log.record(
            actor=actor,
            action=action,
            policy=result.decision.value,
            status="success",
            raw_input=raw_input,
        )
        self.billing_emitter.emit(actor=actor, action=action)
        return None

    async def _init_autonomous_modules(self):
        """Initialize autonomous agent modules."""
        try:
            # Initialize approval manager
            from navig.approval import ApprovalManager, ApprovalPolicy
            from navig.approval.handlers import GatewayApprovalHandler
            
            policy = ApprovalPolicy.default()
            self.approval_manager = ApprovalManager(gateway=self, policy=policy)
            gateway_handler = GatewayApprovalHandler(self.approval_manager)
            self.approval_manager.register_handler('gateway', gateway_handler)
            logger.info("Approval manager initialized")
        except ImportError as e:
            logger.warning(f"Approval module not available: {e}")
        
        try:
            # Initialize browser controller (disabled by default)
            from navig.browser import BrowserController, BrowserConfig
            browser_cfg = self.config_manager.global_config.get('browser', {})
            self.browser_controller = BrowserController(BrowserConfig(
                headless=browser_cfg.get('headless', True),
                timeout_ms=browser_cfg.get('timeout', 30) * 1000,
            ))
            logger.info("Browser controller initialized (not started)")
        except ImportError as e:
            logger.warning(f"Browser module not available: {e}")
        
        try:
            # Initialize MCP client manager
            from navig.mcp import MCPClientManager
            self.mcp_client_manager = MCPClientManager()
            
            # Auto-connect to configured MCP servers
            mcp_servers = self.config_manager.global_config.get('mcp', {}).get('servers', [])
            for server_cfg in mcp_servers:
                try:
                    await self.mcp_client_manager.add_client(
                        name=server_cfg['name'],
                        command=server_cfg.get('command'),
                        url=server_cfg.get('url'),
                    )
                except Exception as e:
                    logger.warning(f"Failed to connect MCP server {server_cfg.get('name')}: {e}")
            
            logger.info(f"MCP client manager initialized with {len(self.mcp_client_manager.clients)} clients")
        except ImportError as e:
            logger.warning(f"MCP module not available: {e}")
        
        try:
            # Initialize webhook receiver
            from navig.webhooks import WebhookReceiver, WebhookSourceConfig
            self.webhook_receiver = WebhookReceiver()
            
            # Configure webhook sources
            webhook_cfg = self.config_manager.global_config.get('webhooks', {})
            for source_name, source_cfg in webhook_cfg.get('sources', {}).items():
                self.webhook_receiver.configure_source(WebhookSourceConfig(
                    name=source_name,
                    secret=source_cfg.get('secret', ''),
                    provider=source_cfg.get('provider', 'generic'),
                ))
            
            logger.info("Webhook receiver initialized")
        except ImportError as e:
            logger.warning(f"Webhook module not available: {e}")
        
        try:
            # Initialize task queue and worker
            from navig.tasks import TaskQueue, TaskWorker, WorkerConfig
            
            queue_path = str(self.storage_dir / 'task_queue.json')
            self.task_queue = TaskQueue(persist_path=queue_path)
            self.task_worker = TaskWorker(
                self.task_queue,
                WorkerConfig(max_concurrent=5)
            )
            
            # Register built-in task handlers
            self._register_task_handlers()
            
            await self.task_worker.start()
            logger.info("Task queue and worker initialized")
        except ImportError as e:
            logger.warning(f"Tasks module not available: {e}")

        # ── Flux Mesh: LAN-local peer discovery ──────────────────────
        try:
            mesh_cfg = self.config_manager.global_config.get("mesh", {})
            if mesh_cfg.get("enabled", True):
                from navig.mesh.registry import get_registry
                from navig.mesh.discovery import MeshDiscovery
                from navig.mesh.auth import load_secret as _load_mesh_secret
                self._mesh_registry = get_registry(self.storage_dir)
                _mesh_secret = _load_mesh_secret(mesh_cfg.get("secret"))
                if _mesh_secret:
                    logger.info("[mesh] BLAKE2b HMAC authentication active")
                self._mesh_discovery = MeshDiscovery(self._mesh_registry, secret=_mesh_secret)
                await self._mesh_discovery.start()
                logger.info("[mesh] Flux mesh discovery started")
            else:
                logger.info("[mesh] Mesh discovery disabled by config (mesh.enabled=false)")
        except Exception as e:
            logger.warning(f"[mesh] Mesh discovery init failed — node runs isolated: {e}")

    def _register_task_handlers(self):
        """Register built-in task handlers."""
        if not self.task_worker:
            return
        
        @self.task_worker.handler("run_command")
        async def handle_run_command(params):
            """Run a shell command (restricted to navig commands for safety)."""
            import shlex
            import subprocess
            command = params['command'].strip()
            # Security: only allow navig-prefixed commands or explicitly approved ones
            allowed_prefixes = ('navig ', 'python -m navig ')
            if not any(command.startswith(p) for p in allowed_prefixes):
                return {
                    "stdout": "",
                    "stderr": f"Blocked: only navig commands are allowed. Got: {command[:80]}",
                    "returncode": 1,
                }
            result = subprocess.run(
                shlex.split(command),
                shell=False,
                capture_output=True,
                text=True,
                timeout=params.get('timeout', 300)
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        
        @self.task_worker.handler("send_alert")
        async def handle_send_alert(params):
            """Send an alert message."""
            await self.send_alert(
                message=params['message'],
                channel=params.get('channel'),
                to=params.get('to')
            )
            return {"sent": True}
    
    async def _init_comms(self):
        """Wire the unified comms dispatcher (Prompt 5 integration)."""
        try:
            from navig.comms.dispatch import configure as comms_configure

            # Grab existing TelegramNotifier if the channel adapter set one up
            telegram_notifier = None
            try:
                from navig.gateway.channels.registry import ChannelRegistry
                registry = ChannelRegistry.instance() if hasattr(ChannelRegistry, 'instance') else None
                if registry:
                    tg = registry.get_adapter('telegram')
                    telegram_notifier = getattr(tg, '_notifier', None) if tg else None
            except Exception:
                pass

            # Optional Matrix bot
            matrix_bot = None
            comms_cfg = self.config_manager.global_config.get('comms', {})
            matrix_cfg = comms_cfg.get('matrix', {})
            if matrix_cfg.get('enabled', False):
                try:
                    from navig.comms.matrix import NavigMatrixBot
                    matrix_bot = NavigMatrixBot(matrix_cfg)
                    await matrix_bot.start()
                    logger.info("Matrix bot started via comms init")
                except ImportError:
                    logger.warning("matrix-nio not installed, Matrix channel disabled")
                except Exception as exc:
                    logger.warning("Matrix bot start failed: %s", exc)

            default_ch = comms_cfg.get('default_channel', 'telegram')
            comms_configure(
                telegram_notifier=telegram_notifier,
                matrix_notifier=matrix_bot,
                default_channel=default_ch,
            )
            logger.info("Unified comms dispatcher configured (default=%s)", default_ch)
        except ImportError:
            logger.debug("navig.comms not available, skipping comms init")
        except Exception as exc:
            logger.warning("Comms init failed: %s", exc)

    def _setup_webhook_routes(self):
        """Setup webhook routes from receiver."""
        if self.webhook_receiver:
            for method, path, handler in self.webhook_receiver.get_routes():
                if method == 'POST':
                    self._app.router.add_post(path, handler)
                elif method == 'GET':
                    self._app.router.add_get(path, handler)
    
    # ==================
    # Approval Handlers
    # ==================
    
    async def _handle_approval_pending(self, request: web.Request) -> web.Response:
        """List pending approval requests."""
        if not self.approval_manager:
            return web.json_response({"error": "Approval module not available"}, status=503)
        
        pending = self.approval_manager.list_pending()
        return web.json_response({
            "pending": [
                {
                    "id": req.id,
                    "action": req.action,
                    "level": req.level.value,
                    "description": req.description,
                    "agent_id": req.agent_id,
                    "created_at": req.created_at.isoformat(),
                }
                for req in pending
            ]
        })
    
    async def _handle_approval_request(self, request: web.Request) -> web.Response:
        """Create an approval request (from agent)."""
        if not self.approval_manager:
            return web.json_response({"error": "Approval module not available"}, status=503)
        
        try:
            data = await request.json()
            req = await self.approval_manager.request_approval(
                action=data['action'],
                description=data.get('description', ''),
                agent_id=data.get('agent_id', 'default'),
                timeout=data.get('timeout', 300.0),
            )
            
            return web.json_response({
                "request_id": req.id,
                "status": req.status.value,
                "level": req.level.value,
            })
        except asyncio.TimeoutError:
            return web.json_response({"error": "Approval timed out"}, status=408)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_approval_respond(self, request: web.Request) -> web.Response:
        """Respond to an approval request."""
        if not self.approval_manager:
            return web.json_response({"error": "Approval module not available"}, status=503)
        
        try:
            request_id = request.match_info['request_id']
            data = await request.json()
            
            approved = data.get('approved', False)
            reason = data.get('reason', '')
            
            success = await self.approval_manager.respond(
                request_id=request_id,
                approved=approved,
                reason=reason,
            )
            
            if success:
                return web.json_response({"success": True})
            else:
                return web.json_response({"error": "Request not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Browser Handlers
    # ==================
    
    async def _handle_browser_status(self, request: web.Request) -> web.Response:
        """Get browser status."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        return web.json_response({
            "started": self.browser_controller._browser is not None,
            "has_page": self.browser_controller._page is not None,
        })
    
    async def _handle_browser_navigate(self, request: web.Request) -> web.Response:
        """Navigate browser to URL."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        try:
            data = await request.json()
            await self.browser_controller.navigate(data['url'])
            return web.json_response({"success": True, "url": data['url']})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_browser_click(self, request: web.Request) -> web.Response:
        """Click element on page."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        try:
            data = await request.json()
            await self.browser_controller.click(data['selector'])
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_browser_fill(self, request: web.Request) -> web.Response:
        """Fill input field."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        try:
            data = await request.json()
            await self.browser_controller.fill(data['selector'], data['value'])
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_browser_screenshot(self, request: web.Request) -> web.Response:
        """Take browser screenshot."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        try:
            data = await request.json() if request.can_read_body else {}
            path = await self.browser_controller.screenshot(
                path=data.get('path'),
                full_page=data.get('full_page', False),
            )
            return web.json_response({"success": True, "path": path})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_browser_stop(self, request: web.Request) -> web.Response:
        """Stop browser."""
        if not self.browser_controller:
            return web.json_response({"error": "Browser module not available"}, status=503)
        
        try:
            await self.browser_controller.stop()
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # MCP Handlers
    # ==================
    
    async def _handle_mcp_clients(self, request: web.Request) -> web.Response:
        """List MCP clients."""
        if not self.mcp_client_manager:
            return web.json_response({"error": "MCP module not available"}, status=503)
        
        clients = []
        for name, client in self.mcp_client_manager.clients.items():
            clients.append({
                "name": name,
                "connected": client.connected,
            })
        
        return web.json_response({"clients": clients})
    
    async def _handle_mcp_tools(self, request: web.Request) -> web.Response:
        """List all available MCP tools."""
        if not self.mcp_client_manager:
            return web.json_response({"error": "MCP module not available"}, status=503)
        
        tools = self.mcp_client_manager.list_tools()
        return web.json_response({
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "client": t.client_name,
                }
                for t in tools
            ]
        })
    
    async def _handle_mcp_call_tool(self, request: web.Request) -> web.Response:
        """Call an MCP tool."""
        if not self.mcp_client_manager:
            return web.json_response({"error": "MCP module not available"}, status=503)
        
        try:
            tool_name = request.match_info['tool_name']
            data = await request.json()
            
            result = await self.mcp_client_manager.call_tool(
                tool_name=tool_name,
                arguments=data.get('arguments', {}),
            )
            
            return web.json_response({"success": True, "result": result})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_mcp_connect(self, request: web.Request) -> web.Response:
        """Connect to an MCP server."""
        if not self.mcp_client_manager:
            return web.json_response({"error": "MCP module not available"}, status=503)
        
        try:
            data = await request.json()
            client = await self.mcp_client_manager.add_client(
                name=data['name'],
                command=data.get('command'),
                url=data.get('url'),
            )
            return web.json_response({
                "success": True,
                "name": data['name'],
                "connected": client.connected,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_mcp_disconnect(self, request: web.Request) -> web.Response:
        """Disconnect an MCP client."""
        if not self.mcp_client_manager:
            return web.json_response({"error": "MCP module not available"}, status=503)
        
        try:
            data = await request.json()
            await self.mcp_client_manager.remove_client(data['name'])
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Task Queue Handlers
    # ==================
    
    async def _handle_tasks_list(self, request: web.Request) -> web.Response:
        """List tasks."""
        if not self.task_queue:
            return web.json_response({"error": "Tasks module not available"}, status=503)
        
        try:
            from navig.tasks import TaskStatus
            
            status_filter = request.query.get('status')
            status = TaskStatus(status_filter) if status_filter else None
            limit = int(request.query.get('limit', 50))
            
            tasks = await self.task_queue.list_tasks(status=status, limit=limit)
            return web.json_response({
                "tasks": [t.to_dict() for t in tasks]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_tasks_add(self, request: web.Request) -> web.Response:
        """Add a task to the queue."""
        if not self.task_queue:
            return web.json_response({"error": "Tasks module not available"}, status=503)
        
        try:
            from navig.tasks import Task
            
            data = await request.json()
            task = Task(
                name=data['name'],
                handler=data['handler'],
                params=data.get('params', {}),
                priority=data.get('priority', 50),
                dependencies=data.get('dependencies', []),
                max_retries=data.get('max_retries', 0),
                timeout=data.get('timeout'),
            )
            
            task = await self.task_queue.add(task)
            return web.json_response(task.to_dict())
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_tasks_stats(self, request: web.Request) -> web.Response:
        """Get task queue stats."""
        if not self.task_queue:
            return web.json_response({"error": "Tasks module not available"}, status=503)
        
        stats = self.task_queue.get_stats()
        if self.task_worker:
            stats['worker'] = self.task_worker.get_stats()
        
        return web.json_response(stats)
    
    async def _handle_tasks_get(self, request: web.Request) -> web.Response:
        """Get a specific task."""
        if not self.task_queue:
            return web.json_response({"error": "Tasks module not available"}, status=503)
        
        try:
            task_id = request.match_info['task_id']
            task = await self.task_queue.get(task_id)
            if not task:
                return web.json_response({"error": "Task not found"}, status=404)
            return web.json_response(task.to_dict())
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_tasks_cancel(self, request: web.Request) -> web.Response:
        """Cancel a task."""
        if not self.task_queue:
            return web.json_response({"error": "Tasks module not available"}, status=503)
        
        try:
            task_id = request.match_info['task_id']
            task = await self.task_queue.cancel(task_id)
            return web.json_response({"success": True, "task": task.to_dict()})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Memory Endpoints
    # ==================
    
    def _get_memory_store(self):
        """Get or create conversation store."""
        if not hasattr(self, '_conversation_store'):
            from navig.memory import ConversationStore
            db_path = self.config.storage_dir / "memory.db"
            self._conversation_store = ConversationStore(db_path)
        return self._conversation_store
    
    def _get_knowledge_base(self):
        """Get or create knowledge base."""
        if not hasattr(self, '_knowledge_base'):
            from navig.memory import KnowledgeBase
            db_path = self.config.storage_dir / "knowledge.db"
            self._knowledge_base = KnowledgeBase(db_path, embedding_provider=None)
        return self._knowledge_base
    
    async def _handle_memory_sessions(self, request: web.Request) -> web.Response:
        """List conversation sessions."""
        try:
            store = self._get_memory_store()
            limit = int(request.query.get('limit', '50'))
            sessions = store.list_sessions(limit=limit)
            
            return web.json_response({
                "sessions": [
                    {
                        "session_key": s.session_key,
                        "message_count": s.message_count,
                        "total_tokens": s.total_tokens,
                        "created_at": s.created_at.isoformat(),
                        "updated_at": s.updated_at.isoformat(),
                    }
                    for s in sessions
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_history(self, request: web.Request) -> web.Response:
        """Get conversation history for a session."""
        try:
            store = self._get_memory_store()
            session_key = request.match_info['session_key']
            limit = int(request.query.get('limit', '100'))
            
            messages = store.get_history(session_key, limit=limit)
            
            return web.json_response({
                "session_key": session_key,
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "timestamp": m.timestamp.isoformat(),
                        "token_count": m.token_count,
                    }
                    for m in messages
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_delete_session(self, request: web.Request) -> web.Response:
        """Delete a conversation session."""
        try:
            store = self._get_memory_store()
            session_key = request.match_info['session_key']
            
            if store.delete_session(session_key):
                return web.json_response({"success": True, "session_key": session_key})
            else:
                return web.json_response({"error": "Session not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_add_message(self, request: web.Request) -> web.Response:
        """Add a message to conversation history."""
        try:
            from navig.memory import Message
            
            store = self._get_memory_store()
            data = await request.json()
            
            message = Message(
                session_key=data['session_key'],
                role=data.get('role', 'user'),
                content=data['content'],
                token_count=data.get('token_count', 0),
                metadata=data.get('metadata', {}),
            )
            
            stored = store.add_message(message)
            
            return web.json_response({
                "success": True,
                "message": {
                    "id": stored.id,
                    "session_key": stored.session_key,
                    "role": stored.role,
                    "timestamp": stored.timestamp.isoformat(),
                }
            })
        except KeyError as e:
            return web.json_response({"error": f"Missing required field: {e}"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_knowledge_list(self, request: web.Request) -> web.Response:
        """List knowledge base entries."""
        try:
            from navig.memory import KnowledgeEntry
            
            kb = self._get_knowledge_base()
            limit = int(request.query.get('limit', '50'))
            tag = request.query.get('tag')
            source = request.query.get('source')
            
            if tag:
                entries = kb.list_by_tag(tag, limit=limit)
            elif source:
                entries = kb.list_by_source(source, limit=limit)
            else:
                raw_entries = kb.export_entries()[:limit]
                entries = [KnowledgeEntry.from_dict(e) for e in raw_entries]
            
            return web.json_response({
                "entries": [
                    {
                        "id": e.id,
                        "key": e.key,
                        "content": e.content[:200],
                        "tags": e.tags,
                        "source": e.source,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in entries
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_knowledge_add(self, request: web.Request) -> web.Response:
        """Add or update a knowledge entry."""
        try:
            from navig.memory import KnowledgeEntry
            
            kb = self._get_knowledge_base()
            data = await request.json()
            
            entry = KnowledgeEntry(
                key=data['key'],
                content=data['content'],
                summary=data.get('summary'),
                tags=data.get('tags', []),
                source=data.get('source', 'api'),
            )
            
            if data.get('ttl_hours'):
                from datetime import datetime, timedelta
                entry.expires_at = datetime.utcnow() + timedelta(hours=data['ttl_hours'])
            
            stored = kb.upsert(entry, compute_embedding=False)
            
            return web.json_response({
                "success": True,
                "entry": {
                    "id": stored.id,
                    "key": stored.key,
                }
            })
        except KeyError as e:
            return web.json_response({"error": f"Missing required field: {e}"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_knowledge_search(self, request: web.Request) -> web.Response:
        """Search knowledge base."""
        try:
            kb = self._get_knowledge_base()
            query = request.query.get('q', '')
            limit = int(request.query.get('limit', '10'))
            tags = request.query.get('tags', '').split(',') if request.query.get('tags') else None
            
            results = kb.text_search(query, limit=limit, tags=tags)
            
            return web.json_response({
                "query": query,
                "results": [
                    {
                        "id": e.id,
                        "key": e.key,
                        "content": e.content[:300],
                        "tags": e.tags,
                    }
                    for e in results
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _handle_memory_stats(self, request: web.Request) -> web.Response:
        """Get memory usage statistics."""
        try:
            store = self._get_memory_store()
            kb = self._get_knowledge_base()
            
            sessions = store.list_sessions(limit=1000)
            
            return web.json_response({
                "conversation": {
                    "sessions": len(sessions),
                    "total_messages": sum(s.message_count for s in sessions),
                    "total_tokens": sum(s.total_tokens for s in sessions),
                },
                "knowledge": {
                    "entries": kb.count(),
                }
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Proactive Agent
    # ==================
    
    async def _handle_proactive_status(self, request: web.Request) -> web.Response:
        """Get status of proactive engine."""
        engine = get_proactive_engine()
        return web.json_response({
            "started": engine.running,
            "last_check": engine.last_check.isoformat() if engine.last_check else None,
            "last_check_status": engine.last_check_status,
            "last_error": engine.last_error,
            "providers": engine.provider_status
        })

    async def _handle_proactive_start(self, request: web.Request) -> web.Response:
        """Start proactive engine loop."""
        engine = get_proactive_engine()
        if not engine.running:
            # Run in background task to avoid blocking
            asyncio.create_task(engine.start())
            return web.json_response({"status": "started"})
        return web.json_response({"status": "already_running"})

    async def _handle_proactive_stop(self, request: web.Request) -> web.Response:
        """Stop proactive engine loop."""
        engine = get_proactive_engine()
        if engine.running:
            await engine.stop()
            return web.json_response({"status": "stopped"})
        return web.json_response({"status": "not_running"})

    async def _handle_proactive_check(self, request: web.Request) -> web.Response:
        """Trigger immediate proactive check."""
        engine = get_proactive_engine()
        # Fire and forget or wait? Let's fire and forget but return indication
        if engine.is_checking:
            return web.json_response({"status": "busy"}, status=409)
            
        asyncio.create_task(engine.run_checks(None))
        return web.json_response({"status": "triggered"})

    async def _handle_engagement_status(self, request: web.Request) -> web.Response:
        """Get proactive engagement system status."""
        try:
            engine = get_proactive_engine()
            coordinator = engine._get_engagement_coordinator()
            state = coordinator.state
            
            return web.json_response({
                "enabled": coordinator.config.enabled,
                "operator_state": state.get_operator_state().value,
                "time_of_day": state.get_time_of_day().value,
                "within_active_hours": state.is_within_active_hours(),
                "stats": {
                    "total_messages": state.stats.total_messages,
                    "total_commands": state.stats.total_commands,
                    "features_used": len(state.stats.features_used),
                    "last_greeting": state.stats.last_greeting,
                    "last_checkin": state.stats.last_checkin,
                    "last_capability_promo": state.stats.last_capability_promo,
                    "last_feedback_ask": state.stats.last_feedback_ask,
                },
                "daily_sends": len(coordinator._daily_sends),
                "max_daily": coordinator.config.max_proactive_per_day,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_engagement_tick(self, request: web.Request) -> web.Response:
        """Trigger immediate engagement evaluation tick."""
        try:
            engine = get_proactive_engine()
            coordinator = engine._get_engagement_coordinator()
            result = coordinator.engagement_tick()
            
            if result:
                # Deliver through gateway if we have a telegram channel
                if 'telegram' in self.channels:
                    await self.deliver_message(
                        channel='telegram',
                        to=None,  # Default recipient
                        content=result.message,
                    )
                
                return web.json_response({
                    "status": "sent",
                    "action": result.action.value,
                    "message": result.message,
                    "priority": result.priority,
                })
            
            return web.json_response({"status": "no_action"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    # ==================
    # Agent Interface
    # ==================
    
    async def run_agent_turn(
        self,
        agent_id: str,
        session_key: str,
        message: str,
        is_heartbeat: bool = False,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Run a single agent turn.
        
        Args:
            agent_id: Agent identifier
            session_key: Session key for context
            message: Message to process
            is_heartbeat: Whether this is a heartbeat run
            model: Optional model override
            
        Returns:
            Agent response text
        """
        # Get or create session
        session = await self.sessions.get_session(session_key)
        
        # Add user message to session
        await self.sessions.add_message(session_key, 'user', message)
        
        # Build context
        context = await self._build_agent_context(agent_id, session, is_heartbeat, message=message)
        
        # Run AI
        response = await self._call_ai(
            context=context,
            message=message,
            model=model,
            **kwargs
        )
        
        # Add assistant response to session
        await self.sessions.add_message(session_key, 'assistant', response)
        
        return response
    
    async def _build_agent_context(
        self,
        agent_id: str,
        session: 'Session',
        is_heartbeat: bool,
        message: str = "",
    ) -> Dict[str, Any]:
        """Build agent context from workspace files."""
        workspace_dir = self.storage_dir / 'workspace'
        workspace_candidates = [USER_WORKSPACE_DIR, workspace_dir]
        
        context = {
            "agent_id": agent_id,
            "is_heartbeat": is_heartbeat,
            "session_messages": session.messages[-20:],  # Last 20 messages
            "files": {}
        }
        
        # Load workspace files
        files_to_load = ['AGENTS.md', 'SOUL.md', 'USER.md', 'TOOLS.md']
        
        if is_heartbeat:
            files_to_load.append('HEARTBEAT.md')
        else:
            files_to_load.append('MEMORY.md')
        
        for filename in files_to_load:
            for base_dir in workspace_candidates:
                filepath = base_dir / filename
                if filepath.exists():
                    try:
                        context['files'][filename] = filepath.read_text(encoding='utf-8')
                        break
                    except Exception as e:
                        logger.warning(f"Failed to read {filename}: {e}")
        
        # Load today's memory log
        today = datetime.now().strftime('%Y-%m-%d')
        for base_dir in workspace_candidates:
            memory_log = base_dir / 'memory' / f'{today}.md'
            if memory_log.exists():
                try:
                    context['files'][f'memory/{today}.md'] = memory_log.read_text(encoding='utf-8')
                    break
                except Exception:
                    pass

        # ── Memory enrichment (best-effort, never blocks the turn) ──────────
        try:
            query = (message or "").strip()[:300]
            kb = self._get_knowledge_base()
            if query and kb:
                kb_results = kb.text_search(query, limit=5)
                if kb_results:
                    context['memory_context'] = "\n".join(
                        f"- {e.key}: {e.content[:150]}"
                        for e in kb_results
                    )
        except Exception as _mem_err:
            logger.debug("[memory] KB search skipped: %s", _mem_err)

        try:
            from navig.memory.manager import get_memory_manager
            mgr = get_memory_manager()
            profile_ctx = mgr.get_user_context() if hasattr(mgr, 'get_user_context') else None
            if profile_ctx:
                context['user_profile'] = profile_ctx
        except Exception as _profile_err:
            logger.debug("[memory] User profile skipped: %s", _profile_err)

        return context
    
    async def _call_ai(
        self,
        context: Dict[str, Any],
        message: str,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """Call AI with context and message."""
        from navig.ai import ask_ai_with_context
        
        # Build system prompt from context
        system_prompt = self._build_system_prompt(context)
        
        # Build conversation history
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in context.get("session_messages", [])
        ]
        
        # Call AI
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ask_ai_with_context(
                    prompt=message,
                    system_prompt=system_prompt,
                    history=history,
                    model=model
                )
            )
            return response
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return f"Error: {e}"
    
    def _build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build system prompt from context files."""
        parts = []
        
        # Add SOUL.md (personality)
        if 'SOUL.md' in context.get('files', {}):
            parts.append(f"# Your Personality\n{context['files']['SOUL.md']}")
        
        # Add USER.md (user info)
        if 'USER.md' in context.get('files', {}):
            parts.append(f"# About Your Human\n{context['files']['USER.md']}")
        
        # Add AGENTS.md (instructions)
        if 'AGENTS.md' in context.get('files', {}):
            parts.append(f"# Instructions\n{context['files']['AGENTS.md']}")
        
        # Add TOOLS.md (config)
        if 'TOOLS.md' in context.get('files', {}):
            parts.append(f"# Available Tools & Config\n{context['files']['TOOLS.md']}")
        
        # Add HEARTBEAT.md for heartbeat runs
        if context.get('is_heartbeat') and 'HEARTBEAT.md' in context.get('files', {}):
            parts.append(f"# Heartbeat Checklist\n{context['files']['HEARTBEAT.md']}")
        
        # Add today's memory
        for key, value in context.get('files', {}).items():
            if key.startswith('memory/'):
                parts.append(f"# Today's Log\n{value}")

        # Add persistent memory context (knowledge base search results)
        if context.get('memory_context'):
            parts.append(f"# Relevant Memory\n{context['memory_context']}")

        # Add user profile
        if context.get('user_profile'):
            parts.append(f"# User Profile\n{context['user_profile']}")

        return "\n\n---\n\n".join(parts)
    
    # ==================
    # Delivery Interface
    # ==================
    
    async def send_alert(self, message: str, channel: str = None, to: str = None):
        """Send alert message to a channel."""
        # Determine channel
        if not channel:
            channel = 'telegram'  # Default
        
        # Get channel handler
        handler = self.channels.get(channel)
        if handler:
            await handler.send(message, to=to)
        else:
            logger.warning(f"No handler for channel: {channel}")
    
    async def deliver_message(
        self, 
        channel: str, 
        to: Optional[str], 
        content: str
    ):
        """Deliver message to a specific channel/recipient."""
        handler = self.channels.get(channel)
        if handler:
            await handler.send(content, to=to)
        else:
            logger.warning(f"Cannot deliver to channel: {channel}")
    
    async def enqueue_system_event(self, text: str, agent_id: str = 'default'):
        """Enqueue a system event for processing."""
        await self.system_events.enqueue(text=text, agent_id=agent_id)
    
    async def request_heartbeat_now(self, agent_id: str = 'default'):
        """Request immediate heartbeat run."""
        if self.heartbeat_runner:
            await self.heartbeat_runner.request_run_now()
    
    def get_queue_size(self) -> int:
        """Get current message queue size."""
        return self._message_queue.qsize()


def run_gateway():
    """Entry point for running gateway as standalone process."""
    gateway = NavigGateway()
    
    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def signal_handler():
        loop.create_task(gateway.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        loop.run_until_complete(gateway.start())
    except KeyboardInterrupt:
        loop.run_until_complete(gateway.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    run_gateway()
