"""Unit tests for navig.vault.validators — all HTTP calls mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.vault.types import Credential, TestResult
from navig.vault.validators import (
    AnthropicValidator,
    GitHubValidator,
    GitLabValidator,
    GroqValidator,
    OpenAIValidator,
    OpenRouterValidator,
    _VALIDATOR_DEFAULT_TIMEOUT,
    _VALIDATOR_EXTENDED_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cred(data: dict | None = None, metadata: dict | None = None) -> Credential:
    return Credential(data=data or {}, metadata=metadata or {})


def _mock_response(status: int, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

class TestValidatorConstants:
    def test_default_timeout_is_int(self):
        assert isinstance(_VALIDATOR_DEFAULT_TIMEOUT, int)

    def test_extended_timeout_is_int(self):
        assert isinstance(_VALIDATOR_EXTENDED_TIMEOUT, int)

    def test_default_timeout_positive(self):
        assert _VALIDATOR_DEFAULT_TIMEOUT > 0

    def test_extended_timeout_gte_default(self):
        assert _VALIDATOR_EXTENDED_TIMEOUT >= _VALIDATOR_DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# OpenAIValidator
# ---------------------------------------------------------------------------

class TestOpenAIValidator:
    _v = OpenAIValidator()

    def test_empty_key_returns_failure(self):
        result = self._v.validate(_cred({}))
        assert result.success is False
        assert "empty" in result.message.lower()

    def test_200_returns_success(self):
        resp = _mock_response(200, {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-test"}))
        assert result.success is True
        assert result.details["models_available"] == 2

    def test_401_returns_invalid_key(self):
        resp = _mock_response(401, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-bad"}))
        assert result.success is False
        assert "invalid" in result.message.lower()

    def test_429_returns_rate_limited(self):
        resp = _mock_response(429, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-test"}))
        assert result.success is False
        assert "rate" in result.message.lower()

    def test_500_returns_api_error(self):
        resp = _mock_response(500, {"error": {"message": "Server error"}})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-test"}))
        assert result.success is False
        assert "500" in result.message

    def test_connection_error_caught(self):
        with patch("httpx.get", side_effect=Exception("timeout")):
            result = self._v.validate(_cred({"api_key": "sk-test"}))
        assert result.success is False
        assert "connection" in result.message.lower()

    def test_returns_test_result_type(self):
        resp = _mock_response(200, {"data": []})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-x"}))
        assert isinstance(result, TestResult)


# ---------------------------------------------------------------------------
# AnthropicValidator
# ---------------------------------------------------------------------------

class TestAnthropicValidator:
    _v = AnthropicValidator()

    def test_empty_key_returns_failure(self):
        result = self._v.validate(_cred({}))
        assert result.success is False
        assert "empty" in result.message.lower()

    def test_200_returns_success(self):
        resp = _mock_response(200, {})
        with patch("httpx.post", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-ant-test"}))
        assert result.success is True

    def test_201_returns_success(self):
        resp = _mock_response(201, {})
        with patch("httpx.post", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-ant-test"}))
        assert result.success is True

    def test_401_invalid_key(self):
        resp = _mock_response(401, {})
        with patch("httpx.post", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-ant-bad"}))
        assert result.success is False
        assert "invalid" in result.message.lower()

    def test_429_rate_limited(self):
        resp = _mock_response(429, {})
        with patch("httpx.post", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-ant-test"}))
        assert result.success is False

    def test_connection_error_caught(self):
        with patch("httpx.post", side_effect=OSError("refused")):
            result = self._v.validate(_cred({"api_key": "sk-ant-test"}))
        assert result.success is False
        assert "connection" in result.message.lower()


# ---------------------------------------------------------------------------
# OpenRouterValidator
# ---------------------------------------------------------------------------

class TestOpenRouterValidator:
    _v = OpenRouterValidator()

    def test_empty_key(self):
        result = self._v.validate(_cred({}))
        assert result.success is False

    def test_200_with_data(self):
        payload = {"data": {"usage": 0.05, "limit": 10.0, "is_free_tier": False}}
        resp = _mock_response(200, payload)
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-or-test"}))
        assert result.success is True
        assert "usage_usd" in result.details

    def test_401_invalid(self):
        resp = _mock_response(401, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-or-bad"}))
        assert result.success is False

    def test_other_status(self):
        resp = _mock_response(503, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "sk-or-test"}))
        assert result.success is False
        assert "503" in result.message

    def test_exception_caught(self):
        with patch("httpx.get", side_effect=ConnectionError("net")):
            result = self._v.validate(_cred({"api_key": "sk-or-test"}))
        assert result.success is False


# ---------------------------------------------------------------------------
# GroqValidator
# ---------------------------------------------------------------------------

class TestGroqValidator:
    _v = GroqValidator()

    def test_empty_key(self):
        result = self._v.validate(_cred({}))
        assert result.success is False

    def test_200_success(self):
        resp = _mock_response(200, {"data": [{"id": "llama3"}]})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "gsk_test"}))
        assert result.success is True
        assert result.details["models_available"] == 1

    def test_401_invalid(self):
        resp = _mock_response(401, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "gsk_bad"}))
        assert result.success is False

    def test_error_code_in_message(self):
        resp = _mock_response(502, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "gsk_test"}))
        assert "502" in result.message

    def test_exception_caught(self):
        with patch("httpx.get", side_effect=TimeoutError()):
            result = self._v.validate(_cred({"api_key": "gsk_test"}))
        assert result.success is False


# ---------------------------------------------------------------------------
# GitHubValidator
# ---------------------------------------------------------------------------

class TestGitHubValidator:
    _v = GitHubValidator()

    def test_empty_both_keys(self):
        result = self._v.validate(_cred({}))
        assert result.success is False
        assert "empty" in result.message.lower()

    def test_uses_token_key(self):
        resp = _mock_response(200, {"login": "alice", "name": "Alice", "email": "alice@example.com"})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "ghp_test"}))
        assert result.success is True
        assert result.details["login"] == "alice"

    def test_uses_api_key(self):
        resp = _mock_response(200, {"login": "bob", "name": "Bob", "email": None})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"api_key": "ghp_test"}))
        assert result.success is True

    def test_401_invalid(self):
        resp = _mock_response(401, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "ghp_bad"}))
        assert result.success is False
        assert "expired" in result.message.lower() or "invalid" in result.message.lower()

    def test_403_no_permissions(self):
        resp = _mock_response(403, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "ghp_ok"}))
        assert result.success is False
        assert "permission" in result.message.lower()

    def test_other_status(self):
        resp = _mock_response(500, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "ghp_test"}))
        assert result.success is False
        assert "500" in result.message

    def test_exception_caught(self):
        with patch("httpx.get", side_effect=Exception("net")):
            result = self._v.validate(_cred({"token": "ghp_test"}))
        assert result.success is False


# ---------------------------------------------------------------------------
# GitLabValidator
# ---------------------------------------------------------------------------

class TestGitLabValidator:
    _v = GitLabValidator()

    def test_empty_token(self):
        result = self._v.validate(_cred({}))
        assert result.success is False

    def test_200_success(self):
        resp = _mock_response(200, {"username": "carol", "name": "Carol", "email": "carol@gl.com"})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "glpat-test"}))
        assert result.success is True
        assert result.details["username"] == "carol"

    def test_401_invalid(self):
        resp = _mock_response(401, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "glpat-bad"}))
        assert result.success is False

    def test_custom_base_url_used(self):
        """Ensure metadata base_url is used over default."""
        resp = _mock_response(200, {"username": "dave", "name": "Dave", "email": ""})
        captured = {}
        original = __import__("httpx").get

        def spy_get(url, **kwargs):
            captured["url"] = url
            return resp

        with patch("httpx.get", side_effect=spy_get):
            self._v.validate(_cred(
                {"token": "glpat-test"},
                metadata={"base_url": "https://gitlab.mycompany.com"},
            ))
        assert "gitlab.mycompany.com" in captured.get("url", "")

    def test_other_status(self):
        resp = _mock_response(404, {})
        with patch("httpx.get", return_value=resp):
            result = self._v.validate(_cred({"token": "glpat-test"}))
        assert result.success is False

    def test_exception_caught(self):
        with patch("httpx.get", side_effect=OSError("refused")):
            result = self._v.validate(_cred({"token": "glpat-test"}))
        assert result.success is False
        assert "connection" in result.message.lower()
