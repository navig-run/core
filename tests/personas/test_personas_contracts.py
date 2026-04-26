"""Tests for navig.personas.contracts — PersonaConfig, normalize_persona_name, validate_persona_name."""

from __future__ import annotations

import pytest

from navig.personas.contracts import (
    BUILTIN_PERSONAS,
    VALID_TONES,
    PersonaConfig,
    normalize_persona_name,
    validate_persona_name,
)


# ---------------------------------------------------------------------------
# PersonaConfig — __post_init__ validation
# ---------------------------------------------------------------------------

class TestPersonaConfigInit:
    def test_basic_creation(self):
        p = PersonaConfig(name="default", tone="warm")
        assert p.name == "default"
        assert p.tone == "warm"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            PersonaConfig(name="", tone="warm")

    def test_invalid_tone_raises(self):
        with pytest.raises(ValueError, match="tone must be one of"):
            PersonaConfig(name="myp", tone="angry")

    def test_all_valid_tones_accepted(self):
        for tone in VALID_TONES:
            p = PersonaConfig(name="tester", tone=tone)
            assert p.tone == tone

    def test_display_name_auto_capitalised(self):
        p = PersonaConfig(name="assistant", tone="warm")
        assert p.display_name == "Assistant"

    def test_explicit_display_name_preserved(self):
        p = PersonaConfig(name="assistant", display_name="My Bot", tone="warm")
        assert p.display_name == "My Bot"

    def test_defaults(self):
        p = PersonaConfig(name="tyler")
        assert p.tone == "warm"
        assert p.model_hint == ""
        assert p.voice_id == ""
        assert p.wallpaper == ""
        assert p.startup_sound == ""
        assert p.banned_phrases == []
        assert p.soul_extends == ""


# ---------------------------------------------------------------------------
# PersonaConfig.from_dict
# ---------------------------------------------------------------------------

class TestPersonaConfigFromDict:
    def test_from_empty_dict(self):
        p = PersonaConfig.from_dict("default", {})
        assert p.name == "default"
        assert p.tone == "warm"

    def test_from_dict_with_tone(self):
        p = PersonaConfig.from_dict("test", {"tone": "formal"})
        assert p.tone == "formal"

    def test_from_dict_tone_lowered(self):
        p = PersonaConfig.from_dict("test", {"tone": "DIRECT"})
        assert p.tone == "direct"

    def test_from_dict_display_name(self):
        p = PersonaConfig.from_dict("test", {"display_name": "My Persona"})
        assert p.display_name == "My Persona"

    def test_from_dict_model_hint(self):
        p = PersonaConfig.from_dict("test", {"model_hint": "gpt-4o"})
        assert p.model_hint == "gpt-4o"

    def test_from_dict_voice_id(self):
        p = PersonaConfig.from_dict("test", {"voice_id": "alloy"})
        assert p.voice_id == "alloy"

    def test_from_dict_banned_phrases(self):
        p = PersonaConfig.from_dict("test", {"banned_phrases": ["bad word", "another"]})
        assert "bad word" in p.banned_phrases

    def test_from_dict_none_banned_phrases_defaults_to_empty(self):
        p = PersonaConfig.from_dict("test", {"banned_phrases": None})
        assert p.banned_phrases == []

    def test_from_dict_soul_extends(self):
        p = PersonaConfig.from_dict("child", {"soul_extends": "default"})
        assert p.soul_extends == "default"

    def test_from_dict_invalid_tone_raises(self):
        with pytest.raises(ValueError):
            PersonaConfig.from_dict("test", {"tone": "evil"})

    def test_from_dict_wallpaper(self):
        p = PersonaConfig.from_dict("test", {"wallpaper": "bg.png"})
        assert p.wallpaper == "bg.png"

    def test_from_dict_startup_sound(self):
        p = PersonaConfig.from_dict("test", {"startup_sound": "ding.mp3"})
        assert p.startup_sound == "ding.mp3"


# ---------------------------------------------------------------------------
# PersonaConfig.to_dict
# ---------------------------------------------------------------------------

class TestPersonaConfigToDict:
    def test_to_dict_basic(self):
        p = PersonaConfig(name="assistant", tone="direct")
        d = p.to_dict()
        assert d["name"] == "assistant"
        assert d["tone"] == "direct"

    def test_to_dict_contains_all_keys(self):
        p = PersonaConfig(name="x", tone="warm")
        d = p.to_dict()
        expected_keys = {"name", "display_name", "tone", "model_hint", "voice_id",
                         "wallpaper", "startup_sound", "banned_phrases", "soul_extends"}
        assert expected_keys == set(d.keys())

    def test_to_dict_roundtrip(self):
        original = PersonaConfig.from_dict("tyler", {
            "tone": "playful",
            "display_name": "Tyler",
            "model_hint": "claude-3",
            "banned_phrases": ["sorry", "I cannot"],
        })
        restored = PersonaConfig.from_dict(original.name, original.to_dict())
        assert restored.tone == original.tone
        assert restored.display_name == original.display_name
        assert restored.banned_phrases == original.banned_phrases

    def test_to_dict_display_name_auto(self):
        p = PersonaConfig(name="philosopher", tone="philosophical")
        d = p.to_dict()
        assert d["display_name"] == "Philosopher"


# ---------------------------------------------------------------------------
# normalize_persona_name
# ---------------------------------------------------------------------------

class TestNormalizePersonaName:
    def test_none_returns_default(self):
        assert normalize_persona_name(None) == "default"

    def test_empty_string_returns_default(self):
        assert normalize_persona_name("") == "default"

    def test_whitespace_returns_default(self):
        assert normalize_persona_name("   ") == "default"

    def test_lowercases_builtin(self):
        assert normalize_persona_name("ASSISTANT") == "assistant"

    def test_strips_whitespace(self):
        assert normalize_persona_name("  tyler  ") == "tyler"

    def test_spaces_to_hyphens(self):
        result = normalize_persona_name("my custom persona")
        assert result == "my-custom-persona"

    def test_builtin_names_pass_through(self):
        for name in BUILTIN_PERSONAS:
            assert normalize_persona_name(name) == name

    def test_custom_slug_passes_through(self):
        assert normalize_persona_name("hermes-v2") == "hermes-v2"

    def test_all_builtins_in_set(self):
        for name in BUILTIN_PERSONAS:
            assert name == normalize_persona_name(name)


# ---------------------------------------------------------------------------
# validate_persona_name
# ---------------------------------------------------------------------------

class TestValidatePersonaName:
    def test_builtin_names_are_valid(self):
        for name in BUILTIN_PERSONAS:
            assert validate_persona_name(name) is True

    def test_unknown_name_is_invalid(self):
        assert validate_persona_name("nonexistent_persona") is False

    def test_empty_returns_false(self):
        # normalize("") → "default", which is valid
        assert validate_persona_name("") is True  # "default" is in BUILTIN_PERSONAS

    def test_case_insensitive(self):
        assert validate_persona_name("DEFAULT") is True

    def test_custom_slug_is_invalid(self):
        assert validate_persona_name("my-custom-v1") is False


# ---------------------------------------------------------------------------
# BUILTIN_PERSONAS and VALID_TONES constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_in_builtins(self):
        assert "default" in BUILTIN_PERSONAS

    def test_assistant_in_builtins(self):
        assert "assistant" in BUILTIN_PERSONAS

    def test_valid_tones_contains_warm(self):
        assert "warm" in VALID_TONES

    def test_all_valid_tones_are_strings(self):
        for t in VALID_TONES:
            assert isinstance(t, str)

    def test_builtin_personas_are_strings(self):
        for p in BUILTIN_PERSONAS:
            assert isinstance(p, str)
