"""
NAVIG CLI Registry
==================

Provides ``get_schema()`` — a machine-readable JSON representation of every
command group and subcommand registered in the NAVIG Typer app.

Used by:
    navig --schema          (cli/__init__.py  →  _schema_callback)
    navig help --schema     (cli/__init__.py  →  help command)

The schema is intentionally flat and stable so automation tools, AI agents,
and shell-completion generators can consume it without importing the full CLI.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_schema() -> Dict[str, Any]:
    """Return a stable JSON-serialisable schema of all registered CLI commands.

    The schema structure is::

        {
            "version": "1",
            "groups": [
                {
                    "name": "host",
                    "description": "Manage remote server connections",
                    "commands": [
                        {"name": "list", "description": "list configured hosts"},
                        ...
                    ]
                },
                ...
            ],
            "flat_commands": [
                {"name": "run", "description": "Execute command on remote host"},
                ...
            ]
        }

    The function is intentionally forgiving — it will never raise; any
    introspection failure for a sub-group is silently skipped so that
    ``navig --schema`` always returns something useful.
    """
    groups: List[Dict[str, Any]] = []
    flat_commands: List[Dict[str, Any]] = []

    try:
        from navig.cli.help_dictionaries import HELP_REGISTRY

        for topic, info in HELP_REGISTRY.items():
            desc = info.get("desc", "")
            commands_dict = info.get("commands", {})
            commands: List[Dict[str, str]] = [
                {"name": cmd_name, "description": cmd_desc}
                for cmd_name, cmd_desc in commands_dict.items()
            ]
            groups.append(
                {
                    "name": topic,
                    "description": desc,
                    "commands": commands,
                }
            )
    except Exception:
        # Fall back to an empty-but-valid structure if help_dictionaries
        # is unavailable (e.g., during testing with a stripped install).
        pass

    # Augment with any additional flat commands not covered by HELP_REGISTRY.
    _FLAT_COMMANDS = [
        {"name": "run", "description": "Execute command on remote host"},
        {"name": "status", "description": "Show active host/app/tunnel status"},
        {"name": "init", "description": "Interactive setup wizard"},
        {"name": "help", "description": "Show help for a topic"},
        {"name": "upgrade", "description": "Upgrade NAVIG to the latest version"},
        {"name": "version", "description": "Show version information"},
        {"name": "menu", "description": "Launch interactive menu"},
    ]
    flat_commands.extend(_FLAT_COMMANDS)

    return {
        "version": "1",
        "groups": groups,
        "flat_commands": flat_commands,
    }


# ---------------------------------------------------------------------------
# CLI entry (not normally called directly, but useful for debugging)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print(json.dumps(get_schema(), indent=2))
