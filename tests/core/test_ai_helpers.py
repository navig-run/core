"""Unit tests for navig.ai — pure helpers and key config resolution logic."""

from __future__ import annotations

import os
import warnings
from unittest.mock import patch

import pytest

from navig.ai import _DEFAULT_MODELS, _get_model_preference, _resolve_openrouter_api_key

# ─── _get_model_preference ───────────────────────────────────────────────────


class TestGetModelPreference:
    def test_returns_canonical_ai_model_preference(self):
        cfg = {"ai": {"model_preference": ["openai/gpt-4o", "anthropic/claude-3-5-sonnet"]}}
        result = _get_model_preference(cfg)
        assert result == ["openai/gpt-4o", "anthropic/claude-3-5-sonnet"]

    def test_canonical_is_a_copy(self):
        """Mutations to the returned list must not affect the config."""
        original = ["openai/gpt-4o"]
        cfg = {"ai": {"model_preference": original}}
        result = _get_model_preference(cfg)
        result.append("extra")
        assert original == ["openai/gpt-4o"]

    def test_falls_back_to_legacy_key(self):
        cfg = {"ai_model_preference": ["legacy/model-1"]}
        result = _get_model_preference(cfg)
        assert result == ["legacy/model-1"]

    def test_legacy_key_emits_deprecation_warning(self):
        cfg = {"ai_model_preference": ["legacy/model-1"]}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _get_model_preference(cfg)
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert dep, "Expected a DeprecationWarning for legacy key"
        assert "ai_model_preference" in str(dep[0].message)

    def test_canonical_key_does_not_emit_deprecation_warning(self):
        cfg = {"ai": {"model_preference": ["openai/gpt-4o"]}}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _get_model_preference(cfg)
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert not dep

    def test_empty_config_returns_defaults(self):
        result = _get_model_preference({})
        assert result == list(_DEFAULT_MODELS)

    def test_ai_section_without_model_preference_returns_defaults(self):
        result = _get_model_preference({"ai": {}})
        assert result == list(_DEFAULT_MODELS)

    def test_returns_list_type(self):
        assert isinstance(_get_model_preference({}), list)

    def test_empty_model_preference_list_falls_back_to_defaults(self):
        # An empty list is falsy; implementation falls back to _DEFAULT_MODELS.
        cfg = {"ai": {"model_preference": []}}
        result = _get_model_preference(cfg)
        assert result == list(_DEFAULT_MODELS)

    def test_legacy_key_never_reached_when_canonical_present(self):
        cfg = {
            "ai": {"model_preference": ["canonical/model"]},
            "ai_model_preference": ["legacy/model"],
        }
        result = _get_model_preference(cfg)
        assert result == ["canonical/model"]


# ─── _resolve_openrouter_api_key ─────────────────────────────────────────────


class TestResolveOpenrouterApiKey:
    def test_env_var_takes_precedence(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-secret"}):
            result = _resolve_openrouter_api_key({})
        assert result == "env-secret"

    def test_env_var_with_return_source(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key"}):
            key, source = _resolve_openrouter_api_key({}, return_source=True)
        assert key == "env-key"
        assert source == "env"

    def test_canonical_config_key_used_when_no_env(self):
        cfg = {"ai": {"api_key": "canonical-key"}}
        with patch.dict(os.environ, {}, clear=True):
            # Remove OPENROUTER_API_KEY if present
            os.environ.pop("OPENROUTER_API_KEY", None)
            result = _resolve_openrouter_api_key(cfg)
        assert result == "canonical-key"

    def test_canonical_source_label(self):
        cfg = {"ai": {"api_key": "canonical-key"}}
        os.environ.pop("OPENROUTER_API_KEY", None)
        key, source = _resolve_openrouter_api_key(cfg, return_source=True)
        assert source == "ai.api_key"

    def test_legacy_config_key_fallback(self):
        cfg = {"openrouter_api_key": "legacy-key"}
        os.environ.pop("OPENROUTER_API_KEY", None)
        result = _resolve_openrouter_api_key(cfg)
        assert result == "legacy-key"

    def test_legacy_source_label(self):
        cfg = {"openrouter_api_key": "legacy-key"}
        os.environ.pop("OPENROUTER_API_KEY", None)
        key, source = _resolve_openrouter_api_key(cfg, return_source=True)
        assert source == "openrouter_api_key"

    def test_no_key_returns_empty_string(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        result = _resolve_openrouter_api_key({})
        assert result == ""

    def test_no_key_source_is_none(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        key, source = _resolve_openrouter_api_key({}, return_source=True)
        assert key == ""
        assert source == "none"

    def test_env_var_stripped_of_whitespace(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "  padded-key  "}):
            result = _resolve_openrouter_api_key({})
        assert result == "padded-key"

    def test_env_var_overrides_canonical_config(self):
        cfg = {"ai": {"api_key": "config-key"}}
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-wins"}):
            result = _resolve_openrouter_api_key(cfg)
        assert result == "env-wins"

    def test_canonical_overrides_legacy(self):
        cfg = {
            "ai": {"api_key": "canonical"},
            "openrouter_api_key": "legacy",
        }
        os.environ.pop("OPENROUTER_API_KEY", None)
        result = _resolve_openrouter_api_key(cfg)
        assert result == "canonical"
