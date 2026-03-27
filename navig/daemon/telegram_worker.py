from __future__ import annotations

import argparse
import asyncio
import os
import signal
from pathlib import Path

from navig.config import get_config_manager
from navig.daemon.entry import NAVIG_HOME
from navig.gateway.channels.matrix import MatrixChannelAdapter
from navig.gateway.channels.telegram import create_telegram_channel
from navig.gateway.server import NavigGateway
from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    project_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        Path.cwd() / ".env",
        project_root / ".env",
        NAVIG_HOME / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate)
            break


def _telegram_config() -> dict:
    cfg = get_config_manager().global_config or {}
    telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}

    token = os.getenv("TELEGRAM_BOT_TOKEN") or telegram_cfg.get("bot_token")
    return {
        "bot_token": token,
        "allowed_users": telegram_cfg.get("allowed_users", []),
        "allowed_groups": telegram_cfg.get("allowed_groups", []),
        "require_auth": telegram_cfg.get("require_auth", True),
    }


def _deck_config() -> dict:
    """Read deck config from global config."""
    cfg = get_config_manager().global_config or {}
    deck_cfg = cfg.get("deck", {}) if isinstance(cfg, dict) else {}
    return {
        "enabled": deck_cfg.get("enabled", True),
        "port": deck_cfg.get("port", 3080),
        "bind": deck_cfg.get("bind", "127.0.0.1"),
        "static_dir": deck_cfg.get("static_dir"),
        "dev_mode": deck_cfg.get("dev_mode", False),
        "auth_max_age": deck_cfg.get("auth_max_age", 3600),
    }


def _matrix_config() -> dict:
    """Read Matrix config from global config."""
    cfg = get_config_manager().global_config or {}
    matrix_cfg = cfg.get("matrix", {}) if isinstance(cfg, dict) else {}
    return {
        "enabled": matrix_cfg.get("enabled", False),
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
        "auto_connect": bridge_cfg.get("mcp_auto_connect", True),
        "reconnect_interval": bridge_cfg.get("mcp_reconnect_interval", 60),
    }


async def _start_gateway_http(
    gateway: NavigGateway, tg_config: dict, deck_cfg: dict
) -> None:
    """
    Start ONLY the HTTP server portion of the gateway (no blocking loop).

    This configures deck auth and starts aiohttp without entering the
    gateway's own while-loop, since our _run() manages the event loop.
    """
    import logging

    from aiohttp import web

    logger = logging.getLogger(__name__)

    gateway.running = True

    # Create app with CORS middleware
    gateway._app = web.Application(middlewares=[gateway._cors_middleware])

    # Register full gateway routes for parity with standard gateway startup
    from navig.gateway.routes import register_all_routes

    register_all_routes(gateway._app, gateway)

    # Register deck routes with full auth config
    if deck_cfg.get("enabled", True):
        try:
            from navig.gateway.deck import register_deck_routes

            register_deck_routes(
                gateway._app,
                bot_token=tg_config.get("bot_token", ""),
                allowed_users=tg_config.get("allowed_users", []),
                require_auth=tg_config.get("require_auth", True),
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
    site = web.TCPSite(gateway._runner, host, port)
    await site.start()

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


async def _mcp_reconnect_loop(
    gateway: NavigGateway,
    mcp_cfg: dict,
    stop_event: asyncio.Event,
) -> None:
    """Background task: keep MCP Bridge connection alive."""
    import logging

    logger = logging.getLogger(__name__)
    interval = mcp_cfg.get("reconnect_interval", 60)
    name = "vscode-copilot"

    while not stop_event.is_set():
        try:
            await asyncio.sleep(interval)
            if stop_event.is_set():
                break

            mgr = gateway.mcp_client_manager
            if mgr is None:
                continue

            # Check if already connected
            client = mgr.clients.get(name)
            if client and client.connected:
                continue

            # Attempt (re)connect
            logger.info("MCP reconnect: attempting %s → %s", name, mcp_cfg["mcp_url"])
            try:
                from navig.mcp.client import MCPClientConfig

                await mgr.add_client(
                    config=MCPClientConfig(
                        id=name,
                        url=mcp_cfg["mcp_url"],
                        transport="sse" if "http" in mcp_cfg["mcp_url"] else "stdio",
                    )
                )
                logger.info("MCP reconnect: %s connected", name)
            except Exception as e:
                logger.debug("MCP reconnect failed: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("MCP reconnect loop error: %s", e)


async def _run(*, port: int | None = None, enable_gateway: bool = True) -> None:
    import logging

    logger = logging.getLogger(__name__)

    _load_env()
    config = _telegram_config()
    if not config.get("bot_token"):
        logger.info("TELEGRAM_BOT_TOKEN not configured; Telegram bot will be disabled")

    deck_cfg = _deck_config()
    matrix_cfg = _matrix_config()
    mcp_cfg = _mcp_bridge_config()

    gateway = NavigGateway()
    if port is not None:
        gateway.config.port = int(port)
    channel = None
    if config.get("bot_token"):
        channel = create_telegram_channel(gateway, config)
        if channel is None:
            logger.error("Failed to initialize Telegram channel")
        else:
            gateway.channels["telegram"] = channel

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
    mcp_reconnect_task = None
    if mcp_cfg.get("auto_connect"):
        try:
            from navig.mcp import MCPClientManager

            gateway.mcp_client_manager = MCPClientManager()
            try:
                from navig.mcp.client import MCPClientConfig

                await gateway.mcp_client_manager.add_client(
                    config=MCPClientConfig(
                        id="vscode-copilot",
                        url=mcp_cfg["mcp_url"],
                        transport="sse" if "http" in mcp_cfg["mcp_url"] else "stdio",
                    )
                )
                logger.info("MCP Bridge client connected → %s", mcp_cfg["mcp_url"])
            except Exception as e:
                logger.info("MCP Bridge not yet available (will retry): %s", e)
        except ImportError as e:
            logger.warning("MCP module not available: %s", e)

    # Start HTTP server (serves Deck + gateway health endpoints)
    if enable_gateway:
        await _start_gateway_http(gateway, config, deck_cfg)

    # Start Telegram channel
    if channel:
        await channel.start()

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
                and gateway.mcp_client_manager.clients["vscode-copilot"].connected
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

    # ── MCP reconnect background task ──
    if mcp_cfg.get("auto_connect") and gateway.mcp_client_manager:
        mcp_reconnect_task = asyncio.create_task(
            _mcp_reconnect_loop(gateway, mcp_cfg, stop_event)
        )

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    finally:
        # Cancel reconnect loop
        if mcp_reconnect_task:
            mcp_reconnect_task.cancel()
            try:
                await mcp_reconnect_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

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
    asyncio.run(_run(port=args.port, enable_gateway=not args.no_gateway))


if __name__ == "__main__":
    main()
