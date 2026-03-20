"""
handler.py - Pack lifecycle for navig-commands-core.

Registers all COMMANDS into the NAVIG command registry on load.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["on_load", "on_unload", "on_event", "PluginContext", "PluginEvent"]


@dataclass
class PluginContext:
    """Runtime context injected by the NAVIG pack host into every lifecycle call."""
    pack_id: str
    version: str
    store_path: Path
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginEvent:
    """A named event dispatched by the NAVIG runtime to subscribed packs."""
    name: str
    payload: dict[str, Any]
    source: str


def on_load(ctx: PluginContext) -> None:
    """Register all command handlers from commands/COMMANDS into the runtime registry."""
    try:
        from commands import COMMANDS  # noqa: PLC0415
        # Attempt to register with the live command registry if available
        try:
            from navig.commands._registry import CommandRegistry  # noqa: PLC0415
            for name, handler in COMMANDS.items():
                CommandRegistry.register(name, handler, pack_id=ctx.pack_id)
                logger.info("[navig-commands-core] Registered command: %s", name)
        except ImportError:
            # Registry not wired yet - log and continue (pack still usable via direct import)
            logger.info(
                "[navig-commands-core] CommandRegistry not found - %d commands available via direct import",
                len(COMMANDS),
            )
    except Exception as exc:
        logger.error("[navig-commands-core] on_load failed: %s", exc)
        raise


def on_unload(ctx: PluginContext) -> None:
    """Deregister all commands on pack removal; must not raise."""
    try:
        from commands import COMMANDS  # noqa: PLC0415
        try:
            from navig.commands._registry import CommandRegistry  # noqa: PLC0415
            for name in COMMANDS:
                CommandRegistry.deregister(name, pack_id=ctx.pack_id)
        except ImportError:
            pass  # optional dependency not installed; feature disabled
        logger.info("[navig-commands-core] Unloaded %d commands", len(COMMANDS))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[navig-commands-core] on_unload error (suppressed): %s", exc)


def on_event(event: PluginEvent, ctx: PluginContext) -> dict[str, Any] | None:
    """Route generic named events to matching command handlers if name matches a command."""
    from commands import COMMANDS  # noqa: PLC0415
    handler = COMMANDS.get(event.name)
    if handler is None:
        return None
    import asyncio  # noqa: PLC0415
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(handler(event.payload, ctx))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.error("[navig-commands-core] on_event(%s) error: %s", event.name, exc)
        return None