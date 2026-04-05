"""Persona contracts — PersonaConfig dataclass, canonical names, normalization."""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Canonical persona names — shipped with the package
# ---------------------------------------------------------------------------

BUILTIN_PERSONAS: tuple[str, ...] = (
    "default",
    "assistant",
    "tyler",
    "storyteller",
    "philosopher",
    "teacher",
)

# ---------------------------------------------------------------------------
# PersonaConfig — the validated in-memory representation of a persona.yaml
# ---------------------------------------------------------------------------

VALID_TONES = frozenset({"direct", "warm", "playful", "formal", "philosophical"})


@dataclass
class PersonaConfig:
    """Parsed and validated representation of a persona.yaml file."""

    name: str
    display_name: str = ""
    tone: str = "warm"                     # direct | warm | playful | formal | philosophical
    model_hint: str = ""                   # optional per-persona model override
    voice_id: str = ""                     # TTS voice identifier
    wallpaper: str = ""                    # relative path inside persona dir
    startup_sound: str = ""               # relative path inside persona dir
    banned_phrases: list[str] = field(default_factory=list)
    soul_extends: str = ""                 # name of parent persona to inherit from

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PersonaConfig.name must not be empty")
        if self.tone not in VALID_TONES:
            raise ValueError(
                f"PersonaConfig.tone must be one of {sorted(VALID_TONES)}, got {self.tone!r}"
            )
        if not self.display_name:
            self.display_name = self.name.capitalize()

    @classmethod
    def from_dict(cls, name: str, data: dict) -> PersonaConfig:
        """Construct from a parsed YAML dict.  Raises ValueError on invalid tone."""
        return cls(
            name=name,
            display_name=str(data.get("display_name", name.capitalize())),
            tone=str(data.get("tone", "warm")).lower(),
            model_hint=str(data.get("model_hint", "")),
            voice_id=str(data.get("voice_id", "")),
            wallpaper=str(data.get("wallpaper", "")),
            startup_sound=str(data.get("startup_sound", "")),
            banned_phrases=list(data.get("banned_phrases") or []),
            soul_extends=str(data.get("soul_extends", "")),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "tone": self.tone,
            "model_hint": self.model_hint,
            "voice_id": self.voice_id,
            "wallpaper": self.wallpaper,
            "startup_sound": self.startup_sound,
            "banned_phrases": self.banned_phrases,
            "soul_extends": self.soul_extends,
        }


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def normalize_persona_name(name: str | None) -> str:
    """Return the canonical persona slug.  Unknown names fall back to 'default'."""
    value = (name or "").strip().lower().replace(" ", "-")
    if not value:
        return "default"
    if value in BUILTIN_PERSONAS:
        return value
    # Accept free-form slug as-is (user-installed persona)
    return value


def validate_persona_name(name: str) -> bool:
    """Return True if *name* is a known built-in persona slug."""
    return normalize_persona_name(name) in BUILTIN_PERSONAS
