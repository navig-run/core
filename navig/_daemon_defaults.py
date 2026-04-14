"""
navig._daemon_defaults — zero-dependency leaf for daemon / IPC constants.

The canonical runtime defaults live in config/defaults.yaml:
  - daemon.port          → _DAEMON_PORT
  - oauth.redirect_port  → _OAUTH_REDIRECT_PORT

This module holds module-level constants for code paths that cannot call
get_config_manager() (e.g. dataclass field defaults, CLI argument defaults).
"""

from __future__ import annotations

# IPC / MCP WebSocket port.  Canonical default: config/defaults.yaml daemon.port
_DAEMON_PORT: int = 8765

# OAuth PKCE callback server port.  Canonical default: config/defaults.yaml oauth.redirect_port
_OAUTH_REDIRECT_PORT: int = 1455
