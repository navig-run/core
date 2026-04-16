"""
NAVIG CLI Registry
==================

Provides :func:`get_schema` — a machine-readable JSON representation of every
command group and subcommand registered in the NAVIG Typer app.

Used by:
    ``navig --schema``      (cli/__init__.py → _schema_callback)
    ``navig help --schema`` (cli/__init__.py → help command)

The schema is intentionally flat and stable so automation tools, AI agents,
and shell-completion generators can consume it without importing the full CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _generated_manifest_path() -> Path:
    return _repo_root() / "generated" / "commands.json"


def _load_generated_manifest() -> dict[str, Any] | None:
    """Try to load the pre-built commands.json manifest.  Returns ``None`` on failure."""
    path = _generated_manifest_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("commands"), list):
            return data
    except Exception:
        pass  # invalid/corrupt commands.json — fall through to runtime build
    return None


def _build_runtime_manifest() -> dict[str, Any]:
    """Build a manifest at runtime by introspecting the live Typer app."""
    from navig.registry.manifest import build_public_manifest

    return build_public_manifest(validate=False)


def _load_help_registry() -> dict[str, Any]:
    """Load the help dictionary, returning an empty dict on failure."""
    try:
        from navig.cli.help_dictionaries import HELP_REGISTRY

        return HELP_REGISTRY
    except Exception:
        return {}


def _to_group_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Group flat command entries by their top-level group name."""
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        return []

    help_registry = _load_help_registry()
    grouped: dict[str, dict[str, Any]] = {}

    for cmd in commands:
        if not isinstance(cmd, dict):
            continue

        path = str(cmd.get("path", "")).strip()
        parts = path.split()
        # Commands must start with "navig" and have at least one sub-token.
        if len(parts) < 2 or parts[0] != "navig":
            continue

        group_name = parts[1]
        sub_name = " ".join(parts[2:]) if len(parts) > 2 else group_name

        group = grouped.setdefault(
            group_name,
            {
                "name": group_name,
                "description": help_registry.get(group_name, {}).get("desc", ""),
                "commands": [],
            },
        )
        group["commands"].append(
            {
                "name": sub_name,
                "description": str(cmd.get("summary", "")).strip(),
                "path": path,
                "status": cmd.get("status", "stable"),
                "since": cmd.get("since", ""),
                "aliases": cmd.get("aliases", []),
            }
        )

    # Sort groups alphabetically; sort each group's commands by name then path.
    rows = sorted(grouped.values(), key=lambda g: g["name"])
    for row in rows:
        row["commands"] = sorted(
            row["commands"],
            key=lambda c: (str(c.get("name", "")), str(c.get("path", ""))),
        )
    return rows


def _to_flat_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a flat, sorted list of all commands (stripping the 'navig' prefix)."""
    rows: list[dict[str, Any]] = []
    for cmd in manifest.get("commands", []):
        if not isinstance(cmd, dict):
            continue
        path = str(cmd.get("path", "")).strip()
        parts = path.split()
        if len(parts) < 2 or parts[0] != "navig":
            continue
        rows.append(
            {
                "name": " ".join(parts[1:]),
                "description": str(cmd.get("summary", "")).strip(),
                "path": path,
                "status": cmd.get("status", "stable"),
                "since": cmd.get("since", ""),
            }
        )
    return sorted(rows, key=lambda r: str(r.get("name", "")))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_schema() -> dict[str, Any]:
    """Return a stable, JSON-serialisable schema of all registered CLI commands.

    Schema structure::

        {
            "version": "2",
            "groups": [
                {
                    "name": "host",
                    "description": "Manage remote server connections",
                    "commands": [
                        {
                            "name": "list",
                            "description": "List configured hosts",
                            "path": "navig host list",
                            "status": "stable",
                            "since": "",
                            "aliases": []
                        }
                    ]
                }
            ],
            "flat_commands": [{"name": "host list", "description": "..."}]
        }

    This function never raises — any failure during manifest loading or
    introspection is caught and an empty-commands structure is returned.
    """
    manifest = _load_generated_manifest()
    if manifest is None:
        try:
            manifest = _build_runtime_manifest()
        except Exception:
            manifest = {
                "schema_version": "1.0.0",
                "generated_at": "",
                "total": 0,
                "commands": [],
            }

    return {
        "version": "2",
        "schema_version": manifest.get("schema_version", "1.0.0"),
        "generated_at": manifest.get("generated_at", ""),
        "total": manifest.get("total", 0),
        "commands": manifest.get("commands", []),
        "groups": _to_group_rows(manifest),
        "flat_commands": _to_flat_rows(manifest),
    }


# ---------------------------------------------------------------------------
# CLI entry (debugging aid)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(json.dumps(get_schema(), indent=2))
