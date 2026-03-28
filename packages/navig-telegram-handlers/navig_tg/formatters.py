"""
telegram/formatters.py - Formats navig-commands-core result dicts into styled Telegram messages.

Each format_<command> function accepts the dict returned by the command handler
and returns a ready-to-send Markdown string. No business logic here.
"""

from __future__ import annotations

from typing import Any


def format_checkdomain(result: dict[str, Any]) -> str:
    """Format a checkdomain result with emoji status indicator."""
    status = result.get("status", "error")
    domain = result.get("domain", "")
    details = result.get("details", "")

    icon = {
        "available": "✅",
        "taken": "❌",
        "error": "⚠️",
    }.get(status, "❓")

    lines = [f"{icon} *{domain}*" if domain else f"{icon} *Unknown domain*"]

    if status == "available":
        lines.append(f"This domain appears to be *available* for registration.")
    elif status == "taken":
        lines.append(f"This domain is *already registered*.")
    else:
        lines.append(f"Could not determine availability.")

    if details:
        lines.append(f"\n_{details}_")

    return "\n".join(lines)


# Registry: maps command name -> formatter function
# navig-telegram-handlers registers these with navig-telegram on load.
FORMATTERS: dict[str, object] = {
    "checkdomain": format_checkdomain,
}
