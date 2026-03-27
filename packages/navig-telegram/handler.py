"""
handler.py - Pack lifecycle for navig-telegram.

on_load  : starts the Telegram bot worker in a background thread
on_unload: signals the worker to stop gracefully
on_event : routes NAVIG lifecycle events to Telegram if relevant
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["on_load", "on_unload", "on_event"]


@dataclass
class PluginContext:
    """Runtime context injected by the NAVIG pack host."""

    pack_id: str
    version: str
    store_path: Path
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginEvent:
    """A named event dispatched by the NAVIG runtime."""

    name: str
    payload: dict[str, Any]
    source: str


def on_load(ctx: PluginContext) -> None:
    """Start the Telegram bot worker thread using config from PluginContext."""
    sys.path.insert(0, str(ctx.store_path / "src"))
    try:
        import telegram_worker  # noqa: PLC0415

        # Resolve packages/ dir as the parent of this package's install dir
        packages_dir = ctx.store_path.parent
        telegram_worker.start(ctx.config, packages_dir)
        logger.info("[navig-telegram] Bot worker started")
    except ImportError as exc:
        raise RuntimeError(
            f"navig-telegram: cannot import telegram_worker — {exc}"
        ) from exc
    except Exception as exc:
        logger.error("[navig-telegram] Failed to start bot worker: %s", exc)
        raise


def on_unload(ctx: PluginContext) -> None:
    """Signal the bot worker to stop; must not raise."""
    try:
        import telegram_worker  # noqa: PLC0415

        telegram_worker.stop()
        logger.info("[navig-telegram] Bot worker stopped")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[navig-telegram] on_unload error (suppressed): %s", exc)


def on_event(event: PluginEvent, ctx: PluginContext) -> dict[str, Any] | None:
    """Route relevant NAVIG lifecycle events to Telegram (no-op for most events)."""
    # Future: forward "navig.notification" events to a configured chat_id
    logger.debug(
        "[navig-telegram] on_event: %s from %s (no action)", event.name, event.source
    )
    return None
