"""
src/telegram_worker.py - Telegram bot worker.

Scans the packages/ directory for all installed packages. For each package that
contains telegram/handlers.py, imports it and registers all cmd_* functions
as Telegram CommandHandlers.

Reads bot token from:
  1. .navig/config.json key "telegram_token"
  2. ~/.navig/config.yaml key "telegram_bot_token"
  3. env var TELEGRAM_BOT_TOKEN
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STOP_EVENT: threading.Event | None = None
_BOT_THREAD: threading.Thread | None = None


def _get_token(config: dict[str, Any]) -> str:
    """Resolve the Telegram bot token from config, YAML, or env."""
    # 1. Pack config (.navig/config.json)
    token = config.get("telegram_token", "").strip()
    if token:
        return token
    # 2. Global YAML config (~/.navig/config.yaml)
    try:
        import yaml  # noqa: PLC0415

        cfg_path = Path.home() / ".navig" / "config.yaml"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            token = data.get("telegram_bot_token", "").strip()
            if token:
                return token
    except Exception:  # noqa: BLE001
        pass
    # 3. Environment variable
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _load_pack_handlers(app: Any, pack_dir: Path) -> int:
    """
    Look for telegram/handlers.py in a pack directory.
    Import it and register all cmd_* functions as CommandHandlers.
    Returns count of handlers registered.
    """
    from telegram.ext import CommandHandler  # noqa: PLC0415

    handlers_file = pack_dir / "telegram" / "handlers.py"
    if not handlers_file.is_file():
        return 0

    module_name = f"_navig_tg_{pack_dir.name.replace('@', '_').replace('-', '_')}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, handlers_file)
        if spec is None or spec.loader is None:
            logger.warning("Cannot load spec for %s", handlers_file)
            return 0
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to import %s: %s", handlers_file, exc)
        return 0

    count = 0
    # TELEGRAM_COMMANDS dict in handlers.py is the preferred registration mechanism
    telegram_commands = getattr(module, "TELEGRAM_COMMANDS", None)
    if isinstance(telegram_commands, dict):
        for cmd_name, fn in telegram_commands.items():
            app.add_handler(CommandHandler(cmd_name, fn))
            logger.info("Telegram: registered /%s from %s", cmd_name, pack_dir.name)
            count += 1
    else:
        # Fallback: auto-discover all cmd_* functions
        for attr in dir(module):
            if attr.startswith("cmd_"):
                command = attr[len("cmd_") :]
                app.add_handler(CommandHandler(command, getattr(module, attr)))
                logger.info(
                    "Telegram: auto-registered /%s from %s", command, pack_dir.name
                )
                count += 1

    return count


def load_all_handlers(app: Any, store_plugins_root: Path) -> int:
    """Scan every installed pack for telegram/handlers.py and register all found."""
    if not store_plugins_root.is_dir():
        logger.warning("packages dir not found at %s", store_plugins_root)
        return 0

    total = 0
    for pack_dir in sorted(store_plugins_root.iterdir()):
        if pack_dir.is_dir():
            total += _load_pack_handlers(app, pack_dir)
    logger.info("Telegram worker: registered %d handler(s) total", total)
    return total


def run_worker(
    config: dict[str, Any], store_plugins_root: Path, stop_event: threading.Event
) -> None:
    """Main worker function - run in a background thread via on_load."""
    from telegram.ext import Application  # noqa: PLC0415

    token = _get_token(config)
    if not token:
        logger.error(
            "Telegram worker: no token found. "
            "Set telegram_token in .navig/config.json or telegram_bot_token in ~/.navig/config.yaml"
        )
        return

    app = Application.builder().token(token).build()
    count = load_all_handlers(app, store_plugins_root)
    logger.info("Telegram worker: starting with %d command handler(s)", count)

    async def _run():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            stop_event.wait()  # block until on_unload signals stop
            await app.updater.stop()
            await app.stop()

    import asyncio  # noqa: PLC0415

    asyncio.run(_run())


def start(config: dict[str, Any], store_plugins_root: Path) -> threading.Thread:
    """Start telegram_worker in a background thread. Returns the thread."""
    global _STOP_EVENT, _BOT_THREAD
    _STOP_EVENT = threading.Event()
    _BOT_THREAD = threading.Thread(
        target=run_worker,
        args=(config, store_plugins_root, _STOP_EVENT),
        daemon=True,
        name="navig-telegram-worker",
    )
    _BOT_THREAD.start()
    return _BOT_THREAD


def stop() -> None:
    """Signal the telegram worker to stop gracefully."""
    global _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
