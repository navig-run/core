from __future__ import annotations

import asyncio
import argparse
import os
import signal
from pathlib import Path

from navig.config import get_config_manager
from navig.daemon.entry import NAVIG_HOME
from navig.gateway.channels.telegram import create_telegram_channel
from navig.gateway.server import NavigGateway


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


async def _start_gateway_http(gateway: NavigGateway, tg_config: dict, deck_cfg: dict) -> None:
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
            from navig.gateway.deck_api import register_deck_routes
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

    logger.info("Gateway HTTP server started on %s:%d (Deck: %s)",
                host, port, "enabled" if deck_cfg.get("enabled") else "disabled")


async def _stop_gateway_http(gateway: NavigGateway) -> None:
    """Stop the HTTP server cleanly."""
    import logging
    logger = logging.getLogger(__name__)

    gateway.running = False
    if gateway._runner:
        await gateway._runner.cleanup()
        logger.info("Gateway HTTP server stopped")


async def _run(*, port: int | None = None, enable_gateway: bool = True) -> None:
    import logging
    logger = logging.getLogger(__name__)

    _load_env()
    config = _telegram_config()
    if not config.get("bot_token"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")

    deck_cfg = _deck_config()

    gateway = NavigGateway()
    if port is not None:
        gateway.config.port = int(port)
    channel = create_telegram_channel(gateway, config)
    if channel is None:
        raise RuntimeError("Failed to initialize Telegram channel")

    gateway.channels["telegram"] = channel

    # Start HTTP server (serves Deck + gateway health endpoints)
    if enable_gateway:
        await _start_gateway_http(gateway, config, deck_cfg)

    # Start Telegram channel
    await channel.start()

    logger.info(
        "Telegram bot + Deck running as single unit (bot=%s, deck=%s, port=%d)",
        "active",
        "enabled" if deck_cfg.get("enabled") else "disabled",
        gateway.config.port,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    finally:
        # Tightly coupled shutdown: stop bot → stop deck/HTTP → done
        await channel.stop()
        if enable_gateway:
            await _stop_gateway_http(gateway)
        logger.info("Telegram bot + Deck shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NAVIG Telegram worker")
    parser.add_argument("--port", type=int, default=None, help="Override gateway port when gateway is enabled")
    parser.add_argument("--no-gateway", action="store_true", help="Run Telegram bot without gateway HTTP server")
    args = parser.parse_args()
    asyncio.run(_run(port=args.port, enable_gateway=not args.no_gateway))


if __name__ == "__main__":
    main()
