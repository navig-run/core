"""navig.bot.ai_tool_registry — canonical alias for the AI LLM tool-call schema registry.

Re-exports everything from ``navig.bot.command_registry`` under a name that
clearly disambiguates this registry (AI function-call schemas) from the pack
command dispatch registry (``navig.commands._registry.CommandRegistry``).

Prefer importing from here in new code::

    from navig.bot.ai_tool_registry import get_command_registry, BotCommand

``navig.bot.command_registry`` is kept for backward compatibility.
"""

from navig.bot.command_registry import (  # noqa: F401
    BotCommand,
    CommandRegistry,
    get_command_registry,
)

__all__ = ["BotCommand", "CommandRegistry", "get_command_registry"]
