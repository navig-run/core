from __future__ import annotations

import argparse
import asyncio
import errno
import os
import signal
import sys
from pathlib import Path

from navig.config import get_config_manager
from navig.core.coerce import coerce_bool
from navig.gateway.channels.matrix import MatrixChannelAdapter
from navig.gateway.server import NavigGateway, install_loop_noise_filter
from navig.messaging.registry import (
    create_channel_for_provider,
    get_active_provider_name,
    is_supported_provider_name,
    supported_provider_names,
)
from navig.messaging.secrets import resolve_telegram_bot_token, resolve_telegram_uid
from navig.platform import paths as _nav_paths
from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT
from navig.telegram.config import require_auth_enabled


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    project_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        Path.cwd() / ".env",
        project_root / ".env",
        _nav_paths.config_dir() / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate)
            break


def _telegram_config() -> dict:
    cfg = get_config_manager().global_config or {}
    telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}

    token = resolve_telegram_bot_token(cfg)
    owner_uid = resolve_telegram_uid(cfg)
    return {
        "bot_token": token,
        "owner_uid": owner_uid,
        "allowed_users": telegram_cfg.get("allowed_users", []),
        "allowed_groups": telegram_cfg.get("allowed_groups", []),
        "require_auth": require_auth_enabled(telegram_cfg.get("require_auth", True)),
    }


def create_telegram_channel(gateway, config: dict):
    """Backward-compatible helper retained for existing tests/monkeypatches."""
    from navig.gateway.channels.telegram import create_telegram_channel as _create_telegram

    return _create_telegram(gateway, config)


def _deck_config() -> dict:
    """Read deck config from global config."""
    cfg = get_config_manager().global_config or {}
    deck_cfg = cfg.get("deck", {}) if isinstance(cfg, dict) else {}
    return {
        # coerce_bool: `navig config set` stores raw strings and bool("false") is
        # True, so a deck.enabled the operator disabled would stay ON (this daemon
        # gate is separate from the gateway-server one #482 already coerced). deck
        # is a feature (UI/routes), default True — safe to honor "false".
        "enabled": coerce_bool(deck_cfg.get("enabled", True), default=True),
        "port": deck_cfg.get("port", 3080),
        "bind": deck_cfg.get("bind", "127.0.0.1"),
        "static_dir": deck_cfg.get("static_dir"),
        # dev_mode gates the local auth bypass; configure_deck_auth also coerces it
        # (#532), but keep the builder dict honest too.
        "dev_mode": coerce_bool(deck_cfg.get("dev_mode", False), default=False),
        "auth_max_age": deck_cfg.get("auth_max_age", 86400),
        # These two were MISSING, and this dict is a hand-written whitelist, so the
        # consumer just saw defaults. `register_deck_routes` reads both:
        #
        #   api_key      — absent meant `resolve("")` reported the config as WIPED and
        #                  "restored" the key from the vault mirror on every
        #                  registration: a config write, a scary "the config was
        #                  wiped, not freshly installed" warning, and a
        #                  `deck_key_restored` incident each time (84 of them on the
        #                  operator's machine, one every ~130s). Nothing was wiped —
        #                  the reader never asked for the key. Worse, with no vault
        #                  mirror that same path MINTS a new key, which moves the
        #                  Lighthouse tenant and silently kills the bot and Mini App.
        #   telegram_only — absent meant the remote lockdown defaulted to OFF on this
        #                  surface, so `deck.telegram_only=true` was not honoured here
        #                  even though the gateway-server path honoured it.
        #
        # Kept as an explicit list rather than passing the raw dict: the coercions
        # above are load-bearing. tests/daemon/test_deck_config_parity.py derives the
        # required set from what the consumer actually reads, so the next key added
        # there fails the build instead of silently becoming a default.
        "api_key": deck_cfg.get("api_key", ""),
        "telegram_only": coerce_bool(deck_cfg.get("telegram_only", False), default=False),
    }


def _matrix_config() -> dict:
    """Read Matrix config from global config."""
    cfg = get_config_manager().global_config or {}
    matrix_cfg = cfg.get("matrix", {}) if isinstance(cfg, dict) else {}
    return {
        # Matrix is opt-in (default False); coerce so `config set matrix.enabled
        # false` (string "false", truthy) doesn't leave it trying to start.
        "enabled": coerce_bool(matrix_cfg.get("enabled", False), default=False),
        "homeserver": matrix_cfg.get("homeserver", ""),
        "user_id": matrix_cfg.get("user_id", ""),
        "access_token": matrix_cfg.get("access_token", ""),
        "device_id": matrix_cfg.get("device_id", ""),
        "rooms": matrix_cfg.get("rooms", []),
    }


def _mcp_bridge_config() -> dict:
    """Read MCP Bridge auto-connect config from global config."""
    cfg = get_config_manager().global_config or {}
    bridge_cfg = cfg.get("bridge", {}) if isinstance(cfg, dict) else {}
    return {
        "mcp_url": (
            os.getenv("NAVIG_BRIDGE_MCP_URL")
            or bridge_cfg.get("mcp_url")
            or f"ws://127.0.0.1:{BRIDGE_DEFAULT_PORT}"
        ),
        "token": (os.getenv("NAVIG_BRIDGE_LLM_TOKEN") or bridge_cfg.get("token", "")),
        "auto_connect": coerce_bool(bridge_cfg.get("mcp_auto_connect", True), default=True),
    }


async def _start_gateway_http(gateway: NavigGateway, tg_config: dict, deck_cfg: dict) -> None:
    """
    Start ONLY the HTTP server portion of the gateway (no blocking loop).

    This configures deck auth and starts aiohttp without entering the
    gateway's own while-loop, since our _run() manages the event loop.
    """
    import inspect
    import logging

    from aiohttp import web

    logger = logging.getLogger(__name__)

    gateway.running = True

    # Create app with CORS middleware (new-style adapter to avoid aiohttp
    # old-style middleware deprecation warnings).
    @web.middleware
    async def _cors_middleware_adapter(request, handler):
        result = gateway._cors_middleware(request, handler)
        if inspect.isawaitable(result):
            return await result
        return result

    gateway._app = web.Application(middlewares=[_cors_middleware_adapter])

    # Let installed plugins register their route hooks BEFORE register_deck_routes
    # fires `gateway:register_routes` — same ordering the dedicated `gateway` child
    # uses (server.py). Without this the worker's listener serves core deck routes
    # only (plugin routes 404) and, if this process ever handles a callback query,
    # plugin button prefixes (`mr:` …) are not registered. Best-effort: a plugin
    # must never block the bot process.
    try:
        from navig.core.plugins import load_entry_point_plugins

        load_entry_point_plugins()
    except Exception:  # noqa: BLE001
        pass

    # Register full gateway routes for parity with standard gateway startup
    from navig.gateway.routes import register_all_routes

    register_all_routes(gateway._app, gateway)

    # Register deck routes with full auth config. coerce_bool (as the deck auth dict above
    # already does): `config set deck.enabled false` stores the truthy string "false", so a
    # raw read here would register the deck routes even when the operator disabled the deck.
    if coerce_bool(deck_cfg.get("enabled", True), default=True):
        try:
            from navig.gateway.deck import register_deck_routes

            register_deck_routes(
                gateway._app,
                bot_token=tg_config.get("bot_token", ""),
                allowed_users=tg_config.get("allowed_users", []),
                require_auth=require_auth_enabled(tg_config.get("require_auth", True)),
                deck_cfg=deck_cfg,
            )
            logger.info("Deck Mini App activated (tightly coupled to Telegram bot)")
        except Exception as e:
            logger.error("Failed to register Deck routes: %s", e)
    else:
        logger.info("Deck Mini App disabled in config")

    # Start HTTP server
    gateway._runner = web.AppRunner(gateway._app)
    await gateway._runner.setup()

    # Use gateway config for host/port (from gateway section in config.yaml)
    host = gateway.config.host
    port = gateway.config.port
    from navig.platform import accept_guard

    accept_guard.install()  # see navig/platform/accept_guard.py
    site = web.TCPSite(gateway._runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        # EADDRINUSE is not this child's problem to die of.
        #
        # This worker's JOB is the Telegram bot; the deck HTTP server is an extra.
        # When the daemon also runs a dedicated `gateway` child (daemon config
        # `gateway: true`, which is what carries the Lighthouse uplink), BOTH try to
        # bind gateway.config.port and the loser raised here — taking the whole
        # telegram-bot process down with it. Measured on the operator's install:
        # "telegram-bot exited with code 1 - restarting in 120s", attempt 536, a
        # crash loop every two minutes for 18 hours, while the bot itself was
        # perfectly capable of running.
        #
        # Skipping is deliberately better than retrying on a free port: a second
        # deck API on a DIFFERENT port is a split brain (both 8789 and 5176 answered
        # /api/deck/status during that window), and clients resolve one address via
        # gateway.json. Whoever bound first is already serving; this child just runs
        # the bot.
        if getattr(exc, "errno", None) not in (errno.EADDRINUSE, 10048):
            raise
        logger.warning(
            "Deck HTTP server not started: %s:%d is already served by another "
            "process (usually the daemon's own `gateway` child). The Telegram bot "
            "continues; the deck API is reachable on the existing listener.",
            host,
            port,
        )
        await gateway._runner.cleanup()
        gateway._runner = None
        return

    logger.info(
        "Gateway HTTP server started on %s:%d (Deck: %s)",
        host,
        port,
        "enabled" if deck_cfg.get("enabled") else "disabled",
    )


async def _stop_gateway_http(gateway: NavigGateway) -> None:
    """Stop the HTTP server cleanly."""
    import logging

    logger = logging.getLogger(__name__)

    gateway.running = False
    if gateway._runner:
        await gateway._runner.cleanup()
        logger.info("Gateway HTTP server stopped")


def _transport_for_url(url: str) -> str:
    """Map URL scheme to the correct MCPClientConfig transport type.

    http/https  → 'sse'         (HTTP+SSE Streamable-HTTP)
    ws/wss      → 'websocket'   (WebSocket)
    other       → 'stdio'       (legacy fallback; logged as warning)
    """
    import logging as _logging

    lower = (url or "").strip().lower()
    if lower.startswith(("http://", "https://")):
        return "sse"
    if lower.startswith(("ws://", "wss://")):
        return "websocket"
    _logging.getLogger(__name__).warning(
        "MCP URL %r has unrecognised scheme; defaulting to 'stdio'", url
    )
    return "stdio"


async def _run(*, port: int | None = None, enable_gateway: bool = True) -> None:
    import logging

    logger = logging.getLogger(__name__)

    # This worker serves the gateway HTTP itself (_start_gateway_http), never through
    # NavigGateway.start(), so the benign-loop-noise filter must be installed here too —
    # with or without --no-gateway, the Proactor reset reaches this loop either way.
    install_loop_noise_filter(asyncio.get_running_loop())

    _load_env()
    raw_cfg = get_config_manager().global_config or {}
    provider_name = get_active_provider_name(raw_cfg)
    if not is_supported_provider_name(provider_name):
        supported = ", ".join(supported_provider_names())
        msg = (
            f"[NAVIG] FATAL: Unsupported messaging provider '{provider_name}'.\n"
            f"        Set NAVIG_MESSAGING_PROVIDER or messaging.provider to one of: {supported}."
        )
        print(msg, file=sys.stderr, flush=True)
        logger.error(
            "Unsupported messaging provider '%s' (supported: %s)",
            provider_name,
            supported,
        )
        sys.exit(1)

    config = _telegram_config()
    if provider_name == "telegram" and not config.get("bot_token"):
        logger.warning("TELEGRAM_BOT_TOKEN not configured; Telegram bot will be disabled")
        print(
            "[NAVIG] WARNING: TELEGRAM_BOT_TOKEN not set — Telegram bot is disabled.\n"
            "        Set it in ~/.navig/config.yaml (telegram.bot_token) or the "
            "TELEGRAM_BOT_TOKEN env var.",
            file=sys.stderr,
            flush=True,
        )

    deck_cfg = _deck_config()
    matrix_cfg = _matrix_config()
    mcp_cfg = _mcp_bridge_config()

    gateway = NavigGateway()
    if port is not None:
        gateway.config.port = int(port)
    channel = None
    if provider_name == "telegram":
        channel = create_telegram_channel(gateway, config)
    elif provider_name != "none":
        channel = create_channel_for_provider(provider_name, gateway, raw_cfg)

    if provider_name != "none":
        if channel is None:
            if provider_name == "telegram":
                logger.error("Failed to initialize Telegram channel")
            else:
                logger.error("Failed to initialize messaging channel (provider=%s)", provider_name)
        else:
            gateway.channels[provider_name] = channel

    # ── Matrix channel (optional) ──
    matrix_adapter = None
    if matrix_cfg.get("enabled") and matrix_cfg.get("homeserver"):
        try:
            matrix_adapter = MatrixChannelAdapter(matrix_cfg)
            gateway.channels["matrix"] = matrix_adapter
            await matrix_adapter.start()
            logger.info("Matrix channel started (%s)", matrix_cfg.get("homeserver"))
        except Exception as e:
            logger.warning("Matrix channel failed to start (non-fatal): %s", e)
            matrix_adapter = None

    # ── MCP client manager ──
    if mcp_cfg.get("auto_connect"):
        try:
            from navig.mcp import MCPClientManager

            gateway.mcp_client_manager = MCPClientManager()
            try:
                from navig.mcp.client import MCPClientConfig

                # ONE connection owner: add_client (auto_connect=True) starts the
                # first attempt in the background AND arms the registry's health
                # sweep, which retries with exponential backoff (1 min → 30 min) and
                # goes quiet after a few failures. A worker-side 60 s loop used to
                # call connect_client() here and on every tick — the EXPLICIT path,
                # which resets that backoff — so an absent bridge (its only server,
                # navig-grid, was archived) logged WARN+ERROR every minute forever:
                # ~2,500 of 2,712 telegram-bot.log lines on 2026-10-15.
                await gateway.mcp_client_manager.add_client(
                    config=MCPClientConfig(
                        id="vscode-copilot",
                        url=mcp_cfg["mcp_url"],
                        transport=_transport_for_url(mcp_cfg["mcp_url"]),
                    )
                )
                logger.info("MCP Bridge client registered → %s", mcp_cfg["mcp_url"])
            except Exception as e:
                logger.info("MCP Bridge not yet available (will retry): %s", e)
        except ImportError as e:
            logger.warning("MCP module not available: %s", e)

    # Start HTTP server (serves Deck + gateway health endpoints)
    if enable_gateway:
        await _start_gateway_http(gateway, config, deck_cfg)

    # Start Telegram channel
    if channel:
        try:
            await channel.start()
        except RuntimeError as e:
            print(
                f"[NAVIG] FATAL: Telegram channel failed to start: {e}",
                file=sys.stderr,
                flush=True,
            )
            logger.error("Telegram channel failed to start: %s", e)
            sys.exit(1)

    logger.info(
        "Telegram bot + Deck running as single unit (bot=%s, deck=%s, port=%d, matrix=%s, mcp=%s)",
        "active" if channel else "disabled",
        "enabled" if deck_cfg.get("enabled") else "disabled",
        gateway.config.port,
        "active" if matrix_adapter else "off",
        (
            "connected"
            if (
                gateway.mcp_client_manager
                and gateway.mcp_client_manager.clients.get("vscode-copilot", None)
                and gateway.mcp_client_manager.clients["vscode-copilot"].is_connected
            )
            else "pending"
        ),
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # optional feature not implemented by this adapter

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    finally:
        # Stop the MCP manager: disconnects clients and cancels its health sweep
        # (the reconnect owner), so no retry task outlives the worker.
        if gateway.mcp_client_manager is not None:
            try:
                await gateway.mcp_client_manager.stop()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical at shutdown

        # Tightly coupled shutdown: stop bot → stop matrix → stop deck/HTTP → done
        if channel:
            await channel.stop()
        if matrix_adapter:
            try:
                await matrix_adapter.stop()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if enable_gateway:
            await _stop_gateway_http(gateway)
        logger.info("Telegram bot + Deck shutdown complete")


def main() -> None:
    # Runs as `pythonw.exe -m navig.daemon.telegram_worker` — no console, so every console
    # child it spawns would otherwise get a brand-new window. See
    # navig.platform.process.install_windowless_spawn_default; no-op when a console exists.
    try:
        from navig.platform.process import install_windowless_spawn_default

        install_windowless_spawn_default()
    except Exception:  # noqa: BLE001 — cosmetic; never block the worker
        pass

    parser = argparse.ArgumentParser(description="Run NAVIG Telegram worker")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override gateway port when gateway is enabled",
    )
    parser.add_argument(
        "--no-gateway",
        action="store_true",
        help="Run Telegram bot without gateway HTTP server",
    )
    args = parser.parse_args()

    # ONE BRAIN PER DEPLOYMENT. The worker holds the bot's token and delivery, so a
    # second machine running it is precisely the incident: the Windows worker + gateway
    # kept answering as the bot while the outpost was supposed to own it. Refuse unless
    # this machine holds the lease; the Telegram channel adds a second, mode-level guard
    # (`navig.brain.telegram_mode`) so a client install cannot set the webhook either.
    import logging as _logging

    _log = _logging.getLogger("navig.daemon.telegram_worker")
    from navig.brain.guard import OwnershipRefused, require_ownership

    try:
        _lease = require_ownership(service="telegram")
    except OwnershipRefused as exc:
        _log.error("Telegram worker start refused: %s", exc)
        try:  # under pythonw stdout may not exist — the log line above is the record
            print(f"\n  [NAVIG] Telegram worker start refused — {exc}\n", flush=True)
        except OSError:
            pass
        raise SystemExit(exc.exit_code) from None
    if _lease is not None:
        _log.info(
            "Brain ownership: %s (epoch %d, role %s) — this process may run the bot",
            _lease.owner_id,
            _lease.epoch,
            _lease.role,
        )

    # Identity for a STANDALONE worker (navig bot start --background with no service
    # installed): `navig service pids` lists it and a sweeper that follows the
    # contract spares it. Under the supervisor it is already a descendant of
    # supervisor.pid and is not listed twice. Best-effort, removed on clean exit.
    from navig.daemon.launch import remove_worker_pid, write_worker_pid

    write_worker_pid()
    try:
        asyncio.run(_run(port=args.port, enable_gateway=not args.no_gateway))
    finally:
        remove_worker_pid()


if __name__ == "__main__":
    main()
