"""
commands/__init__.py - Registry of all transport-agnostic command handlers.

Every key in COMMANDS maps a command name (as typed by the user, no slash)
to its async handle(args, ctx) coroutine.

The pack scanner and channel adapters (Telegram, Discord, CLI) all import
COMMANDS from here - adding a new command means adding one entry to this dict.
"""

from __future__ import annotations

from .checkdomain import handle as _checkdomain_handle
from .ping import handle as _ping_handle
from .sysinfo import handle as _sysinfo_handle
from .whois import handle as _whois_handle

# Map command name -> async handle(args: dict, ctx) -> dict
COMMANDS: dict[str, object] = {
    "checkdomain": _checkdomain_handle,
    "ping": _ping_handle,
    "sysinfo": _sysinfo_handle,
    "whois": _whois_handle,
}

__all__ = ["COMMANDS"]
