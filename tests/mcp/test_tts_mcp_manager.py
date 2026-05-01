"""Tests for voice/tts.py and mcp_manager.py — batch 114."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/voice/tts.py — enums, config, result
# ---------------------------------------------------------------------------

class TestTTSProviderEnum:
    def test_openai_value(self):
        from navig.voice.tts import TTSProvider
        assert TTSProvider.OPENAI.value == "openai"

    def test_elevenlabs_value(self):
        from navig.voice.tts import TTSProvider
        assert TTSProvider.ELEVENLABS.value == "elevenlabs"

    def test_edge_value(self):
        from navig.voice.tts import TTSProvider
        assert TTSProvider.EDGE.value == "edge"

    def test_is_str_enum(self):
        from navig.voice.tts import TTSProvider
        assert isinstance(TTSProvider.OPENAI, str)

    def test_all_members(self):
        from navig.voice.tts import TTSProvider
        names = {p.name for p in TTSProvider}
        assert "OPENAI" in names
        assert "ELEVENLABS" in names
        assert "EDGE" in names


class TestTTSVoiceEnum:
    def test_nova_value(self):
        from navig.voice.tts import TTSVoice
        assert TTSVoice.NOVA.value == "nova"

    def test_alloy_value(self):
        from navig.voice.tts import TTSVoice
        assert TTSVoice.ALLOY.value == "alloy"

    def test_edge_jenny(self):
        from navig.voice.tts import TTSVoice
        assert TTSVoice.EDGE_EN_US_JENNY.value == "en-US-JennyNeural"

    def test_all_values_are_strings(self):
        from navig.voice.tts import TTSVoice
        for v in TTSVoice:
            assert isinstance(v.value, str)


class TestTTSConfig:
    def test_default_provider(self):
        from navig.voice.tts import TTSConfig, TTSProvider
        cfg = TTSConfig()
        assert cfg.provider == TTSProvider.EDGE

    def test_default_speed(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.speed == 1.0

    def test_default_openai_model(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.openai_model == "tts-1"

    def test_default_output_format(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.output_format == "mp3"

    def test_default_sample_rate(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.sample_rate == 24000

    def test_cache_enabled_default(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.cache_enabled is True

    def test_auto_summarize_default(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.auto_summarize is True

    def test_max_text_length(self):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        assert cfg.max_text_length == 4096

    def test_custom_provider(self):
        from navig.voice.tts import TTSConfig, TTSProvider
        cfg = TTSConfig(provider=TTSProvider.OPENAI)
        assert cfg.provider == TTSProvider.OPENAI

    def test_get_cache_dir_with_explicit_dir(self, tmp_path):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig(cache_dir=tmp_path / "tts_cache")
        result = cfg.get_cache_dir()
        assert result == tmp_path / "tts_cache"
        assert result.exists()

    def test_get_cache_dir_default(self, tmp_path):
        from navig.voice.tts import TTSConfig
        cfg = TTSConfig()
        with patch("navig.voice.tts.config_dir", return_value=tmp_path):
            result = cfg.get_cache_dir()
        assert result.exists()


class TestTTSResult:
    def test_success_true_is_truthy(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=True)
        assert bool(result) is True

    def test_failure_is_falsy(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=False)
        assert bool(result) is False

    def test_default_format(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=True)
        assert result.format == "mp3"

    def test_default_sample_rate(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=True)
        assert result.sample_rate == 24000

    def test_voice_compatible_default_false(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=True)
        assert result.voice_compatible is False

    def test_error_default_none(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=True)
        assert result.error is None

    def test_error_can_be_set(self):
        from navig.voice.tts import TTSResult
        result = TTSResult(success=False, error="API key missing")
        assert result.error == "API key missing"

    def test_audio_path_can_be_path(self, tmp_path):
        from navig.voice.tts import TTSResult
        p = tmp_path / "audio.mp3"
        result = TTSResult(success=True, audio_path=p)
        assert result.audio_path == p


# ---------------------------------------------------------------------------
# navig/mcp_manager.py — MCPServer
# ---------------------------------------------------------------------------

class TestMCPServerInit:
    def _make(self, name="test-server", config=None):
        from navig.mcp_manager import MCPServer
        return MCPServer(name=name, config=config or {})

    def test_name_stored(self):
        s = self._make(name="my-server")
        assert s.name == "my-server"

    def test_config_stored(self):
        cfg = {"type": "npm", "enabled": True}
        s = self._make(config=cfg)
        assert s.config == cfg

    def test_process_starts_none(self):
        s = self._make()
        assert s.process is None


class TestMCPServerIsEnabled:
    def _make(self, enabled=True):
        from navig.mcp_manager import MCPServer
        return MCPServer(name="s", config={"enabled": enabled})

    def test_enabled_true(self):
        assert self._make(enabled=True).is_enabled() is True

    def test_enabled_false(self):
        assert self._make(enabled=False).is_enabled() is False

    def test_missing_defaults_false(self):
        from navig.mcp_manager import MCPServer
        s = MCPServer(name="s", config={})
        assert s.is_enabled() is False


class TestMCPServerIsRunning:
    def _make(self):
        from navig.mcp_manager import MCPServer
        return MCPServer(name="s", config={})

    def test_no_process_not_running(self):
        s = self._make()
        assert s.is_running() is False

    def test_running_when_poll_returns_none(self):
        s = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        s.process = mock_proc
        assert s.is_running() is True

    def test_not_running_when_exited(self):
        s = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        s.process = mock_proc
        assert s.is_running() is False
