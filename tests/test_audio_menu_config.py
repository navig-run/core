"""Tests for navig.gateway.channels.audio_menu.config — PROVIDERS, SPEEDS, FORMATS constants."""

from __future__ import annotations

import pytest

from navig.gateway.channels.audio_menu.config import (
    FORMATS,
    PROVIDERS,
    SPEEDS,
    VOICES_PER_PAGE,
)


# ---------------------------------------------------------------------------
# PROVIDERS structure
# ---------------------------------------------------------------------------

class TestProviders:
    def test_providers_is_dict(self):
        assert isinstance(PROVIDERS, dict)

    def test_openai_provider_exists(self):
        assert "openai" in PROVIDERS

    def test_edge_provider_exists(self):
        assert "edge" in PROVIDERS

    def test_deepgram_provider_exists(self):
        assert "deepgram" in PROVIDERS

    def test_each_provider_has_label(self):
        for provider_id, info in PROVIDERS.items():
            assert "label" in info, f"{provider_id} missing 'label'"
            assert isinstance(info["label"], str)

    def test_each_provider_has_models(self):
        for provider_id, info in PROVIDERS.items():
            assert "models" in info, f"{provider_id} missing 'models'"
            assert isinstance(info["models"], dict)

    def test_openai_has_tts_models(self):
        models = PROVIDERS["openai"]["models"]
        assert "tts-1" in models
        assert "tts-1-hd" in models

    def test_openai_tts1_has_voices(self):
        voices = PROVIDERS["openai"]["models"]["tts-1"]["voices"]
        assert isinstance(voices, list)
        assert len(voices) > 0
        assert "alloy" in voices

    def test_openai_voices_are_strings(self):
        for model_id, model_info in PROVIDERS["openai"]["models"].items():
            for voice in model_info["voices"]:
                assert isinstance(voice, str), f"{model_id} voice not string: {voice}"

    def test_edge_neural_has_multilingual_voices(self):
        voices = PROVIDERS["edge"]["models"]["edge-neural"]["voices"]
        # Should have voices for multiple languages
        prefixes = {v.split("-")[0] for v in voices}
        assert len(prefixes) > 2  # Multiple language codes

    def test_all_model_info_has_label(self):
        for provider_id, provider_info in PROVIDERS.items():
            for model_id, model_info in provider_info["models"].items():
                assert "label" in model_info, f"{provider_id}/{model_id} missing 'label'"

    def test_deepgram_models_are_aura_variants(self):
        models = PROVIDERS["deepgram"]["models"]
        for model_id in models:
            assert "aura" in model_id, f"Unexpected deepgram model: {model_id}"

    def test_no_empty_labels(self):
        for provider_id, provider_info in PROVIDERS.items():
            assert provider_info["label"].strip(), f"{provider_id} label is empty"

    def test_provider_count(self):
        # Should have at least 3 providers
        assert len(PROVIDERS) >= 3


# ---------------------------------------------------------------------------
# SPEEDS
# ---------------------------------------------------------------------------

class TestSpeeds:
    def test_speeds_is_list(self):
        assert isinstance(SPEEDS, list)

    def test_speeds_are_floats(self):
        for speed in SPEEDS:
            assert isinstance(speed, (int, float)), f"Speed {speed} is not numeric"

    def test_normal_speed_included(self):
        assert 1.0 in SPEEDS

    def test_speeds_in_ascending_order(self):
        assert SPEEDS == sorted(SPEEDS)

    def test_speeds_all_positive(self):
        for speed in SPEEDS:
            assert speed > 0, f"Speed {speed} is not positive"

    def test_slow_speed_included(self):
        assert any(s < 1.0 for s in SPEEDS)

    def test_fast_speed_included(self):
        assert any(s > 1.0 for s in SPEEDS)

    def test_speed_count(self):
        assert len(SPEEDS) >= 4


# ---------------------------------------------------------------------------
# FORMATS
# ---------------------------------------------------------------------------

class TestFormats:
    def test_formats_is_list(self):
        assert isinstance(FORMATS, list)

    def test_mp3_included(self):
        assert "mp3" in FORMATS

    def test_formats_are_strings(self):
        for fmt in FORMATS:
            assert isinstance(fmt, str)

    def test_no_empty_formats(self):
        for fmt in FORMATS:
            assert fmt.strip(), f"Empty format found: {fmt!r}"

    def test_formats_all_lowercase(self):
        for fmt in FORMATS:
            assert fmt == fmt.lower(), f"Format not lowercase: {fmt}"

    def test_format_count(self):
        assert len(FORMATS) >= 4


# ---------------------------------------------------------------------------
# VOICES_PER_PAGE
# ---------------------------------------------------------------------------

class TestVoicesPerPage:
    def test_voices_per_page_is_positive_int(self):
        assert isinstance(VOICES_PER_PAGE, int)
        assert VOICES_PER_PAGE > 0

    def test_voices_per_page_reasonable(self):
        # Should be between 4 and 20 for usability
        assert 4 <= VOICES_PER_PAGE <= 20


# ---------------------------------------------------------------------------
# Cross-structural validation
# ---------------------------------------------------------------------------

class TestCrossStructural:
    def test_all_openai_models_have_same_voices(self):
        """All OpenAI models should share the same voice list structure."""
        openai_models = PROVIDERS["openai"]["models"]
        voice_sets = [frozenset(m["voices"]) for m in openai_models.values()]
        # All models should have the same voices
        assert len(set(voice_sets)) == 1, "OpenAI models have inconsistent voice lists"

    def test_edge_voices_follow_bcp47_format(self):
        """Edge voices should follow format: lang-REGION-VoiceName."""
        voices = PROVIDERS["edge"]["models"]["edge-neural"]["voices"]
        for voice in voices:
            parts = voice.split("-")
            assert len(parts) >= 3, f"Voice '{voice}' doesn't match expected format"
