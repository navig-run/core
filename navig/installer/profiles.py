"""
Profile-to-module mapping for the NAVIG installer.

Each profile is an ordered list of module names (in navig/installer/modules/).
Modules are applied in order and must be individually idempotent.

Profiles
--------
node            – bare minimum: dirs + CLI verification + legacy migration
operator        – node + shell integration + vault + telegram  (default)
architect       – operator + MCP configuration
system_standard – operator + telegram + MCP + service daemon
system_deep     – system_standard + tray (Windows) + persona assets
"""

from __future__ import annotations

from typing import Dict, List

PROFILE_MODULES: Dict[str, List[str]] = {
    "node": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
    ],
    "operator": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
    ],
    "architect": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
    ],
    "system_standard": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
        "service",
    ],
    "system_deep": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
        "service",
        "tray",
        "persona_assets",
    ],
}

DEFAULT_PROFILE: str = "operator"
VALID_PROFILES: List[str] = list(PROFILE_MODULES)
