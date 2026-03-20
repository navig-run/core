"""CommandRegistry: extensible registry for bot command schemas.

Provides the architectural pattern for registering bot commands.
The canonical schemas currently live in ``navig.bot.command_tools``
(``COMMAND_TOOLS`` list).  This registry acts as the forward-looking
abstraction layer — new commands should be registered here directly
rather than appended to ``COMMAND_TOOLS``.

Design
------
- Registry holds ``BotCommand`` instances indexed by function name.
- ``command_tools.py`` populates the registry at import time, so all
  existing consumers keep working without any changes.
- Future callers can register additional bot commands from their own
  modules without touching ``command_tools.py``.

Usage
-----
Registering a new command::

    from navig.bot.command_registry import get_command_registry

    registry = get_command_registry()

    @registry.register
    def my_command_schema() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "my_command",
                "description": "Does something useful.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

Querying the registry::

    registry.get("my_command")    # → BotCommand | None
    registry.all()                # → list[BotCommand]
    registry.schemas()            # → list[dict]  ← drop-in for COMMAND_TOOLS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class BotCommand:
    """A single bot command schema entry."""

    name: str
    schema: dict[str, Any]
    # Optional metadata
    tags: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"BotCommand(name={self.name!r})"


# Sentinel returned by .get() when a command is not found
_MISSING_CMD: BotCommand = BotCommand(
    name="__missing__",
    schema={},
    tags=["sentinel"],
)


class CommandRegistry:
    """Registry that maps bot command names to their AI function-call schemas."""

    def __init__(self) -> None:
        self._commands: dict[str, BotCommand] = {}

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def add(self, schema: dict[str, Any], *, tags: list[str] | None = None) -> BotCommand:
        """Register a raw command schema dict (mirrors the ``COMMAND_TOOLS`` list entry format).

        Parameters
        ----------
        schema:
            OpenAI function-calling schema ``{"type": "function", "function": {...}}``.
        tags:
            Optional metadata tags for filtering/grouping.

        Returns the created ``BotCommand`` for chaining or inspection.
        """
        try:
            name: str = schema["function"]["name"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "Schema must have shape {'type': 'function', 'function': {'name': ..., ...}}"
            ) from exc

        if name in self._commands:
            logger.debug("CommandRegistry: overwriting schema for %r", name)

        cmd = BotCommand(name=name, schema=schema, tags=tags or [])
        self._commands[name] = cmd
        return cmd

    def register(self, fn: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
        """Decorator: call ``fn()`` to get the schema, then register it.

        The decorated function is returned unchanged so it can still be called
        directly if needed.
        """
        schema = fn()
        self.add(schema)
        return fn

    def bulk_load(
        self,
        schemas: list[dict[str, Any]],
        *,
        tags: list[str] | None = None,
    ) -> None:
        """Load a list of raw schema dicts (e.g. from ``COMMAND_TOOLS``)."""
        for schema in schemas:
            self.add(schema, tags=tags)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get(self, name: str) -> BotCommand | None:
        """Return the ``BotCommand`` for *name*, or ``None`` if not found."""
        return self._commands.get(name)

    def all(self) -> list[BotCommand]:
        """Return all registered commands (insertion order preserved)."""
        return list(self._commands.values())

    def schemas(self) -> list[dict[str, Any]]:
        """Return all schemas as a plain list — drop-in replacement for ``COMMAND_TOOLS``."""
        return [cmd.schema for cmd in self._commands.values()]

    def names(self) -> list[str]:
        """Return all registered command names."""
        return list(self._commands.keys())

    def __len__(self) -> int:
        return len(self._commands)

    def __contains__(self, name: str) -> bool:
        return name in self._commands

    def __repr__(self) -> str:  # pragma: no cover
        return f"CommandRegistry({len(self._commands)} commands)"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: CommandRegistry | None = None


def get_command_registry() -> CommandRegistry:
    """Return the process-wide ``CommandRegistry`` singleton.

    The registry is populated on first access by importing
    ``navig.bot.command_tools``, which calls ``_populate_registry()``.
    """
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
        # Lazy-populate from existing COMMAND_TOOLS list
        _populate_from_command_tools(_registry)
    return _registry


def _populate_from_command_tools(reg: CommandRegistry) -> None:
    """Seed the registry from the canonical ``COMMAND_TOOLS`` list."""
    try:
        from navig.bot.command_tools import COMMAND_TOOLS  # type: ignore[import]

        reg.bulk_load(COMMAND_TOOLS, tags=["core"])
        logger.debug("CommandRegistry: loaded %d commands from command_tools", len(reg))
    except ImportError as exc:
        logger.warning("CommandRegistry: could not load command_tools: %s", exc)
