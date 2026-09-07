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
from navig.config import get_config_manager
from navig.core.background import spawn
from navig.debug_logger import get_debug_logger
from navig.gateway.audit_log import AuditLog
from navig.gateway.billing_emitter import BillingEmitter
from navig.gateway.channel_router import ChannelRouter
from navig.gateway.config_watcher import ConfigWatcher
from navig.gateway.cooldown import CooldownTracker
from navig.gateway.policy_gate import PolicyGate
from navig.gateway.session_manager import Session, SessionManager
from navig.gateway.system_events import SystemEventQueue
from navig.workspace_ownership import user_workspace_dir

# Lazy imports for optional modules
_approval_manager = None
_browser_controller = None
_mcp_client_manager = None
_webhook_receiver = None
_task_queue = None
_task_worker = None

logger = get_debug_logger()


def _bind_candidates(preferred: int, last_bound: int | None) -> list[int]:
    """Ordered ports for the self-healing bind.

    ``preferred`` and its five neighbours first, then the port the previous
    run self-healed onto (sticky — keeps the gateway URL stable across
    restarts when the whole preferred range is OS-reserved), and finally
    ``0`` (let the OS pick any free port).
    """
    # 0 means "any free port the OS hands out" — it is not a preference to heal away
    # from, so it must not be expanded. Expanding it produced [0, 1, 2, 3, 4, 5,
    # <last_bound>, 0]: ports 1-5 are privileged and meaningless, and `last_bound` is read
    # from the discovery file, which on a developer's machine is THEIR LIVE GATEWAY. A
    # caller asking for an ephemeral port must never be handed the operator's daemon as a
    # fallback — `tests/e2e/test_gateway_api.py` asks for exactly that, and its own
    # docstring warns that a foreign gateway answering is "a silent false PASS".
    if preferred == 0:
        return [0]
    candidates = [preferred, *range(preferred + 1, preferred + 6)]
    if (
        last_bound is not None
        and 0 < last_bound < 65536
        and last_bound not in candidates
    ):
        candidates.append(last_bound)
    candidates.append(0)
    return candidates


class GatewayConfig:
    """Gateway configuration with defaults."""

    def __init__(self, raw_config: dict[str, Any] = None):
        raw_config = raw_config or {}
        gateway_cfg = raw_config.get("gateway", {})
        #: The raw ``gateway:`` section, kept so a later minted credential can be written
        #: back into the same dict the rest of the boot path reads (see
        #: :func:`_ensure_auth_token`).
        self.raw_gateway_cfg = gateway_cfg

        self.enabled = _section_enabled(gateway_cfg, True)
        # Gateway HTTP port. Default is the canonical _GATEWAY_PORT (8789) — NOT
        # _DAEMON_PORT (8765), which belongs to the IPC/MCP WebSocket daemon. A
        # stale 8765 fallback here made the gateway squat the daemon's port and
        # left every 8789-probing client (doctor, deck, flux, mesh) unable to
        # reach it.
        self.port = gateway_cfg.get("port", _GATEWAY_PORT)
        self.host = gateway_cfg.get("host", "127.0.0.1")
        # Parsed, never minted here. Constructing a GatewayConfig is cheap and tests do
        # it freely; minting at parse time gave a pure constructor a **disk write**, so
        # merely reading the config wrote a token into the operator's real
        # `~/.navig/config.yaml`. The mint belongs to `NavigGateway.start()`, which is
        # the moment the routes actually become reachable.
        self.auth_token = gateway_cfg.get("auth", {}).get("token")

        # Storage directory. The default is the RESOLVED config dir, not a literal
        # "~/.navig": `paths.config_dir()` honours NAVIG_CONFIG_DIR and falls back to
        # ~/.navig, so a normal install is byte-identical while an install that moved its
        # config no longer gets a split brain — config in one place, the gateway's
        # events/task-queue/mesh state in another. It is the same class as the comment
        # directly above (a constructor touching the operator's REAL ~/.navig), which was
        # fixed for the token mint and left standing here: constructing a GatewayConfig in
        # a test read the operator's live events.json, and NavigGateway.__init__ mkdir'd
        # into their home. Measured with an audit of every real-home read during the
        # suite — 22 of them came through this one line.
        from navig.platform.paths import config_dir

        storage = gateway_cfg.get("storage_dir") or str(config_dir())
        self.storage_dir = Path(storage).expanduser()

        # Heartbeat defaults
        heartbeat_cfg = raw_config.get("heartbeat", {})
        self.heartbeat_enabled = _section_enabled(heartbeat_cfg, True)
        self.heartbeat_interval = heartbeat_cfg.get("interval", "30m")

        # Agent config
        agents_cfg = raw_config.get("agents", {})
        self.default_agent = agents_cfg.get("default", "navig")
        self.agents = agents_cfg.get("list", [])


#: The set of values that count as "on" for a monitor toggle. Identical to the deck
#: route's ``_truthy`` (navig/gateway/deck/routes/notify.py) — the gateway (what runs at
#: boot) and the deck card (what the operator sees) MUST agree, or the card can show OFF
#: while the daemon runs the monitor. ``navig config set`` stores raw strings, so
def _monitor_enabled_truthy(v: object) -> bool:
    # Canonical coercion — MUST stay identical to the deck's notify._truthy
    # (enforced by test_monitor_enabled_coercion_matches_the_deck). Handles the
    # raw-string config gotcha: `config set monitors.x.enabled false` → NOT on.
    from navig.core.coerce import coerce_bool

    return coerce_bool(v)


def _ensure_auth_token(gateway_cfg: dict) -> str | None:
    """Mint and persist ``gateway.auth.token`` when the install has none.

    ``require_bearer_auth`` opens with ``if not token: return None`` — **no token means
    open access** — and this key had no default. Seventeen route modules sit behind it,
    including ``POST /approval/{id}/respond``: on a default install any local process
    could answer the agent's pending approvals. An approval endpoint anyone can call is
    not an approval endpoint, and it silently defeats the whole gate.

    Minting rather than refusing, because refusing breaks the one consumer that exists.
    ``navig gateway approve`` is the only caller of those routes and it reads this same
    config key (``gateway_client.gateway_request_headers``), so a minted token is picked
    up transparently. The deck and the desktop authenticate with ``deck.api_key`` — a
    separate credential, on separate routes — so they are untouched. Same shape as
    ``deck.api_key``, which this gateway has always auto-generated and persisted.

    Nothing is derived from this token (unlike ``deck.api_key``, whose hash is the
    lighthouse tenant), so minting one carries no identity or rotation consequences.

    **Enforce only what was persisted.** If the write fails, an in-memory token would be
    a token no client can read — the CLI would send no header and be locked out of its
    own gateway on the next call. So a failed persist returns ``None`` and leaves the
    previous open behaviour in place, loudly. Fail-open is wrong here in general; it is
    the lesser wrong than an operator locked out by a credential that exists nowhere.
    """
    import secrets

    token = secrets.token_urlsafe(32)
    try:
        from navig.config import get_config_manager

        get_config_manager().set_global("gateway.auth.token", token)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "gateway: could not persist an auth token (%s) — the gateway stays "
            "UNAUTHENTICATED. Every local process can reach the admin routes, "
            "including approval responses. Set one by hand: "
            "navig config set gateway.auth.token <secret>",
            exc,
        )
        return None

    # Keep the in-memory section consistent with what was written, so anything else
    # reading this dict during boot sees the same value.
    try:
        gateway_cfg.setdefault("auth", {})["token"] = token
    except Exception:  # noqa: BLE001 — cosmetic; the persisted value is authoritative
        pass

    logger.warning(
        "gateway: no auth token was configured, so one was generated and saved to "
        "gateway.auth.token. The NAVIG CLI reads it automatically; any other client "
        "calling the gateway's admin routes now needs it "
        "(navig config get gateway.auth.token)."
    )
    return token


def _section_enabled(section_cfg: object, default: bool) -> bool:
    """Read a config section's ``enabled`` toggle through the canonical coercion.

    ``navig config set <section>.enabled false`` stores the *string* ``"false"``,
    which is truthy — so a raw ``section_cfg.get("enabled", True)`` would leave the
    feature ON (the config-boolean footgun). :func:`coerce_bool` handles the
    string/number/bool forms; a missing key or non-mapping section → *default*.
    """
    from navig.core.coerce import coerce_bool

    if not isinstance(section_cfg, dict):
        return default
    return coerce_bool(section_cfg.get("enabled"), default=default)


#: ``path -> (mtime_ns, size, text)``. The deep-agent path re-read 5-6 workspace
#: markdown files from disk on EVERY turn with no cache at all. Bounded by the
#: fixed filename set, so no eviction policy is needed.
_WORKSPACE_FILE_CACHE: dict[Path, tuple[int, int, str]] = {}


def _read_workspace_file(path: Path) -> str | None:
    """Read *path*, serving an unchanged file from cache. ``None`` when absent.

    Freshness is ``(st_mtime_ns, st_size)``: nanosecond mtime makes a
    same-size-same-timestamp edit unreachable in practice, and a stat is orders
    of magnitude cheaper than a read of a multi-KB identity document.
    """
    try:
        stat = path.stat()
    except OSError:
        _WORKSPACE_FILE_CACHE.pop(path, None)
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _WORKSPACE_FILE_CACHE.get(path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read %s: %s", path.name, exc)
        return None
    _WORKSPACE_FILE_CACHE[path] = (*key, text)
    return text


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

        # Before anything becomes reachable: make sure the admin routes are actually
        # guarded. `require_bearer_auth` treats "no token" as open access, and
        # `POST /approval/{id}/respond` sits behind it — an unauthenticated gateway lets
        # any local process answer the agent's pending approvals.
        if not self.config.auth_token:
            self.config.auth_token = _ensure_auth_token(
                self.config.raw_gateway_cfg
            )

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

        # Start the system-event processor. Without this, every emit()
        # (board_update, council_update, requests_update, …) piles up in an
        # undrained queue and /api/events serves heartbeats only — live
        # refresh across deck/OS was silently dead for as long as this call
        # was missing. replay_pending=False: the persisted backlog predates
        # the processor ever running, so it is stale by definition.
        try:
            await self.system_events.start(replay_pending=False)
        except Exception as exc:  # noqa: BLE001 — event delivery must never block boot
            logger.error("System event processor failed to start: %s", exc)

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

        # Start the Studio scheduled-post service (fires due social posts). This
        # lives in the optional navig-social plugin; the import is soft (core boots
        # fine without it) and it's skipped when the free "social" module is toggled
        # off. The async lifecycle stays here (core owns gateway start/stop); the
        # engine + routes live in the plugin.
        self.scheduled_post_service = None
        try:
            from navig.modules.registry import get_registry

            _social_on = get_registry().is_enabled("social")
        except Exception:  # noqa: BLE001 — registry unavailable → treat as off
            _social_on = False
        if _social_on:
            try:
                from navig_social.social.scheduler_service import ScheduledPostService

                self.scheduled_post_service = ScheduledPostService(self)
                await self.scheduled_post_service.start()
                _boot_step("scheduler / studio", icon="gear")
            except ImportError as exc:
                # Benign: `social` is enabled but the plugin isn't installed here.
                logger.debug("navig-social not installed; Studio scheduler skipped: %s", exc)
            except Exception as exc:  # noqa: BLE001
                # Enabled AND installed but failed to start → surface it (scheduled posts
                # would silently never fire otherwise).
                logger.warning("Studio scheduler enabled but failed to start: %s", exc)
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

        # Start the mission scheduler only when the "missions" module is enabled
        # AND the autonomous loop is turned on. (The executor itself is always live
        # for the board + manual POSTs; the module toggle gates the autonomous loop
        # so the surfaceless Missions module has a real on/off.)
        try:
            from navig.modules.registry import get_registry

            _missions_module_on = get_registry().is_enabled("missions")
        except Exception:  # noqa: BLE001
            _missions_module_on = True
        if self.mission_scheduler and _missions_module_on and self._missions_autonomous_enabled():
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

        # Stop the system-event processor (symmetric with start()).
        try:
            await self.system_events.stop()
        except Exception:  # noqa: BLE001 — shutdown must not fail on telemetry
            pass

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
            # Mount the deck whenever it is enabled — independent of Telegram.
            # The deck is the operator's local data + control plane: the desktop
            # OS app reaches it over loopback (auto-trusted), and a publicly
            # reachable deck (Lighthouse / Direct / tunnel) is guarded by the
            # Bearer deck.api_key. Telegram-specific behaviour (Mini App initData
            # auth, webhook, bot menu button, the polling channel) stays gated on
            # the bot token, which may be empty here. This lets a fresh install
            # drive setup from the OS UI before any Telegram token exists.
            if _section_enabled(deck_cfg, True):
                register_deck_routes(
                    self._app,
                    bot_token=bot_token,
                    allowed_users=tg_cfg.get("allowed_users", []),
                    require_auth=tg_cfg.get("require_auth", True),
                    deck_cfg=deck_cfg,
                )
                if not bot_token:
                    logger.info(
                        "Deck API mounted without a Telegram bot token — reachable on "
                        "loopback and via the Bearer deck.api_key. The Telegram Mini App / "
                        "webhook stay disabled until a bot token is configured."
                    )
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

        # Start server — SELF-HEALING BIND. The preferred port can be
        # unavailable through no fault of ours: on Windows, WinNAT/Hyper-V
        # reserves *dynamic* port ranges that can silently swallow it (this is
        # why a fixed default like 8789 periodically "dies"), or another process
        # holds it. Rather than crash, try the preferred port, then a few
        # neighbours, then the port the LAST run self-healed onto (sticky —
        # keeps the URL stable across restarts on machines where the whole
        # preferred range is reserved), then let the OS pick ANY free port.
        # Whatever we land on is recorded to ~/.navig/gateway.json so every
        # surface (desktop OS sidecar, deck, CLI) discovers the live URL
        # instead of guessing a hardcoded number.
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        preferred = int(self.config.port)
        last_bound: int | None = None
        try:
            from navig.gateway_client import read_gateway_discovery

            disc = read_gateway_discovery()
            if disc is not None:
                last_bound = disc[0]
        except Exception:  # noqa: BLE001 — stickiness is a convenience only
            pass
        candidates = _bind_candidates(preferred, last_bound)
        site: "web.TCPSite | None" = None
        last_err: Exception | None = None
        for cand in candidates:
            try:
                s = web.TCPSite(self._runner, self.config.host, cand)
                await s.start()
                site = s
                break
            except OSError as exc:  # port reserved / in use — try the next
                last_err = exc
        if site is None:
            raise last_err or OSError("gateway: no bindable port found")
        # Resolve the port we actually bound (differs from preferred when the
        # preferred was taken, and is OS-assigned when we fell back to port 0).
        actual_port = preferred
        try:
            socks = getattr(getattr(site, "_server", None), "sockets", None) or []
            if socks:
                actual_port = int(socks[0].getsockname()[1])
        except Exception:  # noqa: BLE001
            pass
        if actual_port != preferred:
            logger.warning(
                "gateway: preferred port %s unavailable (reserved or in use) — "
                "bound %s instead; recorded to gateway.json for surface discovery",
                preferred,
                actual_port,
            )
        self.config.port = actual_port
        self._write_gateway_discovery(actual_port)

    def _write_gateway_discovery(self, port: int) -> None:
        """Record the LIVE gateway URL to ``~/.navig/gateway.json`` so surfaces
        (desktop OS sidecar, deck) discover the actual port instead of hardcoding
        one — the counterpart to the self-healing bind. Atomic, best-effort;
        never blocks startup."""
        try:
            import json as _json  # noqa: PLC0415

            from navig.platform.paths import config_dir  # noqa: PLC0415

            host = self.config.host or "127.0.0.1"
            client_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            payload = {
                "host": client_host,
                "port": int(port),
                "url": f"http://{client_host}:{port}",
            }
            path = config_dir() / "gateway.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(_json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except Exception:  # noqa: BLE001 — discovery is a convenience, not critical
            logger.debug("gateway discovery write failed", exc_info=True)

    # ── Autonomous mission triggers ──────────────────────────────────

    def _missions_autonomous_enabled(self) -> bool:
        """Master kill-switch for SYSTEM-initiated missions (default False)."""
        try:
            from navig.core.coerce import coerce_bool  # noqa: PLC0415

            # coerce_bool, not bool(): `navig config set missions.autonomous_enabled false`
            # stores the STRING "false" (truthy), so a raw bool() would leave autonomous
            # missions RUNNING when the operator meant to shut them off — a control-safety
            # footgun on the master kill-switch.
            return coerce_bool(
                (self.config_manager.global_config or {})
                .get("missions", {})
                .get("autonomous_enabled", False),
                default=False,
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
        if not _section_enabled(cloud_cfg, True):
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
        cloud_on = manager_alive or _section_enabled(cloud_cfg, True)

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

        REQUIRE_APPROVAL decisions block on ``approval_manager.request_approval``
        (deck Inbox / Telegram / /approval routes resolve it) up to the approval
        policy's ``timeout_seconds``; timeout or denial → 403. No approval
        manager wired → fail CLOSED (403), never open.

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

            mgr = getattr(self, "approval_manager", None)
            if mgr is None:
                # An explicit require_approval rule with no approval channel must
                # fail CLOSED — proceeding would silently void the operator's policy.
                self.audit_log.record(
                    actor=actor,
                    action=action,
                    policy=result.decision.value,
                    status="denied",
                    raw_input=raw_input,
                    metadata={
                        "matched_rule": result.matched_rule,
                        "reason": "approval_unavailable",
                    },
                )
                return web.json_response(
                    {
                        "ok": False,
                        "error": (
                            f"Action '{action}' requires approval but no approval "
                            "manager is available"
                        ),
                        "error_code": "approval_unavailable",
                    },
                    status=403,
                )

            logger.warning(
                "[PolicyGate] Action '%s' by %s requires approval — waiting for a response",
                action,
                actor,
            )
            try:
                approved = await mgr.request_approval(
                    command=action,
                    description=f"Policy gate: '{action}' requested by {actor}",
                    user_id=actor,
                    channel="policy",
                    session_key=f"policy:{actor}",
                )
            except Exception:  # noqa: BLE001 — an approval-flow crash must fail closed
                logger.exception(
                    "[PolicyGate] approval flow failed for '%s' — denying", action
                )
                approved = False

            self.audit_log.record(
                actor=actor,
                action=action,
                policy=result.decision.value,
                status="approved" if approved else "denied",
                raw_input=raw_input,
                metadata={
                    "matched_rule": result.matched_rule,
                    "via": "approval_manager",
                },
            )
            if not approved:
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"Action '{action}' was not approved",
                        "error_code": "approval_denied",
                    },
                    status=403,
                )
            # Approved — the action proceeds, so it counts as a billable event too.
            self.billing_emitter.emit(actor=actor, action=action)
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

            # Policy comes from the operator's config (`approval:` section);
            # an empty config yields the same defaults ApprovalPolicy.default()
            # produced. The audit log MUST be wired here — without it approval
            # decisions leave no trace and auto-evolve can never be enabled
            # (is_audit_log_live() gates it).
            policy = ApprovalPolicy.from_config(self.config_manager.global_config or {})
            self.approval_manager = ApprovalManager(
                gateway=self, policy=policy, audit_log=self.audit_log
            )
            gateway_handler = GatewayApprovalHandler(self.approval_manager)
            self.approval_manager.register_handler("gateway", gateway_handler)
            logger.info("Approval manager initialized")
        except ImportError as e:
            logger.warning("Approval module not available: %s", e)

        # Agent tool-execution gate → ApprovalManager (the #299 twin). Without
        # this bind the ApprovalGate keeps its single-operator default (approve
        # dangerous tools with a warning) — fine for a headless CLI, fail-OPEN
        # inside the gateway where real approval consumers exist. With a live
        # manager, dangerous agent tool calls prompt the operator (deck Inbox /
        # Telegram); with none, they are DENIED and audited — never silently run.
        # A FAILED bind is handled the same way: deny-all, not the default (below).
        try:
            from navig.tools.approval import bind_approval_manager

            bind_approval_manager(self.approval_manager, self.audit_log)
            logger.info(
                "Agent ApprovalGate bound to ApprovalManager (fail closed%s)",
                "" if self.approval_manager is not None else ", no manager: deny-all",
            )
        except Exception as e:  # noqa: BLE001 — never block boot on this
            # Leaving the gate untouched here restores the single-operator default —
            # approve-dangerous-with-a-warning — which is exactly the fail-OPEN state
            # this bind exists to remove, inside the one process where real approval
            # consumers exist. So install deny-all instead: still no raise (boot is
            # never blocked), but an unbindable gate refuses rather than waves through.
            logger.error("ApprovalGate binding failed — installing deny-all: %s", e)
            try:
                from navig.tools.approval import bind_approval_manager as _bind_deny

                _bind_deny(None, self.audit_log)
            except Exception as inner:  # noqa: BLE001
                # The approval module itself is unusable. Both consumers already fail
                # closed on their own in that case (MCP `_gate_tool` raises
                # PermissionError, the agent loop returns a denial), so this is loud
                # rather than fatal.
                logger.error(
                    "ApprovalGate deny-all fallback also failed — consumers fail "
                    "closed independently: %s",
                    inner,
                )

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
            from navig.core.coerce import coerce_bool

            browser_cfg = self.config_manager.global_config.get("browser", {})
            self.browser_controller = BrowserController(
                BrowserConfig(
                    # `navig config set browser.headless false` stores the string "false",
                    # which is truthy — so a raw read forced this daemon-side browser
                    # headless even when the operator had explicitly asked to see it.
                    headless=coerce_bool(browser_cfg.get("headless", True), default=True),
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
            # Initialize webhook receiver. Pass the global config so it loads the operator's
            # configured `webhooks:` sources (with their secrets, signature settings, and
            # enabled/verify_signature toggles) via WebhookReceiver._load_sources — reading
            # config["webhooks"]. The previous code built the receiver with NO config (so the
            # operator's `webhooks:` config was ignored) and then tried to re-add sources via
            # a `configure_source` method that does not exist, passing a `provider` field
            # WebhookSourceConfig does not have — an AttributeError/TypeError for anyone who
            # actually configured a source.
            from navig.webhooks import WebhookReceiver

            self.webhook_receiver = WebhookReceiver(self.config_manager.global_config)

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
            if _section_enabled(mesh_cfg, True):
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
            # OFF the loop. This runs a whole CLI subprocess with a default
            # 300s timeout — inline it froze the entire gateway (deck, OS,
            # webhook, uplink) for as long as the command took, up to five
            # minutes for one queued task.
            result = await asyncio.to_thread(
                subprocess.run,
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

    def _telegram_approval_responder(self):
        """Resolve an approval button tap through the manager, or None if there is none.

        Returning None is deliberate: the keyboard consumer checks for a responder
        and answers "Approval system unavailable" rather than acknowledging a tap
        that resolved nothing.
        """
        manager = getattr(self, "approval_manager", None)
        if manager is None:
            return None

        async def _respond(user_id: int, approved: bool, request_id: str | None):
            if not request_id:
                return False, "⚠️ This button has lost its request id."
            try:
                ok = await manager.respond(request_id=request_id, approved=approved)
            except TypeError:
                # Older doubles take positional args.
                ok = await manager.respond(request_id, approved)
            if not ok:
                return False, "⚠️ Approval request expired or not found."
            return True, ("✅ Approved" if approved else "❌ Denied")

        return _respond

    def _wire_telegram_approvals(self, channel, allowed_users) -> None:
        """Give approval requests a way to reach the operator.

        Nothing sent them before: `TelegramApprovalHandler` was never instantiated
        anywhere, so a mission asking "Remediate health issues?" waited out its
        120 s timeout and auto-denied. Measured on the operator's install: 55
        expiries, every one `channel=mission`, `user_id=system`, auto-DENIED.
        Nothing unsafe ran — it just meant autonomous remediation never could.

        The handler registers itself with the manager in its constructor, so
        building it IS the wiring; it is kept on the gateway only to keep it alive.
        """
        manager = getattr(self, "approval_manager", None)
        if manager is None:
            return
        try:
            from navig.approval.handlers import TelegramApprovalHandler

            owner = None
            if allowed_users:
                try:
                    owner = int(allowed_users[0])
                except (TypeError, ValueError):
                    owner = None
            self._telegram_approval_handler = TelegramApprovalHandler(
                manager, bot=channel, owner_chat_id=owner
            )
            logger.info("Telegram approval prompts wired (owner=%s)", owner)
        except Exception as exc:  # noqa: BLE001 - never block channel startup
            logger.warning("Could not wire Telegram approval prompts: %s", exc)

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
                # The RESPONSE half of approvals. `create_telegram_channel` (the
                # telegram_worker path) has always passed this; THIS construction
                # site never did, so on the gateway's own channel every approval
                # button answered "⚠️ Approval system unavailable" — the seam
                # existed and one of its two callers was wired.
                on_approval_response=self._telegram_approval_responder(),
            )
            self.channels["telegram"] = channel
            # The REQUEST half: something has to ASK. Runs after the channel
            # exists and after _init_autonomous_modules() built the manager.
            self._wire_telegram_approvals(channel, allowed_users)
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
                # There is no second place to look. This used to "fall back" to
                # `ChannelRegistry.instance()`, a classmethod that does not exist, so the
                # branch was unreachable and the comment described a recovery that never
                # happened. The notifier is absent for exactly two reasons and neither is
                # fixable from a registry: the telegram channel is not running, or it has
                # no `allowed_users` (the notifier needs a default chat id — see
                # TelegramChannel._start_notifier). Say which, instead of pretending.
                logger.warning(
                    "Comms dispatcher has NO Telegram notifier (channel running: %s) — "
                    "Telegram-routed comms will not be delivered.",
                    tg_channel is not None,
                )

            # Optional Matrix bot
            matrix_bot = None
            comms_cfg = self.config_manager.global_config.get("comms", {})
            matrix_cfg = comms_cfg.get("matrix", {})
            if _section_enabled(matrix_cfg, False):
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
            if _section_enabled(tg_cfg, True):
                try:
                    from navig.messaging.adapters.telegram_adapter import TelegramMessagingAdapter

                    tg_adapter = TelegramMessagingAdapter()
                    # The LIVE channel is the adapter's bot. This used to go through
                    # `ChannelRegistry.instance()` — a classmethod that does not exist
                    # (the registry only ever exposed the module-level
                    # `get_channel_registry()`), so `hasattr(...)` was permanently False,
                    # the branch never ran, and `set_bot` was never called ONCE. Every
                    # send through this adapter returned
                    # `DeliveryReceipt.failure("Telegram bot not initialised")`.
                    #
                    # `TelegramChannel` satisfies the whole interface the adapter calls —
                    # `send_message(chat_id=, text=, parse_mode=)`, `send_photo` /
                    # `send_video` / `send_animation` / `send_voice` / `send_document`,
                    # and `_session` for URL attachments — and `_msg_id` documents that it
                    # accepts "a dict (channel) or object (PTB Message)", the dict being
                    # exactly what this channel returns. `_init_channels()` runs before
                    # this, so it is already populated.
                    tg_channel = self.channels.get("telegram")
                    if tg_channel is not None:
                        tg_adapter.set_bot(tg_channel)
                    else:
                        logger.warning(
                            "Telegram messaging adapter registered WITHOUT a bot — the "
                            "telegram channel is not running, so every send through it "
                            "will fail. Check the telegram channel's own startup log."
                        )
                    registry.register(tg_adapter)
                    logger.debug("Messaging adapter registered: telegram")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Telegram messaging adapter skipped: %s", exc)

            # ── SMS adapter ──
            sms_cfg = adapters_cfg.get("sms", {})
            if _section_enabled(sms_cfg, False):
                try:
                    from navig.messaging.adapters.sms import SmsAdapter

                    adapter = SmsAdapter(config=self._resolve_adapter_config(sms_cfg))
                    registry.register(adapter)
                    logger.debug("Messaging adapter registered: sms")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("SMS adapter skipped: %s", exc)

            # ── WhatsApp Cloud adapter ──
            wa_cfg = adapters_cfg.get("whatsapp", {})
            if _section_enabled(wa_cfg, False):
                try:
                    from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter

                    adapter = WhatsAppCloudAdapter(config=self._resolve_adapter_config(wa_cfg))
                    registry.register(adapter)
                    logger.debug("Messaging adapter registered: whatsapp")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("WhatsApp adapter skipped: %s", exc)

            # ── Discord adapter ──
            discord_cfg = adapters_cfg.get("discord", {})
            if _section_enabled(discord_cfg, False):
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
        """Register the webhook receiver's routes on the app.

        ``get_routes()`` returns aiohttp ``RouteDef`` objects, which are NOT
        iterable tuples — the previous ``for method, path, handler in …`` raised
        ``TypeError: cannot unpack non-iterable RouteDef`` the moment it ran with
        a receiver set. Hand them straight to ``add_routes`` (the idiomatic form
        the receiver's own docstring documents). NOTE: this method is currently a
        no-op in practice — it runs from ``_start_http_server`` *before*
        ``_init_autonomous_modules`` sets ``self.webhook_receiver`` — so the
        ``/webhook/{source}`` endpoint does not register today. Enabling it is a
        separate decision (it exposes an external endpoint); this fix just ensures
        the registration is correct rather than a latent startup crash.
        """
        if self.webhook_receiver:
            self._app.router.add_routes(self.webhook_receiver.get_routes())

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

    @staticmethod
    def _workspace_file_cache() -> dict:
        """Process-wide ``path -> (mtime_ns, size, text)`` cache for workspace files."""
        return _WORKSPACE_FILE_CACHE

    async def _build_agent_context(
        self,
        agent_id: str,
        session: Session,
        is_heartbeat: bool,
        message: str = "",
    ) -> dict[str, Any]:
        """Build agent context from workspace files."""
        workspace_dir = self.storage_dir / "workspace"
        workspace_candidates = [user_workspace_dir(), workspace_dir]

        context = {
            "agent_id": agent_id,
            "is_heartbeat": is_heartbeat,
            "session_messages": session.messages[-20:],  # Last 20 messages
            "files": {},
        }

        # Identity resolves through the SAME chain as the chat path, so a persona,
        # a space SOUL.md or IDENTITY.md applies here too. This path used to probe
        # a bare "SOUL.md" filename and therefore ignored all three.
        try:
            from navig.personas.soul_loader import resolve_soul

            resolution = resolve_soul()
            if resolution.raw:
                context["files"]["SOUL.md"] = resolution.raw
            context["identity_source"] = resolution.source
            context["identity_shadowed"] = [s.tag for s in resolution.shadowed]
        except Exception as exc:  # noqa: BLE001 — never block a turn on identity
            logger.warning("identity resolution failed on deep path: %s", exc)

        # Load workspace files. GUARDRAILS.md is operator-supplied and only ever
        # ADDS rules — the floor itself is compiled in (see _build_system_prompt).
        files_to_load = ["GUARDRAILS.md", "AGENTS.md", "USER.md", "TOOLS.md"]
        if "SOUL.md" not in context["files"]:
            files_to_load.append("SOUL.md")

        if is_heartbeat:
            files_to_load.append("HEARTBEAT.md")
        else:
            files_to_load.append("MEMORY.md")

        for filename in files_to_load:
            for base_dir in workspace_candidates:
                text = _read_workspace_file(base_dir / filename)
                if text is not None:
                    context["files"][filename] = text
                    break

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
        """Build the deep-agent system prompt from context files.

        Ordered stable-first, volatile-last, for the same prompt-cache reason as
        the chat path: identity and instructions hold for a session, while the
        memory blocks change every turn. Guardrails lead, and they come from code
        — an operator ``GUARDRAILS.md`` can only append to them.
        """
        from navig.agent.conv.guardrails import SOUL_DEMOTION_NOTE, guardrail_block

        files = context.get("files", {})
        parts: list[str] = [guardrail_block(files.get("GUARDRAILS.md", ""))]

        # ── Stable: identity and standing instructions ──────────────────────
        if "SOUL.md" in files:
            parts.append(f"# Your Personality\n{SOUL_DEMOTION_NOTE}\n\n{files['SOUL.md']}")

        if "AGENTS.md" in files:
            parts.append(f"# Instructions\n{files['AGENTS.md']}")

        if "TOOLS.md" in files:
            parts.append(f"# Available Tools & Config\n{files['TOOLS.md']}")

        if "USER.md" in files:
            parts.append(f"# About Your Human\n{files['USER.md']}")

        # ── Volatile: memory, logs, per-turn search results ─────────────────
        # MEMORY.md was loaded into context and then silently dropped here — the
        # long-term memory file the agent is told to keep never reached a prompt.
        if "MEMORY.md" in files:
            parts.append(f"# Long-Term Memory\n{files['MEMORY.md']}")

        for key, value in files.items():
            if key.startswith("memory/"):
                parts.append(f"# Today's Log\n{value}")

        if context.get("memory_context"):
            parts.append(f"# Relevant Memory\n{context['memory_context']}")

        if context.get("user_profile"):
            parts.append(f"# User Profile\n{context['user_profile']}")

        if context.get("is_heartbeat") and "HEARTBEAT.md" in files:
            parts.append(f"# Heartbeat Checklist\n{files['HEARTBEAT.md']}")

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



    def get_queue_size(self) -> int:
        """Get current message queue size."""
        return self._message_queue.qsize()

    def _spawn_background_task(self, coro: Any, *, name: str | None = None) -> Any:
        """Create a tracked background task that is cancelled on shutdown.

        An escaping exception is LOGGED, not silently swallowed: a bare
        ``create_task`` only emits an unhelpful "Task exception was never retrieved"
        at GC time (if ever), so a monitor or producer loop that dies would vanish
        with every light green — exactly the silent failure this daemon keeps
        hardening against. Mirrors :func:`navig.core.background.spawn`'s callback.
        """
        task = asyncio.create_task(coro)
        if name and hasattr(task, "set_name"):
            try:
                task.set_name(name)
            except Exception:  # pragma: no cover — set_name is best-effort
                pass
        if hasattr(task, "add_done_callback"):
            self._background_tasks.add(task)
            task.add_done_callback(self._on_background_task_done)
        return task

    def _on_background_task_done(self, task: Any) -> None:
        """Discard a finished tracked task and surface any exception it raised."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return  # cancellation is the normal shutdown path, not a failure
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "background task %r failed: %r", task.get_name(), exc, exc_info=exc
            )

    # ── Notification monitors / producers ──────────────────────────────────────

    #: Toggleable opt-in producers surfaced in the deck "Monitors" card.
    MONITOR_KEYS = ("webcam", "resources", "self_errors", "connectivity", "config_incidents",
                    "browser_reaper")

    #: Monitors that default ON (started at boot unless explicitly disabled). The rest are
    #: opt-in. config_incidents is the one exception because a MISSED config-rescue event —
    #: a wiped config, a re-identified deck key — is exactly the silent, bot-killing failure
    #: this whole surface exists to surface; it is noiseless on a healthy install (nothing
    #: records) and only pushes if a channel is configured. Kept in sync with the deck
    #: route's _DEFAULT_ON by test_monitor_defaults_agree.
    MONITORS_DEFAULT_ON = frozenset({"config_incidents"})

    def _init_notify_monitors(self) -> None:
        """Start each enabled monitor at boot (per ``monitors.<name>.enabled``).

        The value is COERCED, not read for raw truthiness: ``navig config set`` stores its
        argument as a string, so ``monitors.x.enabled false`` persists the string
        ``"false"`` — which is truthy in Python. A raw check here started a monitor the
        operator had just disabled, while the deck card (which coerces via ``_truthy``)
        correctly showed it OFF. Matched to the deck's coercion; kept in sync by
        test_monitor_enabled_coercion_matches_the_deck.
        """
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        cfg = (self.config_manager.global_config or {}).get("monitors", {}) or {}
        for name in self.MONITOR_KEYS:
            try:
                raw = (cfg.get(name, {}) or {}).get("enabled")
                enabled = _monitor_enabled_truthy(raw) if raw is not None \
                    else name in self.MONITORS_DEFAULT_ON
                if enabled:
                    self._start_monitor(name)
            except Exception as e:  # noqa: BLE001 — never block boot on a monitor
                logger.debug("monitor %s start skipped: %s", name, e)

    def _start_monitor(self, name: str) -> None:
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        if name in self._monitor_tasks:
            return
        if name == "webcam":
            from navig.notify.monitors.webcam import run_webcam_monitor

            self._spawn_monitor_task(name, run_webcam_monitor())
        elif name == "resources":
            from navig.notify.monitors.resources import run_resource_monitor

            rcfg = ((self.config_manager.global_config or {}).get("monitors", {}) or {}).get(
                "resources", {}
            ) or {}
            self._spawn_monitor_task(name, run_resource_monitor(rcfg))
        elif name == "self_errors":
            from navig.notify.producers.self_errors import install_self_error_reporter

            install_self_error_reporter()
            self._monitor_tasks[name] = "installed"
        elif name == "config_incidents":
            from navig.notify.producers.config_incidents import (
                install_config_incident_reporter,
            )

            install_config_incident_reporter()
            self._monitor_tasks[name] = "installed"
        elif name == "browser_reaper":
            from navig.notify.monitors.browser_reaper import run_browser_reaper

            self._spawn_monitor_task(name, run_browser_reaper())
        elif name == "connectivity":
            # Driven by the uplink listener + a live config check — nothing to spawn.
            self._monitor_tasks[name] = "live"
        else:
            return
        logger.info("monitor enabled: %s", name)

    def _spawn_monitor_task(self, name: str, coro: Any) -> Any:
        """Spawn a monitor loop and keep its tracked handle HONEST.

        If the loop ever exits — a crash, or a clean self-stop — the handle in
        ``_monitor_tasks`` is cleared so a dead monitor is not reported as running
        (``_start_monitor`` early-returns while the name is present, so a stale
        handle would make a re-enable silently no-op). The monitors' inner loops
        already survive a single bad poll, so a death here is unexpected and logged.
        """
        task = self._spawn_background_task(coro, name=f"monitor:{name}")
        self._monitor_tasks[name] = task
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(lambda t, n=name: self._on_monitor_task_done(n, t))
        return task

    def _on_monitor_task_done(self, name: str, task: Any) -> None:
        """Clear a finished monitor's tracked handle so its state stays truthful."""
        if self._monitor_tasks.get(name) is not task:
            return  # a newer task already replaced it (stop→start); leave it alone
        self._monitor_tasks.pop(name, None)
        if task.cancelled():
            return  # normal disable/shutdown
        exc = task.exception()
        if exc is not None:
            # The generic done-callback already dumped the traceback; add the
            # monitor identity + the honest state change (it is no longer running).
            logger.error("monitor %s crashed and was cleared from tracking: %r", name, exc)
        else:
            logger.info("monitor %s loop exited on its own; cleared from tracking", name)

    def is_monitor_running(self, name: str) -> bool:
        """Whether a monitor is actually LIVE right now — not merely enabled in config.

        A monitor can be enabled (config) and available (host-capable) yet not running,
        because its loop crashed and ``_on_monitor_task_done`` cleared the handle. A
        status surface that reports config intent alone would show that as a green
        "on" — the "green light over a dead monitor" lie. This reads the real handle:
        a live task (not done), or an installed producer / live marker, is running.
        """
        handle = getattr(self, "_monitor_tasks", {}).get(name)
        if handle is None:
            return False
        done = getattr(handle, "done", None)
        if callable(done):  # an asyncio Task (webcam / resources)
            return not handle.done()
        return True  # a string marker ("installed" / "live") == an active producer

    def _stop_monitor(self, name: str) -> None:
        self._monitor_tasks = getattr(self, "_monitor_tasks", {})
        handle = self._monitor_tasks.pop(name, None)
        if name == "self_errors":
            from navig.notify.producers.self_errors import uninstall_self_error_reporter

            uninstall_self_error_reporter()
        elif name == "config_incidents":
            from navig.notify.producers.config_incidents import (
                uninstall_config_incident_reporter,
            )

            uninstall_config_incident_reporter()
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
        spawn(gateway.stop())

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
