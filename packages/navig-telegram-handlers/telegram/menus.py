"""
telegram/menus.py - InlineKeyboardMarkup builders for navig command results.

Each build_<command>_menu function accepts the dict returned by the command
handler and returns an InlineKeyboardMarkup (or None if no menu applies).
"""

from __future__ import annotations

from typing import Any

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None


def build_checkdomain_menu(result: dict[str, Any]):
    """
    Build an inline keyboard for a checkdomain result.

    - available: buttons to search registrars (Namecheap, Porkbun)
    - taken:     button to do a WHOIS lookup
    - error:     button to retry
    """
    if InlineKeyboardMarkup is None:
        return None

    domain = result.get("domain", "")
    status = result.get("status", "error")

    if status == "available":
        buttons = [
            [
                InlineKeyboardButton(
                    "Register on Namecheap",
                    url=f"https://www.namecheap.com/domains/registration/results/?domain={domain}",
                ),
                InlineKeyboardButton(
                    "Register on Porkbun",
                    url=f"https://porkbun.com/checkout/search?q={domain}",
                ),
            ]
        ]
    elif status == "taken":
        buttons = [
            [
                InlineKeyboardButton(
                    "WHOIS Lookup",
                    url=f"https://who.is/whois/{domain}",
                ),
            ]
        ]
    else:
        # Error - offer retry via callback
        buttons = [
            [
                InlineKeyboardButton(
                    "Retry",
                    callback_data=f"checkdomain:{domain}",
                )
            ]
        ]

    return InlineKeyboardMarkup(buttons)


# Registry: maps command name -> menu builder function
MENUS: dict[str, object] = {
    "checkdomain": build_checkdomain_menu,
}
