"""
Batch 111: hermetic tests for
  - navig/onboarding/validators.py   (ValidationResult + all validate_* functions)
  - navig/vault/validators.py        (CredentialValidator ABC + provider validators)
"""
from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cred(data: dict):
    from navig.vault.types import Credential, CredentialType
    return Credential(
        id="test-id",
        provider="openai",
        profile_id="default",
        credential_type=CredentialType.API_KEY,
        label="test",
        data=data,
    )


def _mock_response(status: int, json_data: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    return r


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_success_ok_true(self):
        from navig.onboarding.validators import ValidationResult
        r = ValidationResult.success(user_id="@alice:example.com")
        assert r.ok is True
        assert r.errors == []
        assert r.info["user_id"] == "@alice:example.com"

    def test_failure_ok_false(self):
        from navig.onboarding.validators import ValidationResult
        r = ValidationResult.failure("bot_token", "Token is missing")
        assert r.ok is False
        assert len(r.errors) == 1
        assert r.errors[0]["field"] == "bot_token"
        assert "Token is missing" in r.errors[0]["message"]

    def test_timeout_ok_false_with_message(self):
        from navig.onboarding.validators import ValidationResult
        r = ValidationResult.timeout("Telegram API")
        assert r.ok is False
        assert any("Telegram API" in e["message"] for e in r.errors)

    def test_direct_construction(self):
        from navig.onboarding.validators import ValidationResult
        r = ValidationResult(ok=True)
        assert r.errors == []
        assert r.info == {}


# ---------------------------------------------------------------------------
# validate_matrix
# ---------------------------------------------------------------------------

class TestValidateMatrix:
    def _fn(self):
        from navig.onboarding.validators import validate_matrix
        return validate_matrix

    def test_empty_url_returns_failure(self):
        r = self._fn()("", "token123")
        assert r.ok is False
        assert r.errors[0]["field"] == "homeserver_url"

    def test_empty_token_returns_failure(self):
        r = self._fn()("https://matrix.example.com", "")
        assert r.ok is False
        assert r.errors[0]["field"] == "access_token"

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"user_id": "@alice:matrix.org"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://matrix.example.com", "secret")
        assert r.ok is True
        assert r.info["user_id"] == "@alice:matrix.org"

    def test_401_returns_failure(self):
        mock_resp = _mock_response(401, {"error": "Invalid token"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://matrix.example.com", "bad")
        assert r.ok is False

    def test_404_returns_url_failure(self):
        mock_resp = _mock_response(404, {})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://matrix.example.com", "token")
        assert r.ok is False
        assert r.errors[0]["field"] == "homeserver_url"

    def test_network_exception_returns_timeout(self):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(side_effect=Exception("timeout connecting"))
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://matrix.example.com", "tok")
        assert r.ok is False
        assert r.errors[0]["field"] == "connection"


# ---------------------------------------------------------------------------
# validate_telegram
# ---------------------------------------------------------------------------

class TestValidateTelegram:
    def _fn(self):
        from navig.onboarding.validators import validate_telegram
        return validate_telegram

    def test_empty_token_returns_failure(self):
        r = self._fn()("")
        assert r.ok is False
        assert r.errors[0]["field"] == "bot_token"

    def test_ok_true_returns_success(self):
        mock_resp = _mock_response(200, {"ok": True, "result": {"username": "testbot", "first_name": "Test"}})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("123:ABC")
        assert r.ok is True
        assert r.info["username"] == "testbot"

    def test_ok_false_returns_failure(self):
        mock_resp = _mock_response(200, {"ok": False, "description": "Not Found"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("bad:token")
        assert r.ok is False

    def test_network_timeout_returns_timeout_result(self):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(side_effect=Exception("connect timeout"))
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("bad:token")
        assert r.ok is False
        assert r.errors[0]["field"] == "connection"


# ---------------------------------------------------------------------------
# validate_smtp
# ---------------------------------------------------------------------------

class TestValidateSMTP:
    def _fn(self):
        from navig.onboarding.validators import validate_smtp
        return validate_smtp

    def test_empty_host_returns_failure(self):
        r = self._fn()("", 587)
        assert r.ok is False
        assert r.errors[0]["field"] == "smtp_host"

    def test_invalid_port_returns_failure(self):
        r = self._fn()("smtp.example.com", "notaport")
        assert r.ok is False
        assert r.errors[0]["field"] == "smtp_port"

    def test_empty_port_uses_default_587(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.ehlo.return_value = (250, b"ok")
        with patch("smtplib.SMTP", return_value=mock_conn):
            r = self._fn()("smtp.example.com", "")
        assert r.ok is True
        assert r.info["port"] == 587

    def test_success_ehlo_200(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.ehlo.return_value = (250, b"Hello server")
        with patch("smtplib.SMTP", return_value=mock_conn):
            r = self._fn()("smtp.example.com", 587)
        assert r.ok is True
        assert r.info["host"] == "smtp.example.com"

    def test_ehlo_non_2xx_returns_failure(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.ehlo.return_value = (550, b"rejected")
        with patch("smtplib.SMTP", return_value=mock_conn):
            r = self._fn()("smtp.example.com", 587)
        assert r.ok is False

    def test_connection_refused(self):
        with patch("smtplib.SMTP", side_effect=ConnectionRefusedError):
            r = self._fn()("smtp.example.com", 587)
        assert r.ok is False
        assert "refused" in r.errors[0]["message"].lower()

    def test_timeout_returns_timeout_result(self):
        with patch("smtplib.SMTP", side_effect=TimeoutError):
            r = self._fn()("smtp.example.com", 587)
        assert r.ok is False
        assert r.errors[0]["field"] == "connection"

    def test_smtp_exception(self):
        with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("error")):
            r = self._fn()("smtp.example.com", 587)
        assert r.ok is False


# ---------------------------------------------------------------------------
# validate_twitter
# ---------------------------------------------------------------------------

class TestValidateTwitter:
    def _fn(self):
        from navig.onboarding.validators import validate_twitter
        return validate_twitter

    def test_empty_key_returns_failure(self):
        r = self._fn()("", "secret")
        assert r.ok is False
        assert r.errors[0]["field"] == "api_key"

    def test_empty_secret_returns_failure(self):
        r = self._fn()("key", "")
        assert r.ok is False
        assert r.errors[0]["field"] == "api_secret"

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"token_type": "bearer", "access_token": "x"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("mykey", "mysecret")
        assert r.ok is True

    def test_non_200_returns_failure(self):
        mock_resp = _mock_response(403, {"errors": [{"message": "Forbidden"}]})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.post = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("mykey", "mysecret")
        assert r.ok is False


# ---------------------------------------------------------------------------
# validate_linkedin
# ---------------------------------------------------------------------------

class TestValidateLinkedIn:
    def _fn(self):
        from navig.onboarding.validators import validate_linkedin
        return validate_linkedin

    def test_empty_token_returns_failure(self):
        r = self._fn()("")
        assert r.ok is False
        assert r.errors[0]["field"] == "access_token"

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"localizedFirstName": "Alice", "localizedLastName": "Smith"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("mytoken")
        assert r.ok is True
        assert "Alice" in r.info["name"]

    def test_401_returns_failure(self):
        mock_resp = _mock_response(401, {"message": "Unauthorized"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("badtoken")
        assert r.ok is False


# ---------------------------------------------------------------------------
# validate_mastodon
# ---------------------------------------------------------------------------

class TestValidateMastodon:
    def _fn(self):
        from navig.onboarding.validators import validate_mastodon
        return validate_mastodon

    def test_empty_url_returns_failure(self):
        r = self._fn()("", "token")
        assert r.ok is False
        assert r.errors[0]["field"] == "instance_url"

    def test_empty_token_returns_failure(self):
        r = self._fn()("https://mastodon.social", "")
        assert r.ok is False
        assert r.errors[0]["field"] == "access_token"

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"username": "alice", "display_name": "Alice Wonder"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://mastodon.social", "mytoken")
        assert r.ok is True
        assert r.info["username"] == "alice"

    def test_non_200_returns_failure(self):
        mock_resp = _mock_response(403, {"error": "Forbidden"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.get = MagicMock(return_value=mock_resp)
        with patch("navig.onboarding.validators._http_client", return_value=ctx):
            r = self._fn()("https://mastodon.social", "badtoken")
        assert r.ok is False


# ---------------------------------------------------------------------------
# vault/validators.py — CredentialValidator ABC
# ---------------------------------------------------------------------------

class TestCredentialValidatorABC:
    def test_cannot_instantiate_directly(self):
        from navig.vault.validators import CredentialValidator
        import pytest
        with pytest.raises(TypeError):
            CredentialValidator()

    def test_subclass_must_implement_validate(self):
        from navig.vault.validators import CredentialValidator
        import pytest
        class Incomplete(CredentialValidator):
            pass
        with pytest.raises(TypeError):
            Incomplete()


# ---------------------------------------------------------------------------
# OpenAIValidator
# ---------------------------------------------------------------------------

class TestOpenAIValidator:
    def _validator(self):
        from navig.vault.validators import OpenAIValidator
        return OpenAIValidator()

    def test_empty_key_returns_failure(self):
        r = self._validator().validate(_cred({"api_key": ""}))
        assert r.success is False
        assert "empty" in r.message.lower()

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-test"}))
        assert r.success is True
        assert r.details["models_available"] == 2

    def test_401_returns_failure(self):
        mock_resp = _mock_response(401, {"error": {"message": "Incorrect API key"}})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-bad"}))
        assert r.success is False
        assert "invalid" in r.message.lower()

    def test_429_rate_limit_returns_failure(self):
        mock_resp = _mock_response(429, {})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-rate"}))
        assert r.success is False
        assert "rate" in r.message.lower()

    def test_connection_error_returns_failure(self):
        with patch("httpx.get", side_effect=Exception("Network error")):
            r = self._validator().validate(_cred({"api_key": "sk-net"}))
        assert r.success is False
        assert "connection" in r.message.lower()


# ---------------------------------------------------------------------------
# AnthropicValidator
# ---------------------------------------------------------------------------

class TestAnthropicValidator:
    def _validator(self):
        from navig.vault.validators import AnthropicValidator
        return AnthropicValidator()

    def test_empty_key_returns_failure(self):
        r = self._validator().validate(_cred({"api_key": ""}))
        assert r.success is False

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {})
        with patch("httpx.post", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-ant-test"}))
        assert r.success is True

    def test_401_returns_failure(self):
        mock_resp = _mock_response(401, {"error": {"message": "Invalid key"}})
        with patch("httpx.post", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-ant-bad"}))
        assert r.success is False

    def test_connection_error(self):
        with patch("httpx.post", side_effect=Exception("Timeout")):
            r = self._validator().validate(_cred({"api_key": "sk-ant-err"}))
        assert r.success is False


# ---------------------------------------------------------------------------
# OpenRouterValidator
# ---------------------------------------------------------------------------

class TestOpenRouterValidator:
    def _validator(self):
        from navig.vault.validators import OpenRouterValidator
        return OpenRouterValidator()

    def test_empty_key(self):
        r = self._validator().validate(_cred({"api_key": ""}))
        assert r.success is False

    def test_200_returns_success_with_details(self):
        mock_resp = _mock_response(200, {"data": {"usage": 1.23, "limit": 10.0, "is_free_tier": False}})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-or-test"}))
        assert r.success is True
        assert r.details["usage_usd"] == 1.23

    def test_401_returns_failure(self):
        mock_resp = _mock_response(401, {})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "sk-bad"}))
        assert r.success is False


# ---------------------------------------------------------------------------
# GroqValidator
# ---------------------------------------------------------------------------

class TestGroqValidator:
    def _validator(self):
        from navig.vault.validators import GroqValidator
        return GroqValidator()

    def test_empty_key(self):
        r = self._validator().validate(_cred({"api_key": ""}))
        assert r.success is False

    def test_200_returns_success(self):
        mock_resp = _mock_response(200, {"data": [{"id": "llama3-8b"}]})
        with patch("httpx.get", return_value=mock_resp):
            r = self._validator().validate(_cred({"api_key": "gsk_test"}))
        assert r.success is True
