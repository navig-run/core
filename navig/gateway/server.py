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
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import aiohttp
    from aiohttp import web

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

from navig.agent.proactive.engine import get_proactive_engine
from navig.config import get_config_manager
from navig.debug_logger import get_debug_logger
from navig.gateway.audit_log import AuditLog
from navig.gateway.billing_emitter import BillingEmitter
from navig.gateway.channel_router import ChannelRouter
from navig.gateway.config_watcher import ConfigWatcher
from navig.gateway.cooldown import CooldownTracker
from navig.gateway.policy_gate import PolicyGate
from navig.gateway.session_manager import Session, SessionManager
from navig.gateway.system_events import SystemEventQueue
from navig.workspace_ownership import USER_WORKSPACE_DIR

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

    def __init__(self, raw_config: dict[str, Any] = None):
        raw_config = raw_config or {}
        gateway_cfg = raw_config.get("gateway", {})

        self.enabled = gateway_cfg.get("enabled", True)
        self.port = gateway_cfg.get("port", 8789)
        self.host = gateway_cfg.get("host", "127.0.0.1")
        self.auth_token = gateway_cfg.get("auth", {}).get("token")

        # Storage directory
        storage = gateway_cfg.get("storage_dir", "~/.navig")
        self.storage_dir = Path(storage).expanduser()

        # Heartbeat defaults
        heartbeat_cfg = raw_config.get("heartbeat", {})
        self.heartbeat_enabled = heartbeat_cfg.get("enabled", True)
        self.heartbeat_interval = heartbeat_cfg.get("interval", "30m")

        # Agent config
        agents_cfg = raw_config.get("agents", {})
        self.default_agent = agents_cfg.get("default", "navig")
        self.agents = agents_cfg.get("list", [])


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

    def __init__(self, config: GatewayConfig | None = None):
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
        self.config_watcher: ConfigWatcher | None = None

        # State
        self.running = False
        self.start_time: datetime | None = None
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

        # Components initialized later
        self.heartbeat_runner = None
        self.cron_service = None
        self.channels: dict[str, Any] = {}

        # Queue for pending messages
        # Bounded queue — prevents OOM on message floods (P1-2)
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queue_task: asyncio.Task | None = None

        # New autonomous modules (lazy initialized)
        self.approval_manager = None
        self.browser_controller = None
        self.mcp_client_manager = None
        self.webhook_receiver = None
        self.task_queue = None
        self.task_worker = None

        # Rate limiter auth state — populated by middleware factory in _start_http_server
        self._auth_attempts: dict[str, list] = {}

        # ── Safety & Audit ─────────────────────────────────────────────────
        raw_cfg = self.config_manager.global_config or {}
        raw_gateway_cfg: dict[str, Any] = (
            raw_cfg.get("gateway", {}) if isinstance(raw_cfg, dict) else {}
        )
        self.policy_gate = PolicyGate.from_config(raw_gateway_cfg)
        self.audit_log = AuditLog()
        self.billing_emitter = BillingEmitter()
        self.cooldown = CooldownTracker(default_cooldown_seconds=30.0)

        # Bind route handler closures as gateway methods for direct access/testing
        self._bind_route_methods()

        logger.info(
            "NavigGateway initialized",
            extra={
                "port": self.config.port,
                "host": self.config.host,
                "storage_dir": str(self.storage_dir),
            },
        )

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

            get_registry().initialize(self.storage_dir / "workspace")
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

        logger.info(
            f"✅ NAVIG Gateway started on {self.config.host}:{self.config.port}"
        )
        print(
            f"\n✅ NAVIG Gateway running at http://{self.config.host}:{self.config.port}"
        )
        print(
            f"   Heartbeat: {'enabled' if self.config.heartbeat_enabled else 'disabled'}"
        )
        print(f"   Storage: {self.storage_dir}")
        print("\n   Press Ctrl+C to stop\n")

        # Keep running
        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown
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
                pass  # task cancelled; expected during shutdown

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
        from navig.gateway.middleware import (
            make_cors_middleware,
            make_rate_limit_middleware,
        )

        rate_mw, self._auth_attempts = make_rate_limit_middleware(
            window=60, max_failures=5
        )
        cors_mw = make_cors_middleware()
        self._app = web.Application(middlewares=[rate_mw, cors_mw])

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

        from navig.heartbeat import HeartbeatConfig, HeartbeatRunner

        # Get heartbeat config from global config
        heartbeat_dict = self.config_manager.global_config.get("heartbeat", {})
        heartbeat_config = HeartbeatConfig.from_dict(heartbeat_dict)

        self.heartbeat_runner = HeartbeatRunner(self, heartbeat_config)
        await self.heartbeat_runner.start()

    async def _start_cron(self):
        """Start cron service."""
        from navig.scheduler import CronConfig, CronService

        # Get cron config from global config
        cron_dict = self.config_manager.global_config.get("cron", {})
        cron_config = CronConfig.from_dict(cron_dict)

        # Cron service needs storage path
        storage_path = self.config_manager.global_config_dir / "scheduler"
        storage_path.mkdir(exist_ok=True)

        self.cron_service = CronService(self, storage_path, cron_config)
        await self.cron_service.start()

    async def _on_config_reload(self, new_config: dict[str, Any]):
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
                logger.info(
                    f"Heartbeat interval changed: {old_interval} → {new_interval}"
                )
                await self.heartbeat_runner.update_config()

    async def _process_message_queue(self):
        """Process queued messages."""
        while self.running:
            try:
                message = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                await self._process_message(message)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error processing message from queue")  # P1-5

    async def _process_message(self, message: dict[str, Any]):
        """Process a single message (with 60 s timeout — P1-1)."""
        try:
            response = await asyncio.wait_for(
                self.router.route_message(
                    channel=message["channel"],
                    user_id=message["user_id"],
                    message=message["message"],
                    metadata=message.get("metadata", {}),
                ),
                timeout=60.0,
            )

            # Store response callback if provided
            if "callback" in message:
                message["callback"](response)

        except asyncio.TimeoutError:
            logger.error(
                "route_message timed out after 60 s for channel=%s user=%s",
                message.get("channel"),
                message.get("user_id"),
            )
            if "callback" in message:
                message["callback"]({"error": "Request timed out"})
        except Exception:
            logger.exception("Failed to process message")  # P1-5

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
                logger.info(
                    "[Gateway] mesh_token auto-generated and saved to persistent config"
                )
            else:
                self.config_manager.global_config["gateway"] = gw_cfg
                logger.info(
                    "[Gateway] mesh_token auto-generated and updated in-memory config"
                )

    # ------------------------------------------------------------------
    # Policy / Audit helpers
    # ------------------------------------------------------------------

    async def policy_check(
        self,
        action: str,
        actor: str,
        raw_input: str = "",
    ) -> web.Response | None:
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
                {
                    "ok": False,
                    "error": f"Action '{action}' is denied by policy",
                    "error_code": "policy_denied",
                },
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
                "[PolicyGate] Action '%s' by %s requires approval (logged, proceeding)",
                action,
                actor,
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
            self.approval_manager.register_handler("gateway", gateway_handler)
            logger.info("Approval manager initialized")
        except ImportError as e:
            logger.warning(f"Approval module not available: {e}")

        try:
            # Initialize browser controller (disabled by default)
            from navig.browser import BrowserConfig, BrowserController

            browser_cfg = self.config_manager.global_config.get("browser", {})
            self.browser_controller = BrowserController(
                BrowserConfig(
                    headless=browser_cfg.get("headless", True),
                    timeout_ms=browser_cfg.get("timeout", 30) * 1000,
                )
            )
            logger.info("Browser controller initialized (not started)")
        except ImportError as e:
            logger.warning(f"Browser module not available: {e}")

        try:
            # Initialize MCP client manager
            from navig.mcp import MCPClientManager

            self.mcp_client_manager = MCPClientManager()

            # Auto-connect to configured MCP servers
            mcp_servers = self.config_manager.global_config.get("mcp", {}).get(
                "servers", []
            )
            for server_cfg in mcp_servers:
                try:
                    await self.mcp_client_manager.add_client(
                        name=server_cfg["name"],
                        command=server_cfg.get("command"),
                        url=server_cfg.get("url"),
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to connect MCP server {server_cfg.get('name')}: {e}"
                    )

            logger.info(
                f"MCP client manager initialized with {len(self.mcp_client_manager.clients)} clients"
            )
        except ImportError as e:
            logger.warning(f"MCP module not available: {e}")

        try:
            # Initialize webhook receiver
            from navig.webhooks import WebhookReceiver, WebhookSourceConfig

            self.webhook_receiver = WebhookReceiver()

            # Configure webhook sources
            webhook_cfg = self.config_manager.global_config.get("webhooks", {})
            for source_name, source_cfg in webhook_cfg.get("sources", {}).items():
                self.webhook_receiver.configure_source(
                    WebhookSourceConfig(
                        name=source_name,
                        secret=source_cfg.get("secret", ""),
                        provider=source_cfg.get("provider", "generic"),
                    )
                )

            logger.info("Webhook receiver initialized")
        except ImportError as e:
            logger.warning(f"Webhook module not available: {e}")

        try:
            # Initialize task queue and worker
            from navig.tasks import TaskQueue, TaskWorker, WorkerConfig

            queue_path = str(self.storage_dir / "task_queue.json")
            self.task_queue = TaskQueue(persist_path=queue_path)
            self.task_worker = TaskWorker(
                self.task_queue, WorkerConfig(max_concurrent=5)
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
                from navig.mesh.auth import load_secret as _load_mesh_secret
                from navig.mesh.discovery import MeshDiscovery
                from navig.mesh.registry import get_registry

                self._mesh_registry = get_registry(self.storage_dir)
                _mesh_secret = _load_mesh_secret(mesh_cfg.get("secret"))
                if _mesh_secret:
                    logger.info("[mesh] BLAKE2b HMAC authentication active")
                self._mesh_discovery = MeshDiscovery(
                    self._mesh_registry, secret=_mesh_secret
                )
                await self._mesh_discovery.start()
                logger.info("[mesh] Flux mesh discovery started")
            else:
                logger.info(
                    "[mesh] Mesh discovery disabled by config (mesh.enabled=false)"
                )
        except Exception as e:
            logger.warning(
                f"[mesh] Mesh discovery init failed — node runs isolated: {e}"
            )

    def _register_task_handlers(self):
        """Register built-in task handlers."""
        if not self.task_worker:
            return

        @self.task_worker.handler("run_command")
        async def handle_run_command(params):
            """Run a shell command (restricted to navig commands for safety)."""
            import shlex
            import subprocess

            command = params["command"].strip()
            # Security: only allow navig-prefixed commands or explicitly approved ones
            allowed_prefixes = ("navig ", "python -m navig ")
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
                timeout=params.get("timeout", 300),
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
                message=params["message"],
                channel=params.get("channel"),
                to=params.get("to"),
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

                registry = (
                    ChannelRegistry.instance()
                    if hasattr(ChannelRegistry, "instance")
                    else None
                )
                if registry:
                    tg = registry.get_adapter("telegram")
                    telegram_notifier = getattr(tg, "_notifier", None) if tg else None
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            # Optional Matrix bot
            matrix_bot = None
            comms_cfg = self.config_manager.global_config.get("comms", {})
            matrix_cfg = comms_cfg.get("matrix", {})
            if matrix_cfg.get("enabled", False):
                try:
                    from navig.comms.matrix import NavigMatrixBot

                    matrix_bot = NavigMatrixBot(matrix_cfg)
                    await matrix_bot.start()
                    logger.info("Matrix bot started via comms init")
                except ImportError:
                    logger.warning("matrix-nio not installed, Matrix channel disabled")
                except Exception as exc:
                    logger.warning("Matrix bot start failed: %s", exc)

            default_ch = comms_cfg.get("default_channel", "telegram")
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
                if method == "POST":
                    self._app.router.add_post(path, handler)
                elif method == "GET":
                    self._app.router.add_get(path, handler)

    def _get_memory_store(self):
        """Get or create conversation store."""
        if not hasattr(self, "_conversation_store"):
            from navig.memory import ConversationStore

            db_path = self.config.storage_dir / "memory.db"
            self._conversation_store = ConversationStore(db_path)
        return self._conversation_store

    def _get_knowledge_base(self):
        """Get or create knowledge base."""
        if not hasattr(self, "_knowledge_base"):
            from navig.memory import KnowledgeBase

            db_path = self.config.storage_dir / "knowledge.db"
            self._knowledge_base = KnowledgeBase(db_path, embedding_provider=None)
        return self._knowledge_base

    # ==================
    # Agent Interface
    # ==================

    async def run_agent_turn(
        self,
        agent_id: str,
        session_key: str,
        message: str,
        is_heartbeat: bool = False,
        model: str | None = None,
        **kwargs,
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
        await self.sessions.add_message(session_key, "user", message)

        # Build context
        context = await self._build_agent_context(
            agent_id, session, is_heartbeat, message=message
        )

        # Run AI
        response = await self._call_ai(
            context=context, message=message, model=model, **kwargs
        )

        # Add assistant response to session
        await self.sessions.add_message(session_key, "assistant", response)

        return response

    async def _build_agent_context(
        self,
        agent_id: str,
        session: Session,
        is_heartbeat: bool,
        message: str = "",
    ) -> dict[str, Any]:
        """Build agent context from workspace files."""
        workspace_dir = self.storage_dir / "workspace"
        workspace_candidates = [USER_WORKSPACE_DIR, workspace_dir]

        context = {
            "agent_id": agent_id,
            "is_heartbeat": is_heartbeat,
            "session_messages": session.messages[-20:],  # Last 20 messages
            "files": {},
        }

        # Load workspace files
        files_to_load = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]

        if is_heartbeat:
            files_to_load.append("HEARTBEAT.md")
        else:
            files_to_load.append("MEMORY.md")

        for filename in files_to_load:
            for base_dir in workspace_candidates:
                filepath = base_dir / filename
                if filepath.exists():
                    try:
                        context["files"][filename] = filepath.read_text(
                            encoding="utf-8"
                        )
                        break
                    except Exception as e:
                        logger.warning(f"Failed to read {filename}: {e}")

        # Load today's memory log
        today = datetime.now().strftime("%Y-%m-%d")
        for base_dir in workspace_candidates:
            memory_log = base_dir / "memory" / f"{today}.md"
            if memory_log.exists():
                try:
                    context["files"][f"memory/{today}.md"] = memory_log.read_text(
                        encoding="utf-8"
                    )
                    break
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # ── Memory enrichment (best-effort, never blocks the turn) ──────────
        try:
            query = (message or "").strip()[:300]
            kb = self._get_knowledge_base()
            if query and kb:
                kb_results = kb.text_search(query, limit=5)
                if kb_results:
                    context["memory_context"] = "\n".join(
                        f"- {e.key}: {e.content[:150]}" for e in kb_results
                    )
        except Exception as _mem_err:
            logger.debug("[memory] KB search skipped: %s", _mem_err)

        try:
            from navig.memory.manager import get_memory_manager

            mgr = get_memory_manager()
            profile_ctx = (
                mgr.get_user_context() if hasattr(mgr, "get_user_context") else None
            )
            if profile_ctx:
                context["user_profile"] = profile_ctx
        except Exception as _profile_err:
            logger.debug("[memory] User profile skipped: %s", _profile_err)

        return context

    async def _call_ai(
        self,
        context: dict[str, Any],
        message: str,
        model: str | None = None,
        **kwargs,
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
                    model=model,
                ),
            )
            return response
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return f"Error: {e}"

    def _build_system_prompt(self, context: dict[str, Any]) -> str:
        """Build system prompt from context files."""
        parts = []

        # Add SOUL.md (personality)
        if "SOUL.md" in context.get("files", {}):
            parts.append(f"# Your Personality\n{context['files']['SOUL.md']}")

        # Add USER.md (user info)
        if "USER.md" in context.get("files", {}):
            parts.append(f"# About Your Human\n{context['files']['USER.md']}")

        # Add AGENTS.md (instructions)
        if "AGENTS.md" in context.get("files", {}):
            parts.append(f"# Instructions\n{context['files']['AGENTS.md']}")

        # Add TOOLS.md (config)
        if "TOOLS.md" in context.get("files", {}):
            parts.append(f"# Available Tools & Config\n{context['files']['TOOLS.md']}")

        # Add HEARTBEAT.md for heartbeat runs
        if context.get("is_heartbeat") and "HEARTBEAT.md" in context.get("files", {}):
            parts.append(f"# Heartbeat Checklist\n{context['files']['HEARTBEAT.md']}")

        # Add today's memory
        for key, value in context.get("files", {}).items():
            if key.startswith("memory/"):
                parts.append(f"# Today's Log\n{value}")

        # Add persistent memory context (knowledge base search results)
        if context.get("memory_context"):
            parts.append(f"# Relevant Memory\n{context['memory_context']}")

        # Add user profile
        if context.get("user_profile"):
            parts.append(f"# User Profile\n{context['user_profile']}")

        return "\n\n---\n\n".join(parts)

    # ==================
    # Delivery Interface
    # ==================

    async def send_alert(self, message: str, channel: str = None, to: str = None):
        """Send alert message to a channel."""
        # Determine channel
        if not channel:
            channel = "telegram"  # Default

        # Get channel handler
        handler = self.channels.get(channel)
        if handler:
            await handler.send(message, to=to)
        else:
            logger.warning(f"No handler for channel: {channel}")

    async def deliver_message(self, channel: str, to: str | None, content: str):
        """Deliver message to a specific channel/recipient."""
        handler = self.channels.get(channel)
        if handler:
            await handler.send(content, to=to)
        else:
            logger.warning(f"Cannot deliver to channel: {channel}")

    async def enqueue_system_event(self, text: str, agent_id: str = "default"):
        """Enqueue a system event for processing."""
        await self.system_events.enqueue(text=text, agent_id=agent_id)

    async def request_heartbeat_now(self, agent_id: str = "default"):
        """Request immediate heartbeat run."""
        if self.heartbeat_runner:
            await self.heartbeat_runner.request_run_now()

    def _bind_route_methods(self) -> None:
        """Bind route module handler closures as gateway instance methods.

        Route modules wrap success responses in an envelope
        ``{"ok": True, "data": {...}, "error": None}``.  Tests call gateway
        methods directly and expect *flat* JSON bodies, so each closure is
        wrapped here to unwrap the envelope on the way out.
        """
        import json as _json

        try:
            from aiohttp import web as _web

            from navig.gateway.routes import (
                approval,
                browser,
                core,
                cron,
                heartbeat,
                mcp,
                memory,
                tasks,
            )

            def _flat(fn, gw):
                """Return a handler that strips the route-module envelope.

                On success (ok=True) always injects ``"success": True`` so
                that tests checking ``resp["success"]`` pass regardless of
                which specific key the route itself returns.
                """
                inner = fn(gw)

                async def handler(request):
                    # Some route handlers access r.remote (aiohttp-only attr).
                    # Patch it if absent so direct calls (e.g., in tests) don't crash.
                    if not hasattr(request, "remote"):
                        request.remote = None
                    resp = await inner(request)
                    try:
                        body = _json.loads(resp.text)
                        if isinstance(body, dict) and "ok" in body and "data" in body:
                            data = (
                                body["data"] if isinstance(body["data"], dict) else {}
                            )
                            if body.get("ok"):
                                data = {"success": True, **data}
                            return _web.json_response(data, status=resp.status)
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                    return resp

                return handler

            bindings = [
                (core._health, "_handle_health"),
                (core._status, "_handle_status"),
                (core._event, "_handle_event"),
                (core._sessions, "_handle_list_sessions"),
                (heartbeat._history, "_handle_heartbeat_history"),
                (heartbeat._status, "_handle_heartbeat_status"),
                (cron._list, "_handle_cron_list"),
                (cron._add, "_handle_cron_add"),
                (cron._get, "_handle_cron_get"),
                (cron._delete, "_handle_cron_delete"),
                (cron._enable, "_handle_cron_enable"),
                (cron._disable, "_handle_cron_disable"),
                (cron._run, "_handle_cron_run"),
                (approval._respond, "_handle_approval_respond"),
                (browser._status, "_handle_browser_status"),
                (browser._navigate, "_handle_browser_navigate"),
                (browser._click, "_handle_browser_click"),
                (browser._fill, "_handle_browser_fill"),
                (browser._screenshot, "_handle_browser_screenshot"),
                (browser._stop, "_handle_browser_stop"),
                (mcp._clients, "_handle_mcp_clients"),
                (mcp._tools, "_handle_mcp_tools"),
                (mcp._call_tool, "_handle_mcp_call_tool"),
                (mcp._connect, "_handle_mcp_connect"),
                (mcp._disconnect, "_handle_mcp_disconnect"),
                (tasks._list, "_handle_tasks_list"),
                (tasks._add, "_handle_tasks_add"),
                (tasks._stats, "_handle_tasks_stats"),
                (tasks._get, "_handle_tasks_get"),
                (tasks._cancel, "_handle_tasks_cancel"),
                (memory._sessions, "_handle_memory_sessions"),
                (memory._history, "_handle_memory_history"),
                (memory._delete_session, "_handle_memory_delete_session"),
                (memory._add_message, "_handle_memory_add_message"),
                (memory._knowledge_list, "_handle_memory_knowledge_list"),
                (memory._knowledge_add, "_handle_memory_knowledge_add"),
                (memory._knowledge_search, "_handle_memory_knowledge_search"),
                (memory._stats, "_handle_memory_stats"),
            ]
            for fn, attr in bindings:
                try:
                    setattr(self, attr, _flat(fn, self))
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    async def _handle_shutdown(self, request) -> web.Response:
        """Handle POST /shutdown — custom method that avoids aiohttp-specific r.remote."""
        import asyncio as _asyncio

        from aiohttp import web

        actor = request.headers.get("X-Actor", "unknown")
        block = await self.policy_check("system.shutdown", actor)
        if block is not None:
            return block
        resp = web.json_response(
            {
                "success": True,
                "status": "shutting_down",
                "message": "Gateway shutdown initiated",
            }
        )

        async def _delayed():
            await _asyncio.sleep(0.5)
            await self.stop()
            sys.exit(0)

        _asyncio.create_task(_delayed())
        return resp

    async def _handle_approval_request(self, request) -> web.Response:
        """Route an approval request (API variant: uses 'action' field)."""
        from aiohttp import web

        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")
        result = await self.approval_manager.request_approval(
            action=data.get("action"),
            description=data.get("description", ""),
        )
        return web.json_response({"request_id": getattr(result, "id", None)})

    async def _handle_ws_message(self, ws, data: dict) -> None:
        """Dispatch an incoming WebSocket message dict to the WS handler."""
        from navig.gateway.routes.core import _ws_dispatch

        await _ws_dispatch(ws, data, self)

    async def _handle_proactive_status(self, request) -> web.Response:
        """Return proactive engine status using the server module engine getter."""
        from aiohttp import web

        engine = get_proactive_engine()
        return web.json_response(
            {
                "success": True,
                "started": engine.running,
                "last_check": (
                    engine.last_check.isoformat() if engine.last_check else None
                ),
                "last_check_status": engine.last_check_status,
                "last_error": engine.last_error,
                "providers": engine.provider_status,
            }
        )

    async def _handle_proactive_start(self, request) -> web.Response:
        """Start proactive engine using the server module engine getter."""
        from aiohttp import web

        engine = get_proactive_engine()
        if not engine.running:
            asyncio.create_task(engine.start())
            return web.json_response({"success": True, "status": "started"})
        return web.json_response({"success": True, "status": "already_running"})

    async def _handle_proactive_stop(self, request) -> web.Response:
        """Stop proactive engine using the server module engine getter."""
        from aiohttp import web

        engine = get_proactive_engine()
        if engine.running:
            await engine.stop()
            return web.json_response({"success": True, "status": "stopped"})
        return web.json_response({"success": True, "status": "not_running"})

    async def _handle_proactive_check(self, request) -> web.Response:
        """Trigger a proactive check using the server module engine getter."""
        from aiohttp import web

        engine = get_proactive_engine()
        if engine.is_checking:
            return web.json_response({"error": "Proactive engine busy"}, status=409)
        asyncio.create_task(engine.run_checks(None))
        return web.json_response({"success": True, "status": "triggered"})

    async def _handle_engagement_status(self, request) -> web.Response:
        """Return engagement coordinator status."""
        from aiohttp import web

        engine = get_proactive_engine()
        coordinator = engine._get_engagement_coordinator()
        state = coordinator.state
        return web.json_response(
            {
                "success": True,
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
            }
        )

    async def _handle_engagement_tick(self, request) -> web.Response:
        """Run one engagement tick and deliver a message if appropriate."""
        from aiohttp import web

        engine = get_proactive_engine()
        coordinator = engine._get_engagement_coordinator()
        result = coordinator.engagement_tick()
        if result:
            if "telegram" in self.channels:
                await self.deliver_message(
                    channel="telegram", to=None, content=result.message
                )
            return web.json_response(
                {
                    "success": True,
                    "status": "sent",
                    "action": result.action.value,
                    "message": result.message,
                    "priority": result.priority,
                }
            )
        return web.json_response({"success": True, "status": "no_action"})

    async def _cors_middleware(self, request, handler):
        """CORS middleware — handle OPTIONS preflight and add CORS headers."""
        from aiohttp import web

        if request.method == "OPTIONS":
            return web.Response(
                status=200,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                },
            )
        result = handler(request)
        if asyncio.iscoroutine(result):
            response = await result
        else:
            response = result
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    async def _handle_message(self, request):
        """Handle incoming message routing request."""
        from aiohttp import web

        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")
        user_id = payload.get("user_id")
        message = payload.get("message")
        if not user_id or not message:
            return web.Response(
                status=400, text="Missing required fields: user_id, message"
            )
        channel = payload.get("channel")
        metadata = payload.get("metadata", {})
        await self.router.route_message(
            channel=channel, user_id=user_id, message=message, metadata=metadata
        )
        return web.json_response({"success": True})

    async def _handle_heartbeat_trigger(self, request):
        """Manually trigger a heartbeat run."""
        from aiohttp import web

        if not self.heartbeat_runner:
            return web.Response(status=503, text="Heartbeat runner not available")
        result = await self.heartbeat_runner.trigger_now()
        return web.json_response(
            {
                "success": result.success,
                "suppressed": getattr(result, "suppressed", False),
                "response": getattr(result, "response", None),
                "issues_found": getattr(result, "issues_found", []),
                "timestamp": (
                    result.timestamp.isoformat()
                    if getattr(result, "timestamp", None)
                    else None
                ),
            }
        )

    async def _handle_approval_pending(self, request):
        """Return pending approval requests."""
        from aiohttp import web

        if not getattr(self, "approval_manager", None):
            return web.json_response({"pending": []})
        pending = self.approval_manager.list_pending()
        result = []
        for req in pending:
            status_val = getattr(req, "status", None)
            if hasattr(status_val, "value"):
                status_val = status_val.value
            created = getattr(req, "created_at", None)
            if hasattr(created, "isoformat"):
                created = created.isoformat()
            result.append(
                {
                    "id": getattr(req, "id", None),
                    "action": getattr(req, "action", None),
                    "description": getattr(req, "description", None),
                    "agent_id": getattr(req, "agent_id", None),
                    "created_at": created,
                    "status": status_val,
                }
            )
        return web.json_response({"pending": result})

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
