"""navig_telegram.handler_registry — Formatter and menu registry for navig-telegram.

Extension packs (including legacy aliases canonicalized to navig-telegram) call::

    from navig_telegram import handler_registry

    handler_registry.register_formatter("checkdomain", my_format_fn)
    handler_registry.register_menu("checkdomain", my_menu_fn)

navig-telegram's telegram_worker then uses these registries when building
replies, falling back to plain text when no formatter is registered.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Formatter: (result: dict) → str (Markdown)
FormatterFn = Callable[[dict[str, Any]], str]
# Menu builder: (result: dict) → InlineKeyboardMarkup (or equivalent)
MenuFn = Callable[[dict[str, Any]], Any]

_lock = threading.Lock()
_formatters: dict[str, FormatterFn] = {}
_menus: dict[str, MenuFn] = {}


# ── Formatters ────────────────────────────────────────────────────────────────


def register_formatter(name: str, fn: FormatterFn) -> None:
    """Register a Markdown formatter for *name* (e.g. "checkdomain")."""
    with _lock:
        if name in _formatters:
            logger.warning("handler_registry: overwriting formatter for '%s'", name)
        _formatters[name] = fn
        logger.debug("handler_registry: registered formatter '%s'", name)


def deregister_formatter(name: str) -> None:
    """Remove formatter for *name*. No-op if not registered."""
    with _lock:
        _formatters.pop(name, None)
        logger.debug("handler_registry: deregistered formatter '%s'", name)


def get_formatter(name: str) -> FormatterFn | None:
    """Return the formatter for *name*, or None."""
    with _lock:
        return _formatters.get(name)


def format_result(name: str, result: dict[str, Any], fallback: str = "") -> str:
    """Format *result* using the registered formatter, or return *fallback*."""
    fn = get_formatter(name)
    if fn is None:
        return fallback
    try:
        return fn(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("handler_registry: formatter '%s' raised: %s", name, exc)
        return fallback


# ── Menus ─────────────────────────────────────────────────────────────────────


def register_menu(name: str, fn: MenuFn) -> None:
    """Register an inline-keyboard menu builder for *name*."""
    with _lock:
        if name in _menus:
            logger.warning("handler_registry: overwriting menu for '%s'", name)
        _menus[name] = fn
        logger.debug("handler_registry: registered menu '%s'", name)


def deregister_menu(name: str) -> None:
    """Remove menu builder for *name*. No-op if not registered."""
    with _lock:
        _menus.pop(name, None)
        logger.debug("handler_registry: deregistered menu '%s'", name)


def get_menu(name: str) -> MenuFn | None:
    """Return the menu builder for *name*, or None."""
    with _lock:
        return _menus.get(name)


def build_menu(name: str, result: dict[str, Any]) -> Any | None:
    """Build the inline keyboard for *name* + *result*, or return None."""
    fn = get_menu(name)
    if fn is None:
        return None
    try:
        return fn(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("handler_registry: menu builder '%s' raised: %s", name, exc)
        return None


# ── Introspection ─────────────────────────────────────────────────────────────


def registered_formatters() -> list[str]:
    """Return sorted list of all registered formatter names."""
    with _lock:
        return sorted(_formatters)


def registered_menus() -> list[str]:
    """Return sorted list of all registered menu names."""
    with _lock:
        return sorted(_menus)
