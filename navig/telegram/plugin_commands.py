"""The plugin→Telegram command seam.

A plugin owns CLI commands (`navig email list`), and until now there was no way for that
capability to become a chat command: the slash registry is a literal list in core, and core
must not import a plugin. So every plugin surface lived one step away from the chat, and the
parity question — "why can I not just ask the bot for my mail" — had no answer inside the
architecture.

This module is that answer, kept deliberately small:

* a plugin calls :func:`register` with a name, an async handler and a one-line description;
* the dispatcher asks :func:`get` **only after** the platform registry found nothing, so a
  plugin can never shadow a core command, and a name already in the registry is refused
  loudly instead of silently losing;
* the handler is called with the arguments it declares, exactly like a registry handler, and
  an exception inside it is contained: the command answers "this plugin command failed"
  rather than falling through to the model, which would improvise an answer.

Nothing here renders a card. A plugin command builds its own body with
``navig.telegram.card_doc`` and sends it with ``card_transport`` — the same kit core uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "PluginCommand",
    "clear",
    "get",
    "names",
    "register",
    "unregister",
]

#: ``name -> command``. Names are bare (``mail``), never ``/mail``.
_COMMANDS: dict[str, "PluginCommand"] = {}


@dataclass(frozen=True)
class PluginCommand:
    """One chat command owned by a plugin."""

    name: str
    handler: Callable[..., Any]
    description: str = ""
    usage: str = ""
    plugin: str = ""


def _normalise(name: str) -> str:
    return (name or "").strip().lstrip("/").lower()


def _core_names() -> frozenset[str]:
    """The platform registry's names, imported lazily to keep this module import-cheap."""
    try:
        from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY  # noqa: PLC0415

        return frozenset(e.command for e in _SLASH_REGISTRY if e.command)
    except Exception:  # noqa: BLE001 — never block registration on an import failure
        return frozenset()


def register(
    name: str,
    handler: Callable[..., Any],
    *,
    description: str = "",
    usage: str = "",
    plugin: str = "",
) -> bool:
    """Register *handler* as the chat command ``/<name>``. False when it cannot be.

    Refused, with a log line, when the name is empty, already a **core** command (a plugin
    must never shadow the platform), or already registered by another plugin. Re-registering
    the same name from the same plugin replaces it, which is what a reload expects.
    """
    key = _normalise(name)
    if not key or not callable(handler):
        logger.warning("plugin command refused: bad name/handler %r", name)
        return False
    if key in _core_names():
        logger.warning("plugin command %r refused: it is a core command", key)
        return False
    existing = _COMMANDS.get(key)
    if existing is not None and existing.plugin != plugin:
        logger.warning(
            "plugin command %r refused: already owned by %r", key, existing.plugin or "a plugin"
        )
        return False
    _COMMANDS[key] = PluginCommand(
        name=key,
        handler=handler,
        description=description,
        usage=usage or f"/{key}",
        plugin=plugin,
    )
    logger.debug("plugin command registered: /%s (%s)", key, plugin)
    return True


def get(name: str) -> PluginCommand | None:
    """The command registered as ``/<name>``, or ``None``."""
    return _COMMANDS.get(_normalise(name))


def names() -> list[str]:
    """Every registered plugin command, sorted — the surface a help screen could list."""
    return sorted(_COMMANDS)


def unregister(name: str) -> bool:
    """Forget one command (a plugin unload). True when something was removed."""
    return _COMMANDS.pop(_normalise(name), None) is not None


def clear(plugin: str | None = None) -> int:
    """Forget every command, or only *plugin*'s. Returns how many went."""
    if plugin is None:
        count = len(_COMMANDS)
        _COMMANDS.clear()
        return count
    doomed = [key for key, cmd in _COMMANDS.items() if cmd.plugin == plugin]
    for key in doomed:
        del _COMMANDS[key]
    return len(doomed)
