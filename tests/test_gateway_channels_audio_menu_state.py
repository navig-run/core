"""
Tests for navig/gateway/channels/audio_menu/state.py
Covers AudioConfig dataclass, load_config(), save_config().
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.gateway.channels.audio_menu.state as state_mod
from navig.gateway.channels.audio_menu.state import (
    AudioConfig,
    load_config,
    save_config,
)


# ---------------------------------------------------------------------------
# Fixture: clear module-level cache before/after every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    state_mod._cache.clear()
    yield
    state_mod._cache.clear()


# ---------------------------------------------------------------------------
# AudioConfig dataclass
# ---------------------------------------------------------------------------

class TestAudioConfig:
    def test_default_provider(self):
        assert AudioConfig().provider == "openai"

    def test_default_model(self):
        assert AudioConfig().model == "tts-1-hd"

    def test_default_voice(self):
        assert AudioConfig().voice == "nova"

    def test_default_speed(self):
        assert AudioConfig().speed == 1.0

    def test_default_format(self):
        assert AudioConfig().format == "mp3"

    def test_default_auto(self):
        assert AudioConfig().auto is False

    def test_default_active(self):
        assert AudioConfig().active is False

    def test_custom_values(self):
        cfg = AudioConfig(provider="azure", model="neural", voice="aria", speed=1.5, format="ogg", auto=True, active=True)
        assert cfg.provider == "azure"
        assert cfg.voice == "aria"
        assert cfg.speed == 1.5
        assert cfg.auto is True

    def test_field_count(self):
        assert len(fields(AudioConfig)) == 7

    def test_asdict_roundtrip(self):
        cfg = AudioConfig(voice="alloy", speed=0.8)
        d = asdict(cfg)
        restored = AudioConfig(**d)
        assert restored == cfg


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_returns_audioconfig(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(1)
        assert isinstance(cfg, AudioConfig)

    def test_defaults_when_no_file(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(999)
        assert cfg.provider == "openai"
        assert cfg.voice == "nova"

    def test_loads_from_existing_file(self, tmp_path):
        user_file = tmp_path / "42.json"
        user_file.write_text(json.dumps({"provider": "azure", "voice": "aria", "model": "tts-1-hd", "speed": 1.0, "format": "mp3", "auto": False, "active": False}))
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(42)
        assert cfg.provider == "azure"
        assert cfg.voice == "aria"

    def test_ignores_unknown_keys(self, tmp_path):
        user_file = tmp_path / "10.json"
        user_file.write_text(json.dumps({"provider": "openai", "voice": "nova", "unknown_key": "ignored", "model": "tts-1-hd", "speed": 1.0, "format": "mp3", "auto": False, "active": False}))
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(10)
        assert cfg.provider == "openai"

    def test_corrupt_json_returns_defaults(self, tmp_path):
        user_file = tmp_path / "7.json"
        user_file.write_text("{ broken json {{")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(7)
        assert cfg.provider == "openai"

    def test_cached_on_second_call(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg1 = load_config(5)
            cfg2 = load_config(5)
        assert cfg1 is cfg2  # same object from cache

    def test_stored_in_cache(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            load_config(3)
        assert 3 in state_mod._cache

    def test_partial_json_fills_defaults(self, tmp_path):
        # Only some fields present — rest get dataclass defaults
        user_file = tmp_path / "20.json"
        user_file.write_text(json.dumps({"provider": "edge"}))
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(20)
        assert cfg.provider == "edge"


# ---------------------------------------------------------------------------
# save_config()
# ---------------------------------------------------------------------------

class TestSaveConfig:
    def test_updates_cache(self, tmp_path):
        cfg = AudioConfig(voice="echo")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(99, cfg)
        assert state_mod._cache[99] is cfg

    def test_persists_to_disk(self, tmp_path):
        cfg = AudioConfig(voice="shimmer", speed=1.3)
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(11, cfg)
            loaded = load_config(11)
        assert loaded.voice == "shimmer"
        assert loaded.speed == 1.3

    def test_file_created(self, tmp_path):
        cfg = AudioConfig()
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(55, cfg)
        assert (tmp_path / "55.json").exists()

    def test_file_is_valid_json(self, tmp_path):
        cfg = AudioConfig(format="ogg")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(66, cfg)
        data = json.loads((tmp_path / "66.json").read_text())
        assert data["format"] == "ogg"

    def test_roundtrip(self, tmp_path):
        original = AudioConfig(provider="edge", model="neural", voice="aria", speed=0.9, format="wav", auto=True, active=True)
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(77, original)
            state_mod._cache.clear()
            restored = load_config(77)
        assert asdict(restored) == asdict(original)

    def test_overwrite_existing(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(88, AudioConfig(voice="nova"))
            state_mod._cache.clear()
            save_config(88, AudioConfig(voice="alloy"))
            state_mod._cache.clear()
            cfg = load_config(88)
        assert cfg.voice == "alloy"
