"""
handler.py - Pack lifecycle for navig-telegram-handlers.

Registers formatters and menu builders with navig-telegram on load.
Gracefully no-ops if navig-telegram is not present.
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
    pack_id: str
    version: str
    store_path: Path
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginEvent:
    name: str
    payload: dict[str, Any]
    source: str


def on_load(ctx: PluginContext) -> None:
    """Register formatters and menu builders with navig-telegram if available."""
    # Add our telegram/ directory to path so sibling imports work
    sys.path.insert(0, str(ctx.store_path / "telegram"))

    try:
        from formatters import FORMATTERS  # noqa: PLC0415
        from menus import MENUS  # noqa: PLC0415

        # Attempt to register with navig-telegram's handler registry
        try:
            from navig_telegram import handler_registry  # noqa: PLC0415
            for name, fn in FORMATTERS.items():
                handler_registry.register_formatter(name, fn)
            for name, fn in MENUS.items():
                handler_registry.register_menu(name, fn)
            logger.info(
                "[navig-telegram-handlers] Registered %d formatter(s) and %d menu(s)",
                len(FORMATTERS), len(MENUS),
            )
        except ImportError:
            # navig-telegram not present or handler_registry not yet implemented
            # This is acceptable - pack degrades gracefully
            logger.info(
                "[navig-telegram-handlers] navig-telegram handler_registry not found "
                "- formatters loaded but not registered (graceful no-op)"
            )
    except Exception as exc:
        logger.warning("[navig-telegram-handlers] on_load warning: %s", exc)
        # Do not raise - this is an optional UX enhancement pack


def on_unload(ctx: PluginContext) -> None:
    """Deregister formatters on removal; must not raise."""
    try:
        from formatters import FORMATTERS  # noqa: PLC0415
        try:
            from navig_telegram import handler_registry  # noqa: PLC0415
            for name in FORMATTERS:
                handler_registry.deregister_formatter(name)
        except ImportError:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[navig-telegram-handlers] on_unload error (suppressed): %s", exc)


def on_event(event: PluginEvent, ctx: PluginContext) -> dict[str, Any] | None:
    """No-op: this pack provides UX only, no event handling."""
    return None