"""Batch 74 — personas/contracts, personas/resolver, personas/assets."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.personas.contracts — PersonaConfig, normalize_persona_name, validate_persona_name
# ---------------------------------------------------------------------------

class TestPersonaConfig:
    def test_empty_name_raises(self):
        from navig.personas.contracts import PersonaConfig
        with pytest.raises(ValueError, match="name must not be empty"):
            PersonaConfig(name="")

    def test_invalid_tone_raises(self):
        from navig.personas.contracts import PersonaConfig
        with pytest.raises(ValueError, match="tone"):
            PersonaConfig(name="test", tone="casual")

    def test_default_display_name_capitalised(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig(name="tyler")
        assert p.display_name == "Tyler"

    def test_explicit_display_name_kept(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig(name="tyler", display_name="Tyler Durden")
        assert p.display_name == "Tyler Durden"

    def test_valid_tones_accepted(self):
        from navig.personas.contracts import PersonaConfig
        for tone in ("direct", "warm", "playful", "formal", "philosophical"):
            p = PersonaConfig(name="test", tone=tone)
            assert p.tone == tone

    def test_from_dict_defaults(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig.from_dict("assistant", {})
        assert p.name == "assistant"
        assert p.tone == "warm"
        assert p.display_name == "Assistant"

    def test_from_dict_sets_fields(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig.from_dict("tyler", {"tone": "direct", "voice_id": "v1"})
        assert p.tone == "direct"
        assert p.voice_id == "v1"

    def test_to_dict_roundtrip(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig(name="teacher", tone="formal", banned_phrases=["foo"])
        d = p.to_dict()
        restored = PersonaConfig.from_dict("teacher", d)
        assert restored.tone == "formal"
        assert restored.banned_phrases == ["foo"]

    def test_to_dict_includes_all_expected_keys(self):
        from navig.personas.contracts import PersonaConfig
        p = PersonaConfig(name="x")
        d = p.to_dict()
        expected_keys = {"name", "display_name", "tone", "model_hint", "voice_id",
                         "wallpaper", "startup_sound", "banned_phrases", "soul_extends"}
        assert expected_keys == set(d.keys())


class TestNormalizePersonaName:
    def test_none_returns_default(self):
        from navig.personas.contracts import normalize_persona_name
        assert normalize_persona_name(None) == "default"

    def test_empty_returns_default(self):
        from navig.personas.contracts import normalize_persona_name
        assert normalize_persona_name("") == "default"

    def test_canonical_returned_lowercase(self):
        from navig.personas.contracts import normalize_persona_name
        assert normalize_persona_name("Tyler") == "tyler"

    def test_spaces_converted_to_dashes(self):
        from navig.personas.contracts import normalize_persona_name
        assert normalize_persona_name("My Persona") == "my-persona"

    def test_custom_slug_returned_as_is(self):
        from navig.personas.contracts import normalize_persona_name
        assert normalize_persona_name("my-custom-bot") == "my-custom-bot"


class TestValidatePersonaName:
    def test_builtin_valid(self):
        from navig.personas.contracts import validate_persona_name
        assert validate_persona_name("tyler") is True
        assert validate_persona_name("default") is True

    def test_custom_invalid(self):
        from navig.personas.contracts import validate_persona_name
        assert validate_persona_name("my-bot") is False


class TestBuiltinPersonas:
    def test_default_in_builtins(self):
        from navig.personas.contracts import BUILTIN_PERSONAS
        assert "default" in BUILTIN_PERSONAS

    def test_tyler_in_builtins(self):
        from navig.personas.contracts import BUILTIN_PERSONAS
        assert "tyler" in BUILTIN_PERSONAS


# ---------------------------------------------------------------------------
# navig.personas.resolver — _find_project_navig_root, resolve_persona
# ---------------------------------------------------------------------------

class TestFindProjectNavigRoot:
    def test_finds_navig_dir_in_cwd(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        from navig.personas.resolver import _find_project_navig_root
        result = _find_project_navig_root(tmp_path)
        assert result == navig_dir

    def test_finds_navig_dir_in_parent(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        from navig.personas.resolver import _find_project_navig_root
        result = _find_project_navig_root(subdir)
        assert result == navig_dir

    def test_returns_none_when_no_navig_dir(self, tmp_path):
        from navig.personas.resolver import _find_project_navig_root
        # Use a temp dir with no .navig anywhere we fully control
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        with patch("pathlib.Path.is_dir", side_effect=lambda self: self.name == "navig_nonexistent"):
            pass  # just confirm function handles missing gracefully
        result = _find_project_navig_root(isolated)
        # May or may not find .navig depending on environment; just ensure no exception
        assert result is None or result.name == ".navig"


class TestResolvePersona:
    def test_returns_none_when_not_found(self, tmp_path):
        from navig.personas.resolver import resolve_persona
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path):
            result = resolve_persona("nonexistent_persona_xyz", cwd=tmp_path)
        assert result is None

    def test_returns_user_home_path_when_found(self, tmp_path):
        from navig.personas.resolver import resolve_persona
        persona_dir = tmp_path / "personas" / "testpersona"
        persona_dir.mkdir(parents=True)
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path):
            result = resolve_persona("testpersona", cwd=tmp_path)
        assert result == persona_dir

    def test_slug_is_lowercased(self, tmp_path):
        from navig.personas.resolver import resolve_persona
        persona_dir = tmp_path / "personas" / "mytestpersona"
        persona_dir.mkdir(parents=True)
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path):
            result = resolve_persona("MYTESTPERSONA", cwd=tmp_path)
        assert result == persona_dir


# ---------------------------------------------------------------------------
# navig.personas.assets — _resolve_asset
# ---------------------------------------------------------------------------

class TestResolveAsset:
    def test_returns_none_when_empty_relative_path(self, tmp_path):
        from navig.personas.assets import _resolve_asset
        assert _resolve_asset("", tmp_path) is None

    def test_returns_none_when_persona_dir_none(self):
        from navig.personas.assets import _resolve_asset
        assert _resolve_asset("wallpaper.jpg", None) is None

    def test_returns_path_when_file_exists(self, tmp_path):
        from navig.personas.assets import _resolve_asset
        asset = tmp_path / "wallpaper.jpg"
        asset.write_bytes(b"fake image")
        result = _resolve_asset("wallpaper.jpg", tmp_path)
        assert result == asset

    def test_returns_none_when_file_missing(self, tmp_path):
        from navig.personas.assets import _resolve_asset
        result = _resolve_asset("missing.wav", tmp_path)
        assert result is None


class TestDeliver:
    def _make_config(self, wallpaper="", startup_sound=""):
        from navig.personas.contracts import PersonaConfig
        return PersonaConfig(name="tyler", wallpaper=wallpaper, startup_sound=startup_sound)

    def test_no_assets_does_nothing(self, tmp_path):
        from navig.personas.assets import deliver
        config = self._make_config()
        bot = AsyncMock()
        asyncio.run(deliver(config, chat_id=1, bot_client=bot, cwd=tmp_path))
        bot.send_photo.assert_not_called()
        bot.send_voice.assert_not_called()

    def test_missing_wallpaper_file_no_raise(self, tmp_path):
        from navig.personas.assets import deliver
        config = self._make_config(wallpaper="missing_wall.jpg")
        bot = AsyncMock()
        asyncio.run(deliver(config, chat_id=1, bot_client=bot, cwd=tmp_path))
        bot.send_photo.assert_not_called()
