"""Tests for navig.gateway.channels.audio_menu.state — AudioConfig, load_config, save_config."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.gateway.channels.audio_menu.state as state_mod
from navig.gateway.channels.audio_menu.state import (
    AudioConfig,
    load_config,
    save_config,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache before each test to avoid state leakage."""
    state_mod._cache.clear()
    yield
    state_mod._cache.clear()


# ---------------------------------------------------------------------------
# AudioConfig defaults
# ---------------------------------------------------------------------------

class TestAudioConfigDefaults:
    def test_default_provider(self):
        c = AudioConfig()
        assert c.provider == "openai"

    def test_default_model(self):
        c = AudioConfig()
        assert c.model == "tts-1-hd"

    def test_default_voice(self):
        c = AudioConfig()
        assert c.voice == "nova"

    def test_default_speed(self):
        c = AudioConfig()
        assert c.speed == 1.0

    def test_default_format(self):
        c = AudioConfig()
        assert c.format == "mp3"

    def test_default_auto(self):
        c = AudioConfig()
        assert c.auto is False

    def test_default_active(self):
        c = AudioConfig()
        assert c.active is False

    def test_custom_values(self):
        c = AudioConfig(provider="edge", voice="alloy", speed=1.5)
        assert c.provider == "edge"
        assert c.voice == "alloy"
        assert c.speed == 1.5


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_returns_default_when_no_file(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(9001)
        assert isinstance(cfg, AudioConfig)
        assert cfg.provider == "openai"

    def test_returns_saved_config(self, tmp_path):
        data = {"provider": "edge", "model": "edge-neural", "voice": "en-US-AriaNeural",
                "speed": 1.0, "format": "mp3", "auto": False, "active": True}
        (tmp_path / "9002.json").write_text(json.dumps(data))
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(9002)
        assert cfg.provider == "edge"
        assert cfg.active is True

    def test_ignores_unknown_keys(self, tmp_path):
        data = {"provider": "openai", "unknown_key": "ignored"}
        (tmp_path / "9003.json").write_text(json.dumps(data))
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(9003)
        assert cfg.provider == "openai"
        assert not hasattr(cfg, "unknown_key")

    def test_falls_back_on_corrupt_json(self, tmp_path):
        (tmp_path / "9004.json").write_text("not valid json {{")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg = load_config(9004)
        # Should return defaults
        assert cfg.provider == "openai"

    def test_cached_on_second_call(self, tmp_path):
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            cfg1 = load_config(9005)
            cfg2 = load_config(9005)
        assert cfg1 is cfg2

    def test_creates_store_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "new_store"
        with patch.object(state_mod, "_STORE_DIR", new_dir):
            load_config(9006)
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

class TestSaveConfig:
    def test_saves_to_file(self, tmp_path):
        cfg = AudioConfig(provider="deepgram", voice="aura-asteria-en")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(9010, cfg)
        saved_path = tmp_path / "9010.json"
        assert saved_path.exists()

    def test_saved_content_is_valid_json(self, tmp_path):
        cfg = AudioConfig()
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(9011, cfg)
        content = json.loads((tmp_path / "9011.json").read_text())
        assert "provider" in content

    def test_saved_provider_matches(self, tmp_path):
        cfg = AudioConfig(provider="google_cloud")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(9012, cfg)
        content = json.loads((tmp_path / "9012.json").read_text())
        assert content["provider"] == "google_cloud"

    def test_updates_cache(self, tmp_path):
        cfg = AudioConfig(voice="echo")
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(9013, cfg)
        assert state_mod._cache.get(9013) is cfg

    def test_load_after_save_returns_saved(self, tmp_path):
        cfg = AudioConfig(provider="edge", speed=1.5)
        with patch.object(state_mod, "_STORE_DIR", tmp_path):
            save_config(9014, cfg)
            state_mod._cache.clear()  # clear so we force a disk read
            loaded = load_config(9014)
        assert loaded.provider == "edge"
        assert loaded.speed == 1.5
