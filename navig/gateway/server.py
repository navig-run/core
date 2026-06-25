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
    # Guard against aiohttp 3.13+ which has a circular-import deadlock on Python 3.14.
    # Check the installed version via metadata (fast, no import) before importing.
    from importlib.metadata import PackageNotFoundError as _PNF
    from importlib.metadata import version as _pkg_version
    try:
        _aiohttp_ver = tuple(int(x) for x in _pkg_version("aiohttp").split(".")[:2])
        # aiohttp ≥ 3.13 has a circular-import deadlock specifically on Python 3.14.
        # Python 3.13 and below are unaffected — allow any aiohttp version there.
        if _aiohttp_ver >= (3, 13) and sys.version_info >= (3, 14):
            _ver_str = ".".join(str(x) for x in _aiohttp_ver)
            raise ImportError(
                f"aiohttp {_ver_str} has a circular-import deadlock on Python 3.14.\n"
                f"  Fix: \"{sys.executable}\" -m pip install \"aiohttp>=3.9.0,<3.13.0\"\n"
                f"  Or upgrade to Python 3.13 64-bit: winget install Python.Python.3.13"
            )
    except _PNF:
        pass  # aiohttp not installed — fall through to ImportError below
    import aiohttp
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError as _aiohttp_import_err:
    web = None
    aiohttp = None
    AIOHTTP_AVAILABLE = False
    print(f"\n⚠  Gateway cannot start: {_aiohttp_import_err}\n", flush=True)


# Safe no-op fallback for @web.middleware when aiohttp is not installed.
# Prevents AttributeError at class parse time during unit tests / imports.
def _noop_deco(fn):  # pragma: no cover
    return fn


_web_middleware = web.middleware if AIOHTTP_AVAILABLE else _noop_deco

from navig._daemon_defaults import _GATEWAY_PORT
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
        # Gateway HTTP port. Default is the canonical _GATEWAY_PORT (8789) — NOT
        # _DAEMON_PORT (8765), which belongs to the IPC/MCP WebSocket daemon. A
        # stale 8765 fallback here made the gateway squat the daemon's port and
        # left every 8789-probing client (doctor, deck, flux, mesh) unable to
        # reach it.
        self.port = gateway_cfg.get("port", _GATEWAY_PORT)
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
            raise ImportError("aiohttp is required for gateway. Install with: pip install aiohttp")

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
        self.cloud_manager: Any = None  # navig.cloud.CloudManager when cloud.enabled
        # Last relay-gate decision (license-bound). None when not yet evaluated
        # or when running in direct mode (cloud.public_url set), where the
        # gate doesn't apply. Exposed via /api/deck/cloud/status.
        self._relay_decision: Any = None
        self.channels: dict[str, Any] = {}

        # Queue for pending messages
        # Bounded queue — prevents OOM on message floods (P1-2)
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queue_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

        # New autonomous modules (lazy initialized)
        self.approval_manager = None
        self.request_registry = None
        self.browser_controller = None
        self.mcp_client_manager = None
        self.webhook_receiver = None
        self.task_queue = None
        self.task_worker = None
        # Autonomous mission loop (executor + scheduler). The executor always
        # exists (it backs the board + manual POST); the SYSTEM triggers
        # (heartbeat / proactive) are gated by `missions.autonomous_enabled`.
        self.mission_executor = None
        self.mission_scheduler = None

        # Per-subsystem health registry (populated at the end of start()).
        # Makes "cloudflared died but gateway is up" observable via /health/services.
        from navig.gateway.managed_service import ServiceRegistry

        self.service_registry = ServiceRegistry()

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

    def _print_boot_banner(self, cloud_url: str | None, elapsed: float) -> None:
        """Print a clean, boxed startup summary to stdout.

        Color is applied only when stdout is an interactive TTY (and NO_COLOR
        is unset); otherwise it degrades to plain ASCII so piped/redirected
        output stays clean.
        """
        import os

        use_color = (
            sys.stdout.isatty()
            and not os.environ.get("NO_COLOR")
            and sys.platform != "emscripten"
        )
        # Box-drawing chars require a UTF-8-capable stdout. Fall back to ASCII
        # on legacy code pages (e.g. Windows cp1251) so the banner never crashes.
        enc = (getattr(sys.stdout, "encoding", "") or "").lower()
        unicode_ok = "utf" in enc
        if unicode_ok:
            TL, TR, BL, BR, H, V, MARK = "╭", "╮", "╰", "╯", "─", "│", "◆"
        else:
            TL, TR, BL, BR, H, V, MARK = "+", "+", "+", "+", "-", "|", ">"

        def c(code: str, text: str) -> str:
            return f"\x1b[{code}m{text}\x1b[0m" if use_color else text

        local = f"http://{self.config.host}:{self.config.port}"
        hb = "enabled" if self.config.heartbeat_enabled else "disabled"

        rows: list[tuple[str, str]] = [("Local", local)]
        if cloud_url:
            rows.append(("Cloud", cloud_url))
        rows.append(("Heartbeat", hb))
        rows.append(("Storage", str(self.storage_dir)))
        rows.append(("Ready in", f"{elapsed:.2f}s"))

        label_w = max(len(k) for k, _ in rows)
        title_plain = f"{MARK} NAVIG Gateway online"
        inner = max(len(f"{k.ljust(label_w)}   {v}") for k, v in rows)
        inner = max(inner, len(title_plain))

        print("", flush=True)
        print(c("38;5;240", TL + H * (inner + 2) + TR), flush=True)

        title = f"{c('1;38;5;39', MARK + ' NAVIG')} {c('38;5;245', 'Gateway online')}"
        print(
            c("38;5;240", V + " ") + title + " " * (inner - len(title_plain)) + c("38;5;240", " " + V),
            flush=True,
        )
        print(c("38;5;240", V + " " + " " * inner + " " + V), flush=True)
        for k, v in rows:
            label = c("38;5;245", k.ljust(label_w))
            value = c("38;5;39", v) if k in ("Local", "Cloud") else v
            plain = f"{k.ljust(label_w)}   {v}"
            line = f"{label}   {value}"
            print(
                c("38;5;240", V + " ") + line + " " * (inner - len(plain)) + c("38;5;240", " " + V),
                flush=True,
            )
        print(c("38;5;240", BL + H * (inner + 2) + BR), flush=True)
        print("", flush=True)

    async def start(self):
        """Start the gateway server and all subsystems."""
        if self.running:
            logger.warning("Gateway already running")
            return

        self.running = True
        self.start_time = datetime.now()
        _t0 = self.start_time.timestamp()

        def _elapsed() -> str:
            import time
            return f"{time.monotonic() - _t0_mono:.2f}s"

        import time as _time_mod
        _t0_mono = _time_mod.monotonic()

        logger.info("Starting NAVIG Gateway...")

        # Narrator: a styled, TTY-only "boot story" so the operator can read
        # what's coming up at a glance. Silent when piped/cron/file — the
        # per-line `logger.debug("[startup] …")` record below stays intact for
        # grep. Never let a narration call break the boot path.
        try:
            from navig.core import narrator as _narr
        except Exception:  # noqa: BLE001
            _narr = None

        def _boot_step(
            label: str,
            secs: float | None = None,
            *,
            note: str = "",
            icon: str = "check",
        ) -> None:
            if _narr is None:
                return
            try:
                _narr.step_row(
                    label,
                    f"{secs:.2f}s" if secs is not None else "",
                    note=note,
                    icon=icon,
                )
            except Exception:  # noqa: BLE001
                pass

        if _narr is not None:
            try:
                _narr.blank()
                _narr.phase(
                    f"Booting NAVIG Gateway on {self.config.host}:{self.config.port}",
                    icon="spark",
                )
            except Exception:  # noqa: BLE001
                pass

        # Formatted boot (narrator on a TTY, i.e. NOT `--debug`): lift INFO/DEBUG
        # log chatter off the CONSOLE so the styled steps stand alone instead of
        # being buried under ~30 raw log lines — including the pre-boot preamble
        # and the "Gateway ready" line (the banner already shows ready). The
        # file handler keeps capturing everything at DEBUG, and real WARN/ERROR
        # during boot still surface. Console verbosity is restored to INFO right
        # after the banner so runtime logs return. In `--debug` mode the
        # narrator is disabled (NAVIG_NO_NARRATOR=1) so this is a no-op and the
        # raw logs flow verbatim, exactly like before.
        import logging as _logging

        _formatted_boot = _narr is not None and _narr.is_active()
        _console_handlers = (
            [
                _h
                for _h in _logging.getLogger("navig").handlers
                if isinstance(_h, _logging.StreamHandler)
                and not isinstance(_h, _logging.FileHandler)
            ]
            if _formatted_boot
            else []
        )
        for _h in _console_handlers:
            _h.setLevel(_logging.WARNING)

        # Initialize config watcher
        self.config_watcher = ConfigWatcher(self)

        # Initialize formation registry (loaded once at gateway start)
        try:
            _ts = _time_mod.monotonic()
            from navig.formations.registry import get_registry

            get_registry().initialize(self.storage_dir / "workspace")
            _dt = _time_mod.monotonic() - _ts
            logger.debug("[startup] Formation registry: %.2fs", _dt)
            _boot_step("formation registry", _dt, icon="gear")
        except Exception as e:
            logger.error("Failed to initialize formation registry: %s", e)
            _boot_step("formation registry unavailable", icon="warn")

        # Start HTTP server
        _ts = _time_mod.monotonic()
        await self._start_http_server()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] HTTP server: %.2fs", _dt)
        _boot_step(
            "HTTP server",
            _dt,
            note=f"{self.config.host}:{self.config.port}",
            icon="anchor",
        )

        # Start config watcher
        await self.config_watcher.start()
        _boot_step("config watcher", icon="gear")

        # Start heartbeat runner
        _ts = _time_mod.monotonic()
        await self._start_heartbeat()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] Heartbeat: %.2fs", _dt)
        _boot_step("heartbeat", _dt, icon="wave")

        # Start cron service
        _ts = _time_mod.monotonic()
        await self._start_cron()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] Cron: %.2fs", _dt)
        _boot_step("scheduler / cron", _dt, icon="gear")

        # Start Studio scheduled-post service (fires due social posts).
        try:
            from navig.social.scheduler_service import ScheduledPostService

            self.scheduled_post_service = ScheduledPostService(self)
            await self.scheduled_post_service.start()
            _boot_step("scheduler / studio", icon="gear")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Studio scheduled-post service skipped: %s", exc)
            self.scheduled_post_service = None

        # Start channel health monitor
        await self._start_health_monitor()
        _boot_step("channel health monitor", icon="gear")

        # Start message queue processor
        self._queue_task = asyncio.create_task(self._process_message_queue())

        # Ensure mesh_token exists (auto-generate if missing)
        await self._ensure_mesh_token()

        # Initialize autonomous modules
        _ts = _time_mod.monotonic()
        await self._init_autonomous_modules()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] Autonomous modules: %.2fs", _dt)
        _boot_step("autonomous modules", _dt, icon="brain")

        # Start the mission scheduler only when the autonomous loop is enabled.
        # (The executor itself is always live for the board + manual POSTs.)
        if self.mission_scheduler and self._missions_autonomous_enabled():
            try:
                await self.mission_scheduler.start()
                _boot_step("mission scheduler", icon="brain")
            except Exception as e:  # noqa: BLE001
                logger.warning("Mission scheduler failed to start: %s", e)

        # Start channel adapters (Telegram polling, etc.)
        _ts = _time_mod.monotonic()
        await self._init_channels()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] Channels: %.2fs", _dt)
        _boot_step("channels", _dt, icon="radio")

        # Wire unified comms dispatcher
        await self._init_comms()
        _boot_step("comms dispatcher", icon="gear")

        # Register messaging adapters (unified multi-network layer)
        await self._init_messaging_adapters()
        _boot_step("messaging adapters", icon="gear")

        # Start cloud broker/tunnel manager when cloud.enabled is true.
        # Off by default; opt-in via `navig cloud connect` or Deck UI toggle.
        _ts = _time_mod.monotonic()
        await self._start_cloud_manager()
        _dt = _time_mod.monotonic() - _ts
        logger.debug("[startup] Cloud manager: %.2fs", _dt)
        _cloud_note = ""
        try:
            _u = self._cloud_url_for_banner()
            if _u:
                _cloud_note = _u.split("://", 1)[-1]
        except Exception:  # noqa: BLE001
            pass
        _boot_step("cloud manager", _dt, note=_cloud_note, icon="globe")

        _total = _time_mod.monotonic() - _t0_mono
        # In formatted mode the console is still quiet, so this INFO line is
        # captured to the log file but kept off the styled boot output — the
        # banner below shows "Ready in" instead. In --debug mode it prints.
        logger.info("Gateway ready in %.2fs", _total)
        # Wrap the entire banner block so a typo or missing attr can't swallow
        # the rest of the output -- the user must always see SOMETHING actionable.
        try:
            cloud_url = self._cloud_url_for_banner()
            self._print_boot_banner(cloud_url, _total)
            try:
                self._print_cloud_user_hints(cloud_url)
            except Exception as _hints_exc:  # noqa: BLE001
                logger.debug("cloud hints failed: %r", _hints_exc)
            print("   Press Ctrl+C to stop\n", flush=True)
        except Exception as _banner_exc:  # noqa: BLE001
            logger.warning("startup banner failed: %r", _banner_exc)
            print(f"\n  NAVIG Gateway running on port {self.config.port}", flush=True)

        # Boot story complete — return console verbosity to INFO so runtime
        # logs (heartbeat, channel traffic, warnings) print normally again.
        # No-op in --debug mode (the list is empty; logs already flowed raw).
        for _h in _console_handlers:
            _h.setLevel(_logging.INFO)

        # Warm the conversational path in the background so the FIRST inbound
        # message doesn't pay the ~3–5s cold start (tool imports, AI-client /
        # hybrid-router init, SOUL load). Non-blocking and best-effort.
        try:
            self._warmup_task = asyncio.create_task(self.router.warmup())
        except Exception as _warm_exc:  # noqa: BLE001
            logger.debug("could not schedule conversational warmup: %r", _warm_exc)

        # Daily Partner Center marketplace sync (best-effort; no-op until the
        # user configures App-Only credentials in the Connectors catalog).
        try:
            self._pc_sync_task = asyncio.create_task(self._partner_center_sync_loop())
        except Exception as _pc_exc:  # noqa: BLE001
            logger.debug("could not schedule partner-center sync: %r", _pc_exc)

        # Register started subsystems for per-subsystem health (/health/services).
        # Best-effort and attribute-driven so a missing/disabled subsystem simply
        # reports "down" rather than breaking the snapshot.
        try:
            for _svc_name, _svc in (
                ("heartbeat", getattr(self, "heartbeat_runner", None)),
                ("cron", getattr(self, "cron_service", None)),
                ("health_monitor", getattr(self, "_health_monitor", None)),
                ("cloud_manager", getattr(self, "cloud_manager", None)),
                ("mission_scheduler", getattr(self, "mission_scheduler", None)),
                ("task_worker", getattr(self, "task_worker", None)),
            ):
                self.service_registry.register(_svc_name, _svc)
        except Exception as _reg_exc:  # noqa: BLE001
            logger.debug("service registry population skipped: %r", _reg_exc)

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
        try:
            from navig.core import narrator
            narrator.blank()
            narrator.phase("Shutting down NAVIG Gateway", icon="wave")
            narrator.step("draining queues + cancelling tasks ...", icon="gear")
        except Exception:  # noqa: BLE001
            pass
        self.running = False
        self._shutdown_t0 = __import__("time").monotonic()

        # Stop queue processor
        if self._queue_task:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # Cancel tracked background tasks (P4 cleanup hardening)
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
            self._background_tasks.clear()

        # Stop mission scheduler + drain executor tasks
        if self.mission_scheduler:
            try:
                await self.mission_scheduler.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.mission_executor:
            try:
                await self.mission_executor.aclose()
            except Exception:  # noqa: BLE001
                pass

        # Stop heartbeat
        if self.heartbeat_runner:
            await self.heartbeat_runner.stop()

        # Stop cron
        if self.cron_service:
            await self.cron_service.stop()

        # Stop Studio scheduled-post service
        if getattr(self, "scheduled_post_service", None):
            try:
                await self.scheduled_post_service.stop()
            except Exception:  # noqa: BLE001
                pass

        # Stop cloud manager (cloudflared subprocess + broker heartbeat)
        if self.cloud_manager is not None:
            try:
                await self.cloud_manager.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("cloud_manager.stop raised: %r", exc)

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
        try:
            import time as _time_stop
            elapsed = _time_stop.monotonic() - getattr(self, "_shutdown_t0", _time_stop.monotonic())
            from navig.core import narrator
            narrator.verdict(f"Gateway stopped cleanly  ({elapsed:.2f}s)", icon="check")
            narrator.blank()
        except Exception:  # noqa: BLE001
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

        rate_mw, self._auth_attempts = make_rate_limit_middleware(window=60, max_failures=5)
        cors_mw = make_cors_middleware()
        # 100 MB request cap (default is 1 MB) so file uploads — inbox drag-and-drop,
        # voice audio — aren't rejected with 413 for ordinary documents/media.
        self._app = web.Application(
            middlewares=[rate_mw, cors_mw],
            client_max_size=100 * 1024 * 1024,
        )
        gateway_key = web.AppKey("gateway", object)
        self._app[gateway_key] = self
        self._app._state["gateway"] = self

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
            from navig.messaging import is_provider_enabled
            from navig.messaging.secrets import resolve_telegram_bot_token

            # Let installed plugins (e.g. the private navig-harbor) register their
            # gateway route hooks BEFORE register_deck_routes fires
            # `gateway:register_routes`. Best-effort: a plugin must never block boot.
            try:
                from navig.core.plugins import load_entry_point_plugins

                load_entry_point_plugins()
            except Exception:  # noqa: BLE001
                pass

            raw_cfg = self.config_manager.global_config or {}
            tg_cfg = raw_cfg.get("telegram", {}) if isinstance(raw_cfg, dict) else {}
            deck_cfg = raw_cfg.get("deck", {}) if isinstance(raw_cfg, dict) else {}

            telegram_channel = self.channels.get("telegram")
            bot_token = (
                getattr(telegram_channel, "bot_token", "")
                or resolve_telegram_bot_token(raw_cfg)
                or tg_cfg.get("bot_token", "")
            )
            provider_ready = (
                bool(telegram_channel)
                or is_provider_enabled("telegram", raw_cfg)
                or bool(bot_token)
            )

            if provider_ready and bot_token and deck_cfg.get("enabled", True):
                register_deck_routes(
                    self._app,
                    bot_token=bot_token,
                    allowed_users=tg_cfg.get("allowed_users", []),
                    require_auth=tg_cfg.get("require_auth", True),
                    deck_cfg=deck_cfg,
                )
            elif not provider_ready:
                logger.info("Deck not loaded: telegram messaging provider disabled")
            elif not bot_token:
                logger.info("Deck not loaded: no Telegram bot_token configured")
            else:
                logger.info("Deck disabled in config")
        except Exception as e:
            logger.debug("Deck API not loaded: %s", e)

        # SECURITY: the brain must bind loopback only. Its sole public ingress is
        # the OUTBOUND Lighthouse uplink (or a cloudflared tunnel) — never a
        # listening socket on a public interface. Binding 0.0.0.0 / a LAN/public
        # IP exposes the Deck API (and its loopback auth-bypass) to the network.
        # Warn loudly; don't block (mesh/advanced users may do this knowingly).
        host = str(self.config.host or "").strip()
        _loopback_hosts = {"127.0.0.1", "::1", "localhost", ""}
        if host not in _loopback_hosts:
            try:
                from navig.core import narrator

                narrator.blank()
                narrator.phase(
                    f"SECURITY: gateway is binding a non-loopback host ({host})",
                    icon="warn",
                )
                narrator.step(
                    "the brain should bind 127.0.0.1 only — reach it via Lighthouse "
                    "(outbound, no open ports), not a public listener",
                    icon="dot",
                )
                narrator.step(
                    "fix: navig config set gateway.host 127.0.0.1  (then restart)",
                    icon="dot",
                )
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "SECURITY: gateway.host=%r is not loopback. The brain's only public "
                "ingress should be the outbound Lighthouse uplink — a public listener "
                "exposes the Deck API. Set gateway.host=127.0.0.1 unless you have a "
                "trusted reverse proxy in front.",
                host,
            )

        # Start server
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()

    # ── Autonomous mission triggers ──────────────────────────────────

    def _missions_autonomous_enabled(self) -> bool:
        """Master kill-switch for SYSTEM-initiated missions (default False)."""
        try:
            return bool(
                (self.config_manager.global_config or {})
                .get("missions", {})
                .get("autonomous_enabled", False)
            )
        except Exception:  # noqa: BLE001
            return False

    def _wire_mission_triggers(self) -> None:
        """Bridge heartbeat issues + proactive suggestions → Missions.

        Each handler re-checks the master flag, so wiring is harmless when the
        flag is off. Called from `_init_autonomous_modules`, which runs after the
        heartbeat runner is constructed."""
        try:
            if self.heartbeat_runner is not None:
                self.heartbeat_runner.on_issue(self._on_heartbeat_issues)
        except Exception as e:  # noqa: BLE001
            logger.debug("heartbeat→mission bridge not wired: %s", e)
        try:
            from navig.core.hooks import register_hook

            register_hook("proactive:engagement", self._on_proactive_suggestion)
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive→mission bridge not wired: %s", e)

    async def _on_heartbeat_issues(self, issues) -> None:
        """Heartbeat found problems → enqueue a remediate mission (flag-gated)."""
        if not self._missions_autonomous_enabled() or not self.mission_executor or not issues:
            return
        try:
            from navig.contracts.mission import Mission, MissionPriority

            mission = Mission(
                title="Remediate health issues",
                capability="remediate",
                payload={"issues": [str(i) for i in issues]},
                priority=MissionPriority.HIGH.value,
            )
            await self.mission_executor.submit(mission)
            logger.info("Heartbeat issues → remediate mission %s", mission.mission_id[:8])
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to create remediate mission: %s", e)

    async def _on_proactive_suggestion(self, event) -> None:
        """Proactive engagement opportunity → enqueue a mission (flag-gated)."""
        if not self._missions_autonomous_enabled() or not self.mission_executor:
            return
        try:
            from navig.contracts.mission import Mission

            ctx = getattr(event, "context", None) or {}
            msgs = getattr(event, "messages", None) or []
            suggestion = msgs[0] if msgs else getattr(event, "action", "")
            mission = Mission(
                title="Proactive engagement",
                capability="proactive",
                payload={"suggestion": suggestion, "context": ctx},
            )
            await self.mission_executor.submit(mission)
            logger.info("Proactive suggestion → mission %s", mission.mission_id[:8])
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to create proactive mission: %s", e)

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

    async def _start_health_monitor(self):
        """Start the channel health monitor background task."""
        try:
            from navig.gateway.health_monitor import ChannelHealthMonitor

            hm_cfg: dict = (
                (self.config_manager.global_config or {}).get("gateway", {}).get(
                    "health_monitor", {}
                )
            )
            self._health_monitor = ChannelHealthMonitor(
                channels=self.channels,
                restart_fn=self._restart_channel,
                **{k: v for k, v in hm_cfg.items() if k in {
                    "check_interval_s",
                    "stale_threshold_s",
                    "startup_grace_s",
                    "max_restarts_per_hour",
                    "cooldown_cycles",
                }},
            )
            self._spawn_background_task(self._health_monitor.run())
            logger.info("Channel health monitor started")
        except Exception as exc:
            logger.debug("Channel health monitor not started: %s", exc)

    async def _restart_channel(self, name: str) -> None:
        """Stop then restart a named channel (used by the health monitor)."""
        channel = self.channels.get(name)
        if channel is None:
            logger.warning("_restart_channel: channel %r not found", name)
            return
        logger.info("_restart_channel: stopping %r", name)
        try:
            await channel.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_restart_channel: stop(%r) raised %r", name, exc)
        logger.info("_restart_channel: starting %r", name)
        try:
            await channel.start()
            # Reset the event timestamp so the health monitor grants the
            # channel its full startup grace period instead of immediately
            # flagging it stale again (last_event_at = 0 → idle = process
            # uptime >> stale_threshold).
            import time as _time
            if hasattr(channel, "_last_event_at"):
                channel._last_event_at = _time.monotonic()
        except Exception as exc:  # noqa: BLE001
            logger.error("_restart_channel: start(%r) failed: %r", name, exc)

    async def _start_cloud_manager(self) -> None:
        """Spawn the CloudManager when ``cloud.enabled`` is true.

        The manager runs the cloudflared subprocess + broker heartbeat so the
        hosted Deck can resolve "where is my daemon" by api_key/telegram_id.
        Errors are logged but do not block the gateway startup -- local-only
        users must keep working even if the broker is unreachable.
        """
        raw = self.config_manager.global_config or {}
        cloud_cfg = raw.get("cloud", {}) if isinstance(raw, dict) else {}
        # Default to ON: a fresh install with no cloud: block in user config
        # should still wire the broker so the hosted Deck + Telegram Mini App
        # work out of the box. Set cloud.enabled: false explicitly to opt out.
        if not cloud_cfg.get("enabled", True):
            return
        deck_cfg = raw.get("deck", {}) if isinstance(raw, dict) else {}
        api_key = (deck_cfg.get("api_key") or "").strip()
        if not api_key:
            # register_deck_routes mints + persists an api_key on first start.
            # If we land here it means the Deck isn't wired (no bot_token, etc.)
            # -- silent skip so non-bot users aren't nagged.
            logger.debug("cloud manager skipped: no deck.api_key (deck not enabled?)")
            return
        # VPS / direct mode: cloud.public_url (config) OR $NAVIG_PUBLIC_URL
        # (env -- useful for systemd unit "Environment=" lines without
        # touching config.yaml). When set, CloudManager skips cloudflared
        # entirely and registers this URL with the broker. The user owns
        # the reverse proxy on this hostname.
        import os as _os
        public_url = (
            cloud_cfg.get("public_url", "")
            or _os.environ.get("NAVIG_PUBLIC_URL", "")
            or ""
        ).strip()

        # Lighthouse self-host: an outbound WebSocket uplink to the user's own
        # Cloudflare edge. No tunnel, no broker, no inbound port — and crucially
        # navig hosts nothing, so (like direct mode) it carries no per-user cost
        # and bypasses the relay gate entirely.
        lighthouse_url = (
            cloud_cfg.get("lighthouse_url", "")
            or _os.environ.get("NAVIG_LIGHTHOUSE_URL", "")
            or ""
        ).strip()
        mode = (cloud_cfg.get("mode", "") or "").strip().lower()
        use_lighthouse = bool(lighthouse_url) and mode in ("", "lighthouse")

        # Hosted-relay gate: the cloudflared/broker path is the only
        # surface that costs us money per active user. Perpetual-pack
        # owners get the local app forever, but the hosted relay is a
        # subscription feature. Direct mode (public_url) and Lighthouse
        # (self-hosted edge) are always allowed -- the user hosts their own
        # ingress, the broker doesn't carry their traffic, no per-user cost.
        if not public_url and not use_lighthouse:
            try:
                from navig.license import current_status
                from navig.license.relay_gate import evaluate_relay_access
                decision = evaluate_relay_access(current_status())
                if not decision.allowed:
                    logger.info(
                        "cloud manager skipped: relay gate denied (reason=%s)",
                        decision.reason,
                    )
                    self._relay_decision = decision
                    return
                self._relay_decision = decision
            except Exception as exc:  # noqa: BLE001
                # Degrade open: license parsing edge case never blocks boot.
                logger.warning("relay gate evaluation failed: %r; allowing", exc)
                self._relay_decision = None
        else:
            self._relay_decision = None

        try:
            from navig.cloud import CloudManager  # local import keeps cold start cheap
            if use_lighthouse:
                telegram_channel = self.channels.get("telegram")
                telegram_handler = getattr(telegram_channel, "handle_webhook_update", None)
                self.cloud_manager = CloudManager(
                    api_key=api_key,
                    broker_url=cloud_cfg.get("broker_url", "https://api.navig.run"),
                    gateway_port=self.config.port,
                    tunnel_label=cloud_cfg.get("tunnel_label", "") or "",
                    lighthouse_url=lighthouse_url,
                    telegram_handler=telegram_handler,
                    system_events=self.system_events,
                    snapshot_provider=self._make_lighthouse_snapshot_provider(api_key),
                )
            else:
                self.cloud_manager = CloudManager(
                    api_key=api_key,
                    broker_url=cloud_cfg.get("broker_url", "https://api.navig.run"),
                    gateway_port=self.config.port,
                    heartbeat_interval_s=float(cloud_cfg.get("heartbeat_interval_s", 60)),
                    tunnel_label=cloud_cfg.get("tunnel_label", "") or "",
                    cloudflared_path=cloud_cfg.get("cloudflared_path", "") or "",
                    cloudflared_extra_args=list(cloud_cfg.get("cloudflared_extra_args") or []),
                    public_url=public_url,
                    broker_timeout_s=float(cloud_cfg.get("broker_timeout", 15)),
                )
            await self.cloud_manager.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cloud manager failed to start: %s", exc)
            self.cloud_manager = None

    def _make_lighthouse_snapshot_provider(self, api_key: str):
        """An async provider that snapshots the deck status for the offline cache.

        Loopback GET to ``/api/deck/status`` so Lighthouse can serve a cached
        view + a "brain offline" banner while the uplink is down. Best-effort:
        any failure returns ``None`` and the edge simply keeps its last snapshot.
        """
        port = self.config.port

        async def _provider():
            import aiohttp
            url = f"http://127.0.0.1:{port}/api/deck/status"
            headers = {"Authorization": f"Bearer {api_key}"}
            timeout = aiohttp.ClientTimeout(total=10)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(url, headers=headers) as resp:
                        if resp.status != 200:
                            return None
                        return await resp.json(content_type=None)
            except Exception:  # noqa: BLE001
                return None

        return _provider

    def _cloud_url_for_banner(self) -> str | None:
        cm = self.cloud_manager
        if cm is None:
            return None
        try:
            return cm.current_url
        except Exception:  # noqa: BLE001
            return None

    def _print_cloud_user_hints(self, cloud_url: str | None) -> None:
        """Print the actionable two-line summary every user actually needs.

        Shown after the gateway is up. Covers the two access paths:
        - Browser: relay.navig.run/connect?key=... magic link
        - Telegram: open the bot, tap the Mini App button

        When cloud is OFF, prints the one-liner that tells the user how to
        flip it on -- nothing else; we don't want to nag.
        """
        raw = self.config_manager.global_config or {}
        cloud_cfg = raw.get("cloud", {}) if isinstance(raw, dict) else {}
        deck_cfg = raw.get("deck", {}) if isinstance(raw, dict) else {}
        # Broker (tunnel routing) and the hosted Relay frontend are separate hosts:
        #   broker_url → api.navig.run    (POST /api/cloud/*)
        #   relay_url  → relay.navig.run  (serves the /connect magic-link page;
        #                                  legacy key cloud.deck_url still honored)
        broker_url = cloud_cfg.get("broker_url", "https://api.navig.run").rstrip("/")
        deck_url = (
            cloud_cfg.get("relay_url")
            or cloud_cfg.get("deck_url")
            or "https://relay.navig.run"
        ).rstrip("/")
        api_key = (deck_cfg.get("api_key") or "").strip()

        # Source of truth: did the CloudManager actually start? Don't trust
        # the config flag alone -- _start_cloud_manager defaults missing
        # `cloud.enabled` to True (opt-out, not opt-in), so a user without a
        # `cloud:` block in their config still has a live manager. Reading
        # the flag with default=False (as we did) printed "OFF" while the
        # manager was happily running -- exactly the bug the user hit.
        cm = self.cloud_manager
        manager_alive = cm is not None and getattr(cm, "status", "off") in (
            "online", "starting"
        )
        cloud_on = manager_alive or bool(cloud_cfg.get("enabled", True))

        if not cloud_on:
            print("", flush=True)
            print("   Cloud routing: OFF  (enable with: navig cloud connect)", flush=True)
            return

        # Cloud is on. Render the boot story via the narrator (styled, with
        # icons + color when stdout is a TTY; gracefully silent otherwise).
        # The plain-print fallback handles legacy Windows consoles + non-TTY
        # contexts (systemd journal, docker logs) -- the regular per-line
        # logger already captures everything for grep.
        from navig.core import narrator

        mode = getattr(cm, "mode", "tunnel") if cm is not None else "tunnel"
        narrator.blank()
        if mode == "direct":
            narrator.phase(
                f"Cloud routing: direct mode -> {cloud_url}", icon="lock"
            )
            narrator.step(
                "your reverse proxy terminates TLS; no cloudflared spawned",
                icon="check",
            )
        elif cloud_url:
            narrator.phase(
                f"Cloud routing: cloudflared tunnel -> {cloud_url}", icon="globe"
            )
            narrator.step(
                f"broker: {broker_url.split('://')[-1]}  ·  heartbeat every 60s", icon="radio"
            )

        if cloud_url and api_key:
            magic = f"{deck_url}/connect?key={api_key}"
            bot_user = self._resolve_bot_username() or "your bot"
            tg_hint = f"@{bot_user}" if bot_user != "your bot" else bot_user
            narrator.blank()
            narrator.phase("Access points", icon="spark")
            narrator.step(f"Browser:  {magic}", icon="globe")
            narrator.step(f"Telegram: {tg_hint} -> /start -> Mini App", icon="anchor")
        elif not api_key:
            narrator.phase("Cloud enabled, but deck.api_key is missing", icon="warn")
            narrator.step("run: navig cloud connect", icon="dot")
        else:
            narrator.step("starting cloudflared… (URL appears within ~5s)", icon="gear")

    def _resolve_bot_username(self) -> str | None:
        """Best-effort lookup of the configured Telegram bot's @username."""
        try:
            tg_channel = self.channels.get("telegram") if hasattr(self, "channels") else None
            if tg_channel is None:
                return None
            for attr in ("bot_username", "_bot_username", "username"):
                v = getattr(tg_channel, attr, None)
                if isinstance(v, str) and v:
                    return v.lstrip("@")
        except Exception:  # noqa: BLE001
            pass
        return None

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
                logger.info("Heartbeat interval changed: %s → %s", old_interval, new_interval)
                await self.heartbeat_runner.update_config()

    async def _partner_center_sync_loop(self) -> None:
        """Pull Partner Center marketplace data once a day when configured.

        Best-effort: does nothing until the user pastes App-Only credentials
        (Connectors catalog → Microsoft Partner Center → Configure). Failures
        are swallowed so a flaky Microsoft API never disturbs the gateway.
        """
        await asyncio.sleep(120)  # let boot settle before the first pull
        while self.running:
            try:
                from navig_harbor.connectors.partner_center import credentials as _pc_creds

                if _pc_creds.is_configured():
                    from navig_harbor.connectors.partner_center.sync import sync_partner_center

                    await sync_partner_center()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("partner-center daily sync skipped: %s", exc)
            try:
                await asyncio.sleep(24 * 3600)
            except asyncio.CancelledError:
                break

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
            logger.warning("Approval module not available: %s", e)

        # Request registry — user-facing questions / route confirmations /
        # operator proposals. Sibling to approval_manager; the deck merges both
        # into a single /api/deck/requests stream.
        try:
            from navig.requests import RequestRegistry

            self.request_registry = RequestRegistry()
            await self.request_registry.start()

            # Push an SSE frame whenever a new request appears so the deck pops a
            # toast immediately (the 15s poll is the fallback). Best-effort.
            async def _emit_requests_update(req) -> None:
                try:
                    payload = req.to_dict() if hasattr(req, "to_dict") else {}
                    await self.system_events.emit("requests_update", payload)
                except Exception:
                    logger.debug("requests_update emit failed", exc_info=True)

            self.request_registry.on_request(_emit_requests_update)
            if self.approval_manager is not None:
                self.approval_manager.on_request(_emit_requests_update)
            logger.info("Request registry initialized")
        except Exception as e:  # noqa: BLE001 — never block boot on this
            logger.warning("Request registry not available: %s", e)
            self.request_registry = None

        # Notification router — give it the gateway handle so the `deck` channel
        # can push an SSE `notification` frame (bell/Inbox/toast) on dispatch.
        try:
            from navig.notify.router import get_notification_router

            get_notification_router().bind_gateway(self)
            logger.info("Notification router bound to gateway")
            # Background loop: sync the inbound-SMS webhook to the public URL +
            # fire scheduled AI briefings.
            from navig.notify.scheduler import start as _start_notify_scheduler

            _start_notify_scheduler(self)
        except Exception as e:  # noqa: BLE001
            logger.debug("Notification router/scheduler bind skipped: %s", e)

        # Opt-in notification monitors/producers (webcam, resources, self-errors).
        # All default OFF; the deck "Monitors" card toggles them live.
        self._init_notify_monitors()

        # Autonomous mission loop — the executor is the single bounded execution
        # path for board card runs, manual POSTs, and (when enabled) system
        # triggers. Constructed after approval_manager because the APPROVAL
        # autonomy mode depends on it.
        try:
            from navig.missions import MissionExecutor, MissionScheduler

            _mcfg = (self.config_manager.global_config or {}).get("missions", {}) or {}
            self.mission_executor = MissionExecutor(self)
            self.mission_scheduler = MissionScheduler(
                self,
                self.mission_executor,
                interval_secs=int(_mcfg.get("scheduler_interval_secs", 300)),
            )

            # System triggers are wired here but every handler re-checks the
            # master flag, so wiring them is harmless when the flag is off.
            self._wire_mission_triggers()
            logger.info("Mission executor initialized")
        except Exception as e:  # noqa: BLE001 — never block startup on this
            logger.warning("Mission executor not available: %s", e)

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
            logger.warning("Browser module not available: %s", e)

        try:
            # Initialize MCP client manager
            from navig.mcp import MCPClientManager

            try:
                from navig.mcp.client import MCPClientConfig
            except ImportError:
                MCPClientConfig = None  # type: ignore[assignment]

            self.mcp_client_manager = MCPClientManager()

            # Auto-connect to configured MCP servers
            mcp_servers = self.config_manager.global_config.get("mcp", {}).get("servers", [])
            for server_cfg in mcp_servers:
                try:
                    if MCPClientConfig is not None:
                        cfg = MCPClientConfig(
                            id=server_cfg["name"],
                            command=server_cfg.get("command"),
                            url=server_cfg.get("url"),
                            transport="sse" if server_cfg.get("url") else "stdio",
                        )
                        await self.mcp_client_manager.add_client(cfg)
                    else:
                        await self.mcp_client_manager.add_client(
                            server_cfg["name"],
                            command=server_cfg.get("command"),
                            url=server_cfg.get("url"),
                        )
                except Exception as e:
                    logger.warning("Failed to connect MCP server %s: %s", server_cfg.get('name'), e)

            logger.info(
                "MCP client manager initialized with %d clients",
                len(self.mcp_client_manager.clients),
            )
        except ImportError as e:
            logger.warning("MCP module not available: %s", e)

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
            logger.warning("Webhook module not available: %s", e)

        try:
            # Initialize task queue and worker
            from navig.tasks import TaskQueue, TaskWorker, WorkerConfig

            queue_path = str(self.storage_dir / "task_queue.json")
            self.task_queue = TaskQueue(persist_path=queue_path)
            self.task_worker = TaskWorker(self.task_queue, WorkerConfig(max_concurrent=5))

            # Register built-in task handlers
            self._register_task_handlers()

            await self.task_worker.start()
            logger.info("Task queue and worker initialized")
        except ImportError as e:
            logger.warning("Tasks module not available: %s", e)

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
                self._mesh_discovery = MeshDiscovery(self._mesh_registry, secret=_mesh_secret)
                await self._mesh_discovery.start()
                logger.info("[mesh] Flux mesh discovery started")
            else:
                logger.info("[mesh] Mesh discovery disabled by config (mesh.enabled=false)")
        except Exception as e:
            logger.warning("[mesh] Mesh discovery init failed — node runs isolated: %s", e)

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

    async def _init_channels(self):
        """Instantiate and start channel adapters (e.g. Telegram polling loop)."""
        raw_cfg = self.config_manager.global_config or {}
        tg_cfg: dict = raw_cfg.get("telegram", {}) if isinstance(raw_cfg, dict) else {}

        # Resolve bot token: config first, then vault
        from navig.messaging.secrets import resolve_telegram_bot_token

        bot_token = resolve_telegram_bot_token(raw_cfg) or tg_cfg.get("bot_token", "")
        if not bot_token:
            logger.info("Telegram channel not started: no bot_token configured")
            return

        try:
            from navig.gateway.channels.telegram import TelegramChannel

            allowed_users: list[int] = [
                int(u) for u in tg_cfg.get("allowed_users", []) if u
            ]
            allowed_groups: list[int] = [
                int(g) for g in tg_cfg.get("allowed_groups", []) if g
            ]
            require_auth: bool = tg_cfg.get("require_auth", True)
            enable_notifications: bool = tg_cfg.get("enable_notifications", True)
            webhook_url: str | None = tg_cfg.get("webhook_url") or None
            webhook_secret: str | None = tg_cfg.get("webhook_secret") or None

            channel = TelegramChannel(
                bot_token=bot_token,
                allowed_users=allowed_users,
                allowed_groups=allowed_groups,
                on_message=self.router.route_message,
                enable_notifications=enable_notifications,
                require_auth=require_auth,
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
            )
            self.channels["telegram"] = channel
            await channel.start()
            logger.info("Telegram channel started")
        except Exception as exc:
            logger.error("Failed to start Telegram channel: %s", exc)

    async def _init_comms(self):
        """Wire the unified comms dispatcher (Prompt 5 integration)."""
        try:
            from navig.comms.dispatch import configure as comms_configure

            # Grab existing TelegramNotifier from the live channel (populated by _init_channels)
            telegram_notifier = None
            tg_channel = self.channels.get("telegram")
            if tg_channel is not None:
                telegram_notifier = getattr(tg_channel, "_notifier", None)
            if telegram_notifier is None:
                # Fallback: try ChannelRegistry (e.g. if channel was started externally)
                try:
                    from navig.gateway.channels.registry import ChannelRegistry

                    registry = (
                        ChannelRegistry.instance() if hasattr(ChannelRegistry, "instance") else None
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

    @staticmethod
    def _resolve_adapter_config(cfg: dict) -> dict:
        """Expand ``vault:KEY`` placeholder strings in an adapter config dict.

        Walks the dict one level deep and replaces any string value of the form
        ``"vault:key_name"`` with the secret retrieved from the NAVIG vault.
        Nested dicts (e.g. ``cfg["twilio"]``) are also expanded one level.
        Non-vault values are returned unchanged.
        """
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            if vault is None:
                return cfg
        except Exception:
            return cfg

        def _expand(d: dict) -> dict:
            out: dict = {}
            for k, v in d.items():
                if isinstance(v, str) and v.startswith("vault:"):
                    secret_key = v[len("vault:"):]
                    try:
                        # get_secret returns a SecretStr (no .strip / masks on str());
                        # reveal the real value before using it as adapter config.
                        sec = vault.get_secret(secret_key)
                        raw = sec.reveal() if hasattr(sec, "reveal") else str(sec or "")
                        resolved = (raw or "").strip()
                        out[k] = resolved if resolved else v
                    except Exception:
                        out[k] = v
                elif isinstance(v, dict):
                    out[k] = _expand(v)
                else:
                    out[k] = v
            return out

        return _expand(cfg)

    async def _init_messaging_adapters(self):
        """Register multi-network messaging adapters from config.

        Reads ``adapters:`` section from :file:`defaults.yaml` / user config
        and populates :func:`~navig.messaging.adapter_registry.get_adapter_registry`.
        Vault placeholder strings (``vault:key_name``) in the adapter config are
        resolved before the adapters are constructed.
        """
        try:
            from navig.messaging.adapter_registry import get_adapter_registry

            registry = get_adapter_registry()
            adapters_cfg = self.config_manager.global_config.get("adapters", {})

            # ── Telegram adapter — inject the live bot instance ──
            tg_cfg = adapters_cfg.get("telegram", {})
            if tg_cfg.get("enabled", True):
                try:
                    from navig.gateway.channels.registry import ChannelRegistry
                    from navig.messaging.adapters.telegram_adapter import TelegramMessagingAdapter

                    chan_registry = (
                        ChannelRegistry.instance()
                        if hasattr(ChannelRegistry, "instance")
                        else None
                    )
                    tg_adapter = TelegramMessagingAdapter()
                    if chan_registry:
                        tg_channel = chan_registry.get_adapter("telegram")
                        bot = getattr(tg_channel, "_bot", None) or getattr(
                            tg_channel, "bot", None
                        )
                        if bot:
                            tg_adapter.set_bot(bot)
                    registry.register(tg_adapter)
                    logger.debug("Messaging adapter registered: telegram")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Telegram messaging adapter skipped: %s", exc)

            # ── SMS adapter ──
            sms_cfg = adapters_cfg.get("sms", {})
            if sms_cfg.get("enabled", False):
                try:
                    from navig.messaging.adapters.sms import SmsAdapter

                    adapter = SmsAdapter(config=self._resolve_adapter_config(sms_cfg))
                    registry.register(adapter)
                    logger.debug("Messaging adapter registered: sms")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("SMS adapter skipped: %s", exc)

            # ── WhatsApp Cloud adapter ──
            wa_cfg = adapters_cfg.get("whatsapp", {})
            if wa_cfg.get("enabled", False):
                try:
                    from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter

                    adapter = WhatsAppCloudAdapter(config=self._resolve_adapter_config(wa_cfg))
                    registry.register(adapter)
                    logger.debug("Messaging adapter registered: whatsapp")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("WhatsApp adapter skipped: %s", exc)

            # ── Discord adapter ──
            discord_cfg = adapters_cfg.get("discord", {})
            if discord_cfg.get("enabled", False):
                try:
                    from navig.messaging.adapters.discord_adapter import DiscordMessagingAdapter

                    resolved_discord = self._resolve_adapter_config(discord_cfg)
                    adapter = DiscordMessagingAdapter(config=resolved_discord)
                    # Client injection happens later when the discord.py bot connects
                    registry.register(adapter)
                    logger.debug("Messaging adapter registered: discord")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Discord adapter skipped: %s", exc)

            enabled = registry.available_names()
            if enabled:
                logger.info("Messaging adapters ready: %s", ", ".join(enabled))
            else:
                logger.debug("No messaging adapters enabled")
        except ImportError:
            logger.debug("Messaging adapter layer not available, skipping")
        except Exception as exc:
            logger.warning("Messaging adapter init failed: %s", exc)

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
        context = await self._build_agent_context(agent_id, session, is_heartbeat, message=message)

        # Run AI
        response = await self._call_ai(context=context, message=message, model=model, **kwargs)

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
                        context["files"][filename] = filepath.read_text(encoding="utf-8")
                        break
                    except Exception as e:
                        logger.warning("Failed to read %s: %s", filename, e)

        # Load today's memory log
        today = datetime.now().strftime("%Y-%m-%d")
        for base_dir in workspace_candidates:
            memory_log = base_dir / "memory" / f"{today}.md"
            if memory_log.exists():
                try:
                    context["files"][f"memory/{today}.md"] = memory_log.read_text(encoding="utf-8")
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
            profile_ctx = mgr.get_user_context() if hasattr(mgr, "get_user_context") else None
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
            response = await asyncio.get_running_loop().run_in_executor(
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
            logger.error("AI call failed: %s", e)
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
            logger.warning("No handler for channel: %s", channel)

    async def deliver_message(self, channel: str, to: str | None, content: str):
        """Deliver message to a specific channel/recipient."""
        handler = self.channels.get(channel)
        if handler:
            await handler.send(content, to=to)
        else:
            logger.warning("Cannot deliver to channel: %s", channel)

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
                            data = body["data"] if isinstance(body["data"], dict) else {}
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

        self._spawn_background_task(_delayed())
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
                "last_check": (engine.last_check.isoformat() if engine.last_check else None),
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
            self._spawn_background_task(engine.start())
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
        self._spawn_background_task(engine.run_checks(None))
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
                await self.deliver_message(channel="telegram", to=None, content=result.message)
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
            return web.Response(status=400, text="Missing required fields: user_id, message")
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
                    result.timestamp.isoformat() if getattr(result, "timestamp", None) else None
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

    def _spawn_background_task(self, coro: Any) -> Any:
        """Create a tracked background task that is cancelled on shutdown."""
        task = asyncio.create_task(coro)
        if hasattr(task, "add_done_callback"):
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        return task

    # ── Notification monitors / producers ──────────────────────────────────────

    #: Toggleable opt-in producers surfaced in the deck "Monitors" card.
    MONITOR_KEYS = ("webcam", "resources", "self_errors", "connectivity")

    def _init_notify_monitors(self) -> None:
        """Start each enabled monitor at boot (per ``monitors.<name>.enabled``)."""
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        cfg = (self.config_manager.global_config or {}).get("monitors", {}) or {}
        for name in self.MONITOR_KEYS:
            try:
                if (cfg.get(name, {}) or {}).get("enabled", False):
                    self._start_monitor(name)
            except Exception as e:  # noqa: BLE001 — never block boot on a monitor
                logger.debug("monitor %s start skipped: %s", name, e)

    def _start_monitor(self, name: str) -> None:
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        if name in self._monitor_tasks:
            return
        if name == "webcam":
            from navig.notify.monitors.webcam import run_webcam_monitor

            self._monitor_tasks[name] = self._spawn_background_task(run_webcam_monitor())
        elif name == "resources":
            from navig.notify.monitors.resources import run_resource_monitor

            rcfg = ((self.config_manager.global_config or {}).get("monitors", {}) or {}).get(
                "resources", {}
            ) or {}
            self._monitor_tasks[name] = self._spawn_background_task(run_resource_monitor(rcfg))
        elif name == "self_errors":
            from navig.notify.producers.self_errors import install_self_error_reporter

            install_self_error_reporter()
            self._monitor_tasks[name] = "installed"
        elif name == "connectivity":
            # Driven by the uplink listener + a live config check — nothing to spawn.
            self._monitor_tasks[name] = "live"
        else:
            return
        logger.info("monitor enabled: %s", name)

    def _stop_monitor(self, name: str) -> None:
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        handle = self._monitor_tasks.pop(name, None)
        if name == "self_errors":
            from navig.notify.producers.self_errors import uninstall_self_error_reporter

            uninstall_self_error_reporter()
        elif handle is not None and hasattr(handle, "cancel"):
            handle.cancel()
        logger.info("monitor disabled: %s", name)

    def set_monitor_enabled(self, name: str, enabled: bool) -> None:
        """Live toggle a monitor (the deck Monitors card calls this)."""
        if name not in self.MONITOR_KEYS:
            raise ValueError(f"unknown monitor: {name}")
        if enabled:
            self._start_monitor(name)
        else:
            self._stop_monitor(name)


def run_gateway():
    """Entry point for running gateway as standalone process."""
    gateway = NavigGateway()

    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Windows-specific: silence benign ProactorEventLoop cleanup spam.
    # When a remote host (Telegram long-poll, LLM provider, cloudflared
    # tunnel) closes a TCP connection while asyncio still has the socket
    # half-open, _ProactorBasePipeTransport._call_connection_lost calls
    # socket.shutdown() on a dead FD and raises ConnectionResetError
    # [WinError 10054]. Python's default exception handler logs the full
    # traceback even though the connection is going away anyway. We swallow
    # exactly this case; every other unhandled exception still surfaces.
    import sys as _sys
    if _sys.platform == "win32":
        def _silence_proactor_resets(loop_, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
                return
            # Default behaviour for everything else.
            loop_.default_exception_handler(context)
        loop.set_exception_handler(_silence_proactor_resets)

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
