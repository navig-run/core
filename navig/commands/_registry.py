"""navig.commands._registry — Runtime command registry for NAVIG packs.

Every command-type pack registers its handlers here during on_load and
deregisters them during on_unload. The registry is a process-level singleton
so all packs share the same dispatch table.

Usage (from a pack handler.py)::

    from navig.commands._registry import CommandRegistry

    def on_load(ctx):
        CommandRegistry.register("checkdomain", handle_checkdomain)

    def on_unload(ctx):
        CommandRegistry.deregister("checkdomain")

Usage (from a caller)::

    from navig.commands._registry import CommandRegistry

    handler = CommandRegistry.get("checkdomain")
    if handler:
        result = handler({"domain": "example.com"}, ctx)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Handler type: async or sync callable that takes (args: dict, ctx: Any) → dict
CommandHandler = Callable[[dict[str, Any], Any], Any]


class _CommandRegistry:
    """Thread-safe singleton for pack command dispatch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[str, CommandHandler] = {}

    # ── Registration ──────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        pack_id: str | None = None,
    ) -> None:
        """Register a command handler by name.

        Args:
            name:     Command name (e.g. ``"checkdomain"``)
            handler:  Callable implementing the command
            pack_id:  Optional owning pack identifier (informational only)

        Overwrites any existing registration and logs a warning when clobbering.
        """
        with self._lock:
            if name in self._handlers:
                logger.warning("_registry: overwriting existing handler for '%s'", name)
            self._handlers[name] = handler
            logger.debug(
                "_registry: registered command '%s'%s",
                name,
                f" (pack={pack_id})" if pack_id else "",
            )

    def deregister(
        self,
        name: str,
        *,
        pack_id: str | None = None,  # noqa: ARG002 (reserved for future audit use)
    ) -> None:
        """Remove a command handler by name. No-op if not registered."""
        with self._lock:
            if name in self._handlers:
                del self._handlers[name]
                logger.debug("_registry: deregistered command '%s'", name)

    def deregister_many(self, names: list[str]) -> None:
        """Remove multiple command handlers at once."""
        for name in names:
            self.deregister(name)

    # ── Lookup ────────────────────────────────────────────────────────────

    def get(self, name: str) -> CommandHandler | None:
        """Return the handler for *name*, or None if not registered."""
        with self._lock:
            return self._handlers.get(name)

    def all(self) -> dict[str, CommandHandler]:
        """Return a snapshot of all registered {name: handler} pairs."""
        with self._lock:
            return dict(self._handlers)

    def names(self) -> list[str]:
        """Return a sorted list of all registered command names."""
        with self._lock:
            return sorted(self._handlers)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._handlers

    def __len__(self) -> int:
        with self._lock:
            return len(self._handlers)

    def run(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        ctx: Any = None,
    ) -> Any:
        """Dispatch command *name* synchronously, handling async handlers transparently.

        Callers can always use this method without knowing whether the underlying
        handler is ``async def`` or a plain function.

        Raises:
            KeyError: if *name* is not registered.
        """
        import asyncio

        handler = self.get(name)
        if handler is None:
            raise KeyError(f"No command registered under '{name}'")
        if asyncio.iscoroutinefunction(handler):
            return asyncio.run(handler(args or {}, ctx))
        return handler(args or {}, ctx)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CommandRegistry commands={self.names()}>"


# Module-level singleton — import and use directly:
#   from navig.commands._registry import CommandRegistry
CommandRegistry = _CommandRegistry()
