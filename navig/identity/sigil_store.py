"""
navig.identity.sigil_store — Persist and load the user's NaviEntity.

Storage: ~/.navig/state/entity.json (legacy: ~/.navig/entity.json)
Format: { "seed", "name", "archetype", "palette_key", "resonance", "version" }

The seed is all that's needed to fully re-derive the entity — other fields
are cached for display without needing to re-run derivation on every command.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.identity.entity import NaviEntity

from navig.platform.paths import entity_json_path as _entity_json_path


def _identity_path() -> Path:
    """Lazy path so NAVIG_CONFIG_DIR overrides are always respected."""
    return _entity_json_path()


_SCHEMA_VERSION = 1


def persist_entity(entity: "NaviEntity") -> None:
    """Save entity fields to ~/.navig/entity.json. Creates directories if needed."""
    path = _identity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version":     _SCHEMA_VERSION,
        "seed":        entity.seed,
        "name":        entity.name,
        "archetype":   entity.archetype,
        "palette_key": entity.palette_key,
        "resonance":   entity.resonance,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_entity() -> dict | None:
    """
    Load entity data from ~/.navig/entity.json.

    Returns the raw dict on success, or None if the file is missing,
    empty, or corrupt. Does NOT re-derive the full NaviEntity — call
    derive_entity(data["seed"]) for that.
    """
    if not _identity_path().exists():
        return None
    try:
        raw = _identity_path().read_text(encoding="utf-8").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or "seed" not in data:
            return None
        return data
    except (json.JSONDecodeError, OSError, PermissionError):
        return None


def entity_exists() -> bool:
    """Quick check — does a valid identity file exist?"""
    return load_entity() is not None


def reset_entity() -> None:
    """Delete the identity file (forces re-genesis on next onboard)."""
    p = _identity_path()
    if p.exists():
        p.unlink()


def get_seed_for_session(demo: bool = False) -> str:
    """
    Return the seed to use for this session.

    In demo mode: NAVIG_DEMO_SEED env var (or ``'deadbeef' * 8`` fallback).
    In normal mode: generate_seed() from machine fingerprint.
    """
    import os

    from navig.identity.seed import generate_seed

    if demo:
        return os.environ.get("NAVIG_DEMO_SEED", "deadbeef" * 8)
    return generate_seed()
