"""
telegram/handlers.py - Transport adapter: routes Telegram updates to navig-commands handlers.

No business logic lives here. This file only:
  1. Parses the incoming Telegram command and arguments
  2. Delegates to the transport-agnostic handler via the command registry
  3. Formats the returned dict into a Telegram reply string

To add a new command: add it to navig-commands/commands/ and register
a cmd_<name> function here that parses args and calls the handler.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _get_handler(command_name: str):
    """
    Resolve a command handler. Tries the live CommandRegistry first,
    then falls back to a direct import from navig-commands.
    """
    try:
        from navig.commands._registry import CommandRegistry  # noqa: PLC0415

        return CommandRegistry.get(command_name)
    except (ImportError, AttributeError):
        pass
    # Direct import fallback: works when the commands pack is co-installed
    try:
        packages_dir = Path(__file__).parent.parent.parent  # packages/
        candidate_names = ("navig-commands", "navig-commands-core")
        for package_name in candidate_names:
            core_commands = packages_dir / package_name / "commands"
            if core_commands.is_dir() and str(core_commands) not in sys.path:
                sys.path.insert(0, str(core_commands))
        # Try parent dir (authoring mode — navig-core/packages/)
        for package_name in candidate_names:
            auth_commands = (
                Path(__file__).parent.parent.parent.parent
                / package_name
                / "commands"
            )
            if auth_commands.is_dir() and str(auth_commands) not in sys.path:
                sys.path.insert(0, str(auth_commands))
        from __init__ import COMMANDS  # noqa: PLC0415

        return COMMANDS.get(command_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("Cannot resolve handler for %r: %s", command_name, exc)
        return None


def _format_checkdomain(result: dict[str, Any]) -> str:
    """Format a checkdomain result dict into a Telegram Markdown reply."""
    icon = {"available": "✅", "taken": "❌", "error": "⚠️"}.get(
        result.get("status", "error"), "❓"
    )
    domain = result.get("domain", "")
    details = result.get("details", "No details.")
    return f"{icon} *{domain}*\n{details}"


async def cmd_checkdomain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /checkdomain <domain> - check whether a domain is available."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "🌐 *Domain Checker*\n\n"
            "Usage: `/checkdomain example.com`\n\n"
            "Or say:\n• _check domain navig.io_\n• _is schema.cx available_",
            parse_mode="Markdown",
        )
        return

    domain = args[0].lower().strip("./")
    status_msg = await update.message.reply_text(
        f"🔍 Checking `{domain}`…", parse_mode="Markdown"
    )

    handler = _get_handler("checkdomain")
    if handler is None:
        await status_msg.edit_text(
            "⚠️ checkdomain handler not found. Is navig-commands installed?"
        )
        return

    try:
        result = await handler({"domain": domain})
        reply = _format_checkdomain(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("checkdomain handler raised")
        reply = f"⚠️ Error checking `{domain}`: {exc}"

    await status_msg.edit_text(reply, parse_mode="Markdown")


# Registry: maps command string -> handler coroutine
# The telegram_worker reads this to register CommandHandlers automatically.
TELEGRAM_COMMANDS: dict[str, object] = {
    "checkdomain": cmd_checkdomain,
}
