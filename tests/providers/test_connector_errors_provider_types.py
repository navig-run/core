"""Tests for connectors/errors.py and providers/types.py."""
from __future__ import annotations

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# connectors/errors.py — exception hierarchy
# ──────────────────────────────────────────────────────────────────────────────
from navig.connectors.errors import (
    ConnectorAPIError,
    ConnectorAuthError,
    ConnectorDegradedError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
)


class TestConnectorError:
    def test_is_exception(self):
        err = ConnectorError("svc", "something failed")
        assert isinstance(err, Exception)

    def test_connector_id_stored(self):
        err = ConnectorError("my-svc", "oops")
        assert err.connector_id == "my-svc"

    def test_message_contains_connector_id(self):
        err = ConnectorError("my-svc", "oops")
        assert "my-svc" in str(err)

    def test_message_contains_description(self):
        err = ConnectorError("svc", "network timeout")
        assert "network timeout" in str(err)


class TestConnectorAuthError:
    def test_inherits_connector_error(self):
        err = ConnectorAuthError("oauth-svc", "token expired")
        assert isinstance(err, ConnectorError)

    def test_connector_id(self):
        err = ConnectorAuthError("auth-svc", "invalid token")
        assert err.connector_id == "auth-svc"


class TestConnectorNotFoundError:
    def test_inherits_connector_error(self):
        err = ConnectorNotFoundError("missing-svc")
        assert isinstance(err, ConnectorError)

    def test_default_message_contains_name(self):
        err = ConnectorNotFoundError("my-connector")
        assert "my-connector" in str(err)

    def test_custom_message(self):
        err = ConnectorNotFoundError("svc", message="custom msg")
        assert "custom msg" in str(err)


class TestConnectorDegradedError:
    def test_inherits_connector_error(self):
        err = ConnectorDegradedError("svc")
        assert isinstance(err, ConnectorError)

    def test_default_message_mentions_circuit_breaker(self):
        err = ConnectorDegradedError("svc")
        assert "circuit" in str(err).lower() or "degraded" in str(err).lower()

    def test_custom_message(self):
        err = ConnectorDegradedError("svc", message="custom degraded")
        assert "custom degraded" in str(err)


class TestConnectorAPIError:
    def test_inherits_connector_error(self):
        err = ConnectorAPIError("api-svc", 500)
        assert isinstance(err, ConnectorError)

    def test_status_code_stored(self):
        err = ConnectorAPIError("api-svc", 404)
        assert err.status_code == 404

    def test_message_contains_status(self):
        err = ConnectorAPIError("api-svc", 503)
        assert "503" in str(err)

    def test_detail_included(self):
        err = ConnectorAPIError("api-svc", 400, detail="Bad request")
        assert "Bad request" in str(err)

    def test_detail_default_empty(self):
        err = ConnectorAPIError("api-svc", 500)
        assert err.detail == ""


class TestConnectorRateLimitError:
    def test_inherits_api_error(self):
        err = ConnectorRateLimitError("rate-svc")
        assert isinstance(err, ConnectorAPIError)

    def test_status_code_is_429(self):
        err = ConnectorRateLimitError("rate-svc")
        assert err.status_code == 429

    def test_retry_after_stored(self):
        err = ConnectorRateLimitError("rate-svc", retry_after=60.0)
        assert err.retry_after == 60.0

    def test_retry_after_none_default(self):
        err = ConnectorRateLimitError("rate-svc")
        assert err.retry_after is None

    def test_catchable_as_connector_error(self):
        with pytest.raises(ConnectorError):
            raise ConnectorRateLimitError("svc")


# ──────────────────────────────────────────────────────────────────────────────
# providers/types.py — enums and dataclasses
# ──────────────────────────────────────────────────────────────────────────────
from navig.providers.types import (
    AuthMode,
    ModelApi,
    ModelCompatConfig,
    ModelCost,
    ModelDefinition,
    ModelInput,
    ProviderConfig,
    _COMPACT_MAX_OUTPUT_TOKENS,
    _LARGE_MAX_OUTPUT_TOKENS,
    _STANDARD_MAX_OUTPUT_TOKENS,
)


class TestModelApiEnum:
    def test_all_values(self):
        names = {e.name for e in ModelApi}
        assert "OPENAI_COMPLETIONS" in names
        assert "ANTHROPIC_MESSAGES" in names

    def test_inherits_str(self):
        assert isinstance(ModelApi.OPENAI_COMPLETIONS, str)


class TestAuthModeEnum:
    def test_api_key(self):
        assert AuthMode.API_KEY == "api-key"

    def test_oauth(self):
        assert AuthMode.OAUTH == "oauth"

    def test_token(self):
        assert AuthMode.TOKEN == "token"


class TestModelInputEnum:
    def test_text_and_image(self):
        names = {e.name for e in ModelInput}
        assert "TEXT" in names
        assert "IMAGE" in names


class TestTokenConstants:
    def test_standard_positive(self):
        assert _STANDARD_MAX_OUTPUT_TOKENS > 0

    def test_compact_positive(self):
        assert _COMPACT_MAX_OUTPUT_TOKENS > 0

    def test_large_positive(self):
        assert _LARGE_MAX_OUTPUT_TOKENS > 0

    def test_ordering(self):
        assert _COMPACT_MAX_OUTPUT_TOKENS <= _STANDARD_MAX_OUTPUT_TOKENS <= _LARGE_MAX_OUTPUT_TOKENS


class TestModelCost:
    def test_defaults_zero(self):
        c = ModelCost()
        assert c.input == 0.0
        assert c.output == 0.0

    def test_to_dict_keys(self):
        d = ModelCost(input=1.0, output=2.0).to_dict()
        assert "input" in d
        assert "output" in d
        assert "cacheRead" in d
        assert "cacheWrite" in d

    def test_to_dict_values(self):
        d = ModelCost(input=5.0, output=10.0).to_dict()
        assert d["input"] == 5.0
        assert d["output"] == 10.0


class TestModelCompatConfig:
    def test_defaults(self):
        c = ModelCompatConfig()
        assert c.supports_developer_role is True
        assert c.supports_store is False
        assert c.supports_reasoning_effort is False

    def test_max_tokens_field_default(self):
        c = ModelCompatConfig()
        assert c.max_tokens_field == "max_tokens"


class TestModelDefinition:
    def _make(self, **kwargs):
        defaults = dict(id="gpt-4o", name="GPT-4o", api=ModelApi.OPENAI_COMPLETIONS)
        defaults.update(kwargs)
        return ModelDefinition(**defaults)

    def test_creation(self):
        m = self._make()
        assert m.id == "gpt-4o"
        assert m.name == "GPT-4o"

    def test_default_input_is_text(self):
        m = self._make()
        assert ModelInput.TEXT in m.input

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for key in ("id", "name", "api", "reasoning", "input", "cost", "contextWindow", "maxTokens"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_api_is_string(self):
        d = self._make().to_dict()
        assert isinstance(d["api"], str)

    def test_reasoning_default_false(self):
        m = self._make()
        assert m.reasoning is False

    def test_context_window_default(self):
        m = self._make()
        assert m.context_window > 0


class TestProviderConfig:
    def _make(self, **kwargs):
        defaults = dict(name="openai", base_url="https://api.openai.com/v1")
        defaults.update(kwargs)
        return ProviderConfig(**defaults)

    def test_creation(self):
        p = self._make()
        assert p.name == "openai"

    def test_enabled_default(self):
        assert self._make().enabled is True

    def test_priority_default(self):
        assert self._make().priority == 100

    def test_auth_default(self):
        assert self._make().auth == AuthMode.API_KEY

    def test_to_dict_has_name(self):
        d = self._make().to_dict()
        assert d.get("name") == "openai"
