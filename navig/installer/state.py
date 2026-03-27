"""
Thin state-persistence layer for the installer.

Writes one JSONL entry per action result to:
    ~/.navig/history/install_<profile>_<timestamp>.jsonl

This file is consumed by:  navig init rollback --last
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.installer.contracts import Action, InstallerContext, Result


def _manifest_path(ctx: InstallerContext) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ctx.config_dir / "history" / f"install_{ctx.profile}_{ts}.jsonl"


def save(
    actions: list[Action],
    results: list[Result],
    ctx: InstallerContext,
    manifest_path: Path | None = None,
) -> Path:
    """Persist action/result pairs as JSONL. Returns the manifest path."""
    path = manifest_path or _manifest_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as fh:
        for action, result in zip(actions, results):
            record = {
                "profile": ctx.profile,
                "action_id": action.id,
                "module": action.module,
                "description": action.description,
                "reversible": action.reversible,
                "state": result.state.value,
                "message": result.message,
                "error": result.error,
                "undo_data": result.undo_data,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "python": sys.version,
            }
            fh.write(json.dumps(record) + "\n")

    return path


def load_last(config_dir: Path, profile: str | None = None) -> list[dict]:
    """Load the most recent manifest for *profile* (or any profile)."""
    history_dir = config_dir / "history"
    if not history_dir.exists():
        return []

    pattern = f"install_{profile}_*.jsonl" if profile else "install_*.jsonl"
    manifests = sorted(history_dir.glob(pattern))
    if not manifests:
        return []

    with open(manifests[-1], encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
