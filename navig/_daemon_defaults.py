"""
navig._daemon_defaults — zero-dependency leaf for port / IPC constants.

The canonical runtime defaults live in config/defaults.yaml:
  - gateway.port         → _GATEWAY_PORT
  - daemon.port          → _DAEMON_PORT
  - oauth.redirect_port  → _OAUTH_REDIRECT_PORT

This module holds module-level constants for code paths that cannot call
get_config_manager() (e.g. dataclass field defaults, CLI argument defaults,
``.get(key, default)`` fallbacks). Import these instead of re-typing the
literal so the default lives in exactly one place and cannot drift.

NB: the gateway HTTP server (_GATEWAY_PORT) and the IPC/MCP WebSocket daemon
(_DAEMON_PORT) are DISTINCT servers on DISTINCT ports. They must never share a
default — a gateway that falls back to _DAEMON_PORT collides with the daemon.
"""

from __future__ import annotations

# Gateway HTTP server port (Telegram bot, Deck API, mesh, install, oauth
# callback). Canonical default: config/defaults.yaml gateway.port
_GATEWAY_PORT: int = 8789

# IPC / MCP WebSocket port.  Canonical default: config/defaults.yaml daemon.port
_DAEMON_PORT: int = 8765

# OAuth PKCE callback server port.  Canonical default: config/defaults.yaml oauth.redirect_port
_OAUTH_REDIRECT_PORT: int = 1455
