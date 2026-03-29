"""Persona loader — parse persona.yaml + soul.md with soul_extends chain."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from navig.personas.contracts import PersonaConfig
from navig.personas.resolver import resolve_persona

logger = logging.getLogger(__name__)

_SOUL_EXTENDS_MAX_DEPTH = 5  # prevent infinite inheritance cycles


def _read_soul_md(persona_dir: Path) -> str:
    soul_file = persona_dir / "soul.md"
    if soul_file.exists():
        try:
            return soul_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read soul.md at %s: %s", soul_file, exc)
    return ""


def _read_persona_yaml(persona_dir: Path) -> dict:
    yaml_file = persona_dir / "persona.yaml"
    if not yaml_file.exists():
        return {}
    try:
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise ValueError(f"Invalid persona.yaml at {yaml_file}: {exc}") from exc


def _load_raw_chain(name: str, depth: int = 0, cwd: Path | None = None) -> tuple[dict, str]:
    """Recursively load persona dicts up the soul_extends chain.

    Returns (merged_yaml_dict, merged_soul_content) from parent → child order.
    """
    if depth >= _SOUL_EXTENDS_MAX_DEPTH:
        logger.warning("soul_extends chain depth limit (%d) reached at '%s'", _SOUL_EXTENDS_MAX_DEPTH, name)
        return {}, ""

    persona_dir = resolve_persona(name, cwd=cwd)
    if persona_dir is None:
        if name == "default":
            return {}, ""
        logger.warning("Persona '%s' not found; skipping soul_extends", name)
        return {}, ""

    data = _read_persona_yaml(persona_dir)
    soul = _read_soul_md(persona_dir)

    parent_name = str(data.get("soul_extends", "")).strip()
    if parent_name and parent_name != name:
        parent_data, parent_soul = _load_raw_chain(parent_name, depth + 1, cwd=cwd)
        # Parent values are the base; child values override
        merged = {**parent_data, **data}
        # Soul: child replaces parent if non-empty; else inherit parent
        merged_soul = soul if soul else parent_soul
        return merged, merged_soul

    return data, soul


def load_persona(
    name: str,
    cwd: Path | None = None,
) -> tuple[PersonaConfig, str]:
    """Load and validate a persona by name.

    Returns ``(PersonaConfig, soul_md_content)``.

    Raises:
        FileNotFoundError: if the persona directory cannot be found at any level.
        ValueError:         if persona.yaml fails schema validation.
    """
    from navig.personas.resolver import resolve_persona  # avoid circular

    persona_dir = resolve_persona(name, cwd=cwd)
    if persona_dir is None:
        raise FileNotFoundError(
            f"Persona '{name}' not found in project, user home, or package defaults."
        )

    merged_data, soul_content = _load_raw_chain(name, cwd=cwd)

    try:
        config = PersonaConfig.from_dict(name, merged_data)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Persona '{name}' validation failed: {exc}") from exc

    return config, soul_content
