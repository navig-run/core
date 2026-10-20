"""Inline-button handlers owned by plugins.

Core's callback router is a fixed chain of prefixes it knows about; a plugin had no way to
put a working button on its own messages. A plugin registers its prefix here from its
``register()``::

    from navig.telegram.plugin_callbacks import register_callback_prefix
    register_callback_prefix("mr:", handle, module="email")

and the channel offers every press that starts with it to ``handle(channel, callback_query,
data)`` — **after** the channel's user/chat authorisation and extension gate have run, so a
plugin never sees a press from someone who may not press anything.

``module`` ties the prefix to the plugin's module switch: while that module is off, a press is
answered "switched off" and never reaches the plugin — the same promise core's own extensions
make ("off means off"). A prefix must be unique and must not shadow a core prefix.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[Any, dict[str, Any], str], Awaitable[None]]


@dataclass(frozen=True)
class _Entry:
    prefix: str
    handler: Handler
    module: str


_REGISTRY: dict[str, _Entry] = {}


def register_callback_prefix(prefix: str, handler: Handler, *, module: str) -> None:
    """Route presses whose ``callback_data`` starts with *prefix* to *handler*.

    Re-registering the same prefix with the same module replaces the handler (a plugin
    reloaded in place); a prefix claimed by another module is refused.
    """
    if not prefix or not prefix.endswith(":") or len(prefix) > 8:
        raise ValueError(f"callback prefix must be short and end with ':' — got {prefix!r}")
    if not module:
        raise ValueError("a plugin callback prefix must name the module that switches it off")
    current = _REGISTRY.get(prefix)
    if current is not None and current.module != module:
        raise ValueError(f"callback prefix {prefix!r} already belongs to module {current.module!r}")
    try:
        from navig.gateway.channels.telegram_extensions import extension_for_callback

        if extension_for_callback(prefix + "x") is not None:
            raise ValueError(f"callback prefix {prefix!r} shadows a core Telegram extension")
    except ImportError:
        pass
    _REGISTRY[prefix] = _Entry(prefix, handler, module)


def unregister_callback_prefix(prefix: str) -> None:
    _REGISTRY.pop(prefix, None)


def registered() -> dict[str, str]:
    """``{prefix: module}`` — for diagnostics and tests."""
    return {p: e.module for p, e in _REGISTRY.items()}


def _module_enabled(module: str) -> bool:
    try:
        from navig.modules.registry import get_registry

        return bool(get_registry().is_enabled(module))
    except Exception:  # noqa: BLE001 — an unreadable module state must not hand presses to a plugin
        logger.debug("module state for %s unreadable — treating as off", module, exc_info=True)
        return False


async def answer(channel: Any, callback_query: dict[str, Any], text: str = "", *, alert: bool = False) -> None:
    """``answerCallbackQuery`` — best effort (an expired query id is not an error worth raising)."""
    try:
        await channel._api_call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query.get("id", ""), "text": text[:190], "show_alert": alert},
        )
    except Exception:  # noqa: BLE001
        logger.debug("answerCallbackQuery failed", exc_info=True)


async def dispatch(channel: Any, callback_query: dict[str, Any], cb_data: str) -> bool:
    """True when a plugin owned this press (handled, or refused because its module is off)."""
    entry = next((e for p, e in _REGISTRY.items() if cb_data.startswith(p)), None)
    if entry is None:
        return False
    if not _module_enabled(entry.module):
        await answer(channel, callback_query, f"{entry.module} is switched off in NAVIG.", alert=True)
        return True
    try:
        await entry.handler(channel, callback_query, cb_data)
    except Exception:  # noqa: BLE001 — a plugin bug answers the press instead of hanging it
        logger.warning("plugin callback %s failed", entry.prefix, exc_info=True)
        await answer(channel, callback_query, "That did not work — see the NAVIG log.", alert=True)
    return True
