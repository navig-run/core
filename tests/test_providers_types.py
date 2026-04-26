"""
Batch 35 — navig/providers/types.py

Covers:
  ModelApi: enum values
  AuthMode: enum values
  ModelInput: enum values
  ModelCost: defaults, to_dict()
  ModelCompatConfig: defaults
  ModelDefinition: defaults, to_dict()
  ProviderConfig: defaults, to_dict()
  ProvidersConfig: defaults, to_dict(), providers round-trip
"""

from __future__ import annotations

import pytest

from navig.providers.types import (
    AuthMode,
    ModelApi,
    ModelCompatConfig,
    ModelCost,
    ModelDefinition,
    ModelInput,
    ProviderConfig,
    ProvidersConfig,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestModelApi:
    def test_openai_completions(self):
        assert ModelApi.OPENAI_COMPLETIONS == "openai-completions"

    def test_openai_responses(self):
        assert ModelApi.OPENAI_RESPONSES == "openai-responses"

    def test_anthropic_messages(self):
        assert ModelApi.ANTHROPIC_MESSAGES == "anthropic-messages"

    def test_google_generative_ai(self):
        assert ModelApi.GOOGLE_GENERATIVE_AI == "google-generative-ai"


class TestAuthMode:
    def test_api_key(self):
        assert AuthMode.API_KEY == "api-key"

    def test_oauth(self):
        assert AuthMode.OAUTH == "oauth"

    def test_token(self):
        assert AuthMode.TOKEN == "token"


class TestModelInput:
    def test_text(self):
        assert ModelInput.TEXT == "text"

    def test_image(self):
        assert ModelInput.IMAGE == "image"


# ---------------------------------------------------------------------------
# ModelCost
# ---------------------------------------------------------------------------

class TestModelCost:
    def test_defaults_zero(self):
        c = ModelCost()
        assert c.input == 0.0
        assert c.output == 0.0
        assert c.cache_read == 0.0
        assert c.cache_write == 0.0

    def test_to_dict_keys(self):
        c = ModelCost(input=1.5, output=2.5, cache_read=0.5, cache_write=0.25)
        d = c.to_dict()
        assert d["input"] == 1.5
        assert d["output"] == 2.5
        assert d["cacheRead"] == 0.5
        assert d["cacheWrite"] == 0.25

    def test_to_dict_default_zeros(self):
        d = ModelCost().to_dict()
        assert all(v == 0.0 for v in d.values())


# ---------------------------------------------------------------------------
# ModelCompatConfig
# ---------------------------------------------------------------------------

class TestModelCompatConfig:
    def test_supports_store_default_false(self):
        c = ModelCompatConfig()
        assert c.supports_store is False

    def test_supports_developer_role_default_true(self):
        c = ModelCompatConfig()
        assert c.supports_developer_role is True

    def test_supports_reasoning_effort_default_false(self):
        c = ModelCompatConfig()
        assert c.supports_reasoning_effort is False

    def test_max_tokens_field_default(self):
        c = ModelCompatConfig()
        assert c.max_tokens_field == "max_tokens"


# ---------------------------------------------------------------------------
# ModelDefinition
# ---------------------------------------------------------------------------

class TestModelDefinition:
    def _make(self, **kwargs):
        defaults = dict(id="gpt-4o", name="GPT-4o")
        defaults.update(kwargs)
        return ModelDefinition(**defaults)

    def test_default_reasoning_false(self):
        m = self._make()
        assert m.reasoning is False

    def test_default_input_text_only(self):
        m = self._make()
        assert m.input == [ModelInput.TEXT]

    def test_default_context_window(self):
        m = self._make()
        assert m.context_window == 128000

    def test_to_dict_id_name(self):
        m = self._make()
        d = m.to_dict()
        assert d["id"] == "gpt-4o"
        assert d["name"] == "GPT-4o"

    def test_to_dict_api_none(self):
        m = self._make()
        d = m.to_dict()
        assert d["api"] is None

    def test_to_dict_api_value(self):
        m = self._make(api=ModelApi.ANTHROPIC_MESSAGES)
        d = m.to_dict()
        assert d["api"] == "anthropic-messages"

    def test_to_dict_input_list(self):
        m = self._make(input=[ModelInput.TEXT, ModelInput.IMAGE])
        d = m.to_dict()
        assert "text" in d["input"]
        assert "image" in d["input"]

    def test_to_dict_cost_present(self):
        m = self._make(cost=ModelCost(input=1.0, output=2.0))
        d = m.to_dict()
        assert d["cost"]["input"] == 1.0

    def test_to_dict_headers_empty_default(self):
        m = self._make()
        assert m.to_dict()["headers"] == {}

    def test_to_dict_reasoning(self):
        m = self._make(reasoning=True)
        assert m.to_dict()["reasoning"] is True


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------

class TestProviderConfig:
    def _make(self, **kwargs):
        defaults = dict(name="openai", base_url="https://api.openai.com/v1")
        defaults.update(kwargs)
        return ProviderConfig(**defaults)

    def test_defaults_enabled(self):
        p = self._make()
        assert p.enabled is True

    def test_defaults_priority(self):
        p = self._make()
        assert p.priority == 100

    def test_defaults_auth_mode(self):
        p = self._make()
        assert p.auth == AuthMode.API_KEY

    def test_defaults_api(self):
        p = self._make()
        assert p.api == ModelApi.OPENAI_COMPLETIONS

    def test_to_dict_name_base_url(self):
        p = self._make()
        d = p.to_dict()
        assert d["name"] == "openai"
        assert d["baseUrl"] == "https://api.openai.com/v1"

    def test_to_dict_auth_value(self):
        p = self._make()
        assert p.to_dict()["auth"] == "api-key"

    def test_to_dict_models_empty_default(self):
        p = self._make()
        assert p.to_dict()["models"] == []

    def test_to_dict_with_model(self):
        m = ModelDefinition(id="gpt-4", name="GPT-4")
        p = self._make(models=[m])
        d = p.to_dict()
        assert len(d["models"]) == 1
        assert d["models"][0]["id"] == "gpt-4"

    def test_to_dict_enabled_flag(self):
        p = self._make(enabled=False)
        assert p.to_dict()["enabled"] is False


# ---------------------------------------------------------------------------
# ProvidersConfig
# ---------------------------------------------------------------------------

class TestProvidersConfig:
    def test_defaults(self):
        pc = ProvidersConfig()
        assert pc.mode == "merge"
        assert pc.default_provider is None
        assert pc.default_model is None
        assert pc.providers == {}
        assert pc.fallback_order == []

    def test_to_dict_mode(self):
        pc = ProvidersConfig(mode="replace")
        d = pc.to_dict()
        assert d["mode"] == "replace"

    def test_to_dict_default_provider(self):
        pc = ProvidersConfig(default_provider="openai")
        assert pc.to_dict()["defaultProvider"] == "openai"

    def test_to_dict_empty_providers(self):
        pc = ProvidersConfig()
        assert pc.to_dict()["providers"] == {}

    def test_to_dict_with_provider(self):
        p = ProviderConfig(name="anthropic", base_url="https://api.anthropic.com")
        pc = ProvidersConfig(providers={"anthropic": p})
        d = pc.to_dict()
        assert "anthropic" in d["providers"]
        assert d["providers"]["anthropic"]["name"] == "anthropic"

    def test_to_dict_fallback_order(self):
        pc = ProvidersConfig(fallback_order=["openai", "anthropic"])
        assert pc.to_dict()["fallbackOrder"] == ["openai", "anthropic"]
