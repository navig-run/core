"""Persona loader — parse persona.yaml + soul.md (or a community soul.yaml) with
the soul_extends chain."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from navig.personas.contracts import VALID_TONES, PersonaConfig
from navig.personas.resolver import resolve_persona

logger = logging.getLogger(__name__)

_SOUL_EXTENDS_MAX_DEPTH = 5  # prevent infinite inheritance cycles


def _read_soul_md(persona_dir: Path) -> str:
    soul_file = persona_dir / "soul.md"
    if soul_file.exists():
        try:
            return soul_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read soul.md at %s: %s", soul_file, exc)
    return ""


def _read_persona_yaml(persona_dir: Path) -> dict:
    yaml_file = persona_dir / "persona.yaml"
    if not yaml_file.exists():
        return {}
    try:
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid persona.yaml at {yaml_file}: {exc}") from exc
    if isinstance(data, dict):
        return data
    if data is not None:
        logger.warning(
            "persona.yaml at %s is not a mapping (got %s); ignoring", yaml_file, type(data).__name__
        )
    return {}


# Map free-form registry `tone.primary` values onto the strict PersonaConfig tones.
_TONE_ALIASES = {
    "technical": "direct",
    "analytical": "direct",
    "precise": "direct",
    "neutral": "direct",
    "professional": "formal",
    "formal": "formal",
    "friendly": "warm",
    "supportive": "warm",
    "empathetic": "warm",
    "witty": "playful",
    "humorous": "playful",
    "philosophical": "philosophical",
}


def _normalize_tone(value: object) -> str:
    """Coerce a registry ``tone.primary`` into a valid PersonaConfig tone."""
    tone = str(value or "").strip().lower()
    if tone in VALID_TONES:
        return tone
    return _TONE_ALIASES.get(tone, "warm")


def _compose_soul_from_identity(identity: dict, tone_block: dict) -> str:
    """Build injected soul content from a soul.yaml identity/tone block."""
    parts: list[str] = []
    name = str(identity.get("name") or "").strip()
    tagline = str(identity.get("tagline") or "").strip()
    description = str(identity.get("description") or "").strip()
    fmt = str((tone_block or {}).get("format") or "").strip()
    if name:
        parts.append(f"# {name} — {tagline}" if tagline else f"# {name}")
    if description:
        parts.append(description)
    if fmt:
        parts.append(f"## Style\n{fmt}")
    return "\n\n".join(parts).strip()


def read_soul_yaml(persona_dir: Path) -> tuple[dict, str] | None:
    """Adapt a community ``soul.yaml`` (registry schema) into the internal
    persona.yaml dict + injected soul content.

    Registry personas ship a single rich ``soul.yaml`` (``identity`` / ``tone`` /
    ``archetype`` …) rather than ``persona.yaml`` + ``soul.md``. Bridging that
    format is what lets ``navig persona use <community-persona>`` load real
    identity and tone instead of an empty default shell. Returns ``None`` when no
    ``soul.yaml`` is present.
    """
    yaml_file = persona_dir / "soul.yaml"
    if not yaml_file.exists():
        return None
    try:
        raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid soul.yaml at {yaml_file}: {exc}") from exc
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "soul.yaml at %s is not a mapping (got %s); ignoring", yaml_file, type(raw).__name__
            )
        return None
    # identity/tone may be authored as scalars in a malformed file — coerce to
    # dict so .get() below never raises AttributeError on community content.
    identity = raw.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    tone_block = raw.get("tone")
    tone_block = tone_block if isinstance(tone_block, dict) else {}
    data = {
        "display_name": str(identity.get("name") or raw.get("id") or "").strip(),
        "tone": _normalize_tone(tone_block.get("primary")),
        # soul.yaml uses `extends`; persona.yaml uses `soul_extends`.
        "soul_extends": str(raw.get("extends") or "").strip(),
    }
    return data, _compose_soul_from_identity(identity, tone_block)


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

    # Community personas ship a single rich `soul.yaml` instead of
    # `persona.yaml` + `soul.md`. Bridge it in for whichever half is missing.
    if not data or not soul:
        adapted = read_soul_yaml(persona_dir)
        if adapted is not None:
            adapted_data, adapted_soul = adapted
            if not data:
                data = adapted_data
            if not soul:
                soul = adapted_soul

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
