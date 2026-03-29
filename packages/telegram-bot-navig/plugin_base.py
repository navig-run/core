"""
plugin_base.py — Base class for all telegram-bot-navig plugins.

Every plugin must:
  1. Subclass BotPlugin.
  2. Implement the three abstract members: meta, command, handle.
  3. Expose a module-level create() factory so the loader can instantiate it.

Enabled/disabled state is held on the instance and persists for the bot's
lifetime.  No restart is required to toggle a plugin.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

try:
    from typing import NotRequired, TypedDict
except ImportError:  # Python <3.11
    from typing_extensions import NotRequired, TypedDict  # type: ignore[assignment]

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed context + event contracts (used by handler.py lifecycle functions)
# ---------------------------------------------------------------------------


class PluginContext(TypedDict):
    """
    Typed dict injected into every handler.py lifecycle call.

    Keys
    ----
    plugin_id  : canonical plugin identifier from plugin.json
    plugin_dir : absolute path to the installed plugin folder
    store_dir  : absolute path to userdata/store/
    config     : merged navig config dict (global + project-local)
    logger     : pre-configured Logger for this plugin
    event_data : present only during on_event calls — absent on on_load/on_unload
    """

    plugin_id: str
    plugin_dir: str
    store_dir: str
    config: dict[str, Any]
    logger: logging.Logger
    event_data: NotRequired[dict[str, Any]]


@dataclass
class PluginEvent:
    """
    NAVIG event dispatched to a handler.py hook.

    name   : matches a hook declared in plugin.json "hooks" list
    source : subsystem that emitted the event  (e.g. "gateway", "scheduler")
    data   : event payload — structure varies per event type
    """

    name: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metadata container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginMeta:
    """Immutable descriptor attached to every plugin."""

    name: str
    description: str
    version: str = "1.0.0"

    def __str__(self) -> str:
        status_icon = ""  # filled in by the loader when rendering /plugins list
        return f"{self.name} v{self.version} — {self.description}"


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class BotPlugin(ABC):
    """
    Base class for every telegram-bot-navig plugin.

    Subclass contract
    -----------------
    meta     : PluginMeta   — static descriptor (name, description, version)
    command  : str          — the Telegram command keyword *without* the slash
                              e.g. "rolldice"  →  /rolldice
    handle() : async        — called when a message matches `command` and the
                              plugin is enabled

    The __call__ dunder wraps handle():
      - returns a polite "disabled" message when enabled == False
      - catches all handler exceptions, logs them, and replies with a
        generic error so the bot never crashes
    """

    def __init__(self) -> None:
        self._enabled: bool = True

    # ------------------------------------------------------------------ #
    # Subclass must implement these                                        #
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def meta(self) -> PluginMeta:
        """Return the plugin's immutable metadata."""
        ...

    @property
    @abstractmethod
    def command(self) -> str:
        """
        Telegram command keyword, without the leading slash.
        Return an empty string "" for passive-only plugins (no slash command).

        Examples:
            "rolldice"   →   /rolldice
            "flip"       →   /flip
            ""           →   (passive only — no /command registered)
        """
        ...

    @abstractmethod
    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Core logic executed when the command is received and the plugin is
        enabled.  Raise freely — the wrapper in __call__ will catch and log.
        """
        ...

    # ------------------------------------------------------------------ #
    # Optional: passive message matching (non-command NL triggers)       #
    # ------------------------------------------------------------------ #

    @property
    def passive_patterns(self) -> list[str]:
        """
        List of regex patterns (compiled with re.IGNORECASE | re.DOTALL)
        that trigger handle_message() on any incoming text message.
        Return [] (default) for slash-command-only plugins.
        """
        return []

    async def handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Called by the loader when any passive_pattern matches a non-command
        message. Override to implement NL / URL-trigger behaviour.
        """
        pass

    # ------------------------------------------------------------------ #
    # Optional: Telegram Business message support (Bot API 7.2+)         #
    # ------------------------------------------------------------------ #

    @property
    def handles_business(self) -> bool:
        """Return True to receive business_message update events."""
        return False

    async def handle_business(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Called for business_message updates when handles_business == True.
        Access the message via update.business_message.
        """
        pass

    # ------------------------------------------------------------------ #
    # Enabled / disabled state                                             #
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Enable this plugin (idempotent)."""
        self._enabled = True
        logger.info("Plugin '%s' enabled", self.meta.name)

    def disable(self) -> None:
        """Disable this plugin (idempotent)."""
        self._enabled = False
        logger.info("Plugin '%s' disabled", self.meta.name)

    # ------------------------------------------------------------------ #
    # Callable entry point (registered with PTB as the command handler)   #
    # ------------------------------------------------------------------ #

    async def __call__(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Dispatch a Telegram update to this plugin.

        Steps:
          1. If disabled → reply with a friendly notice, return early.
          2. Delegate to self.handle().
          3. If handle() raises → log the exception and reply with a safe
             error message; never propagate the exception to PTB.
        """
        if not self._enabled:
            await update.message.reply_text(
                f'Plugin "{self.meta.name}" is currently disabled.'
            )
            return

        try:
            await self.handle(update, context)
        except Exception:
            logger.exception("Unhandled exception in plugin '%s'", self.meta.name)
            await update.message.reply_text(
                "Something went wrong. Please try again later."
            )

    # ------------------------------------------------------------------ #
    # Repr                                                                 #
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        state = "on" if self._enabled else "off"
        return f"<BotPlugin /{self.command} [{state}]>"
