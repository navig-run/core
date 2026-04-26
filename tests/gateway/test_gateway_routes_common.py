"""Tests for navig.gateway.routes.common — envelope_ok, envelope_error."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.gateway.routes.common import envelope_error, envelope_ok


class TestEnvelopeOk:
    def test_ok_true(self):
        r = envelope_ok()
        assert r["ok"] is True

    def test_no_data_default_none(self):
        r = envelope_ok()
        assert r["data"] is None

    def test_error_field_is_none(self):
        r = envelope_ok()
        assert r["error"] is None

    def test_data_passed_through(self):
        r = envelope_ok({"key": "value"})
        assert r["data"] == {"key": "value"}

    def test_simple_scalar_data(self):
        r = envelope_ok(42)
        assert r["data"] == 42


class TestEnvelopeError:
    def test_ok_false(self):
        r = envelope_error("Something failed", code="NOT_FOUND")
        assert r["ok"] is False

    def test_error_message(self):
        r = envelope_error("Bad request", code="BAD_REQUEST")
        assert r["error"] == "Bad request"

    def test_error_code(self):
        r = envelope_error("msg", code="INTERNAL_ERROR")
        assert r["error_code"] == "INTERNAL_ERROR"

    def test_data_is_none(self):
        r = envelope_error("msg", code="err")
        assert r["data"] is None

    def test_details_included_when_given(self):
        r = envelope_error("msg", code="err", details={"field": "x"})
        assert r["details"] == {"field": "x"}

    def test_details_not_in_response_when_none(self):
        r = envelope_error("msg", code="err")
        assert "details" not in r


class TestRequireBearerAuth:
    def test_no_token_configured_returns_none(self):
        from navig.gateway.routes.common import require_bearer_auth
        with patch("navig.gateway.routes.common._get_web"):
            gateway = MagicMock()
            gateway.config.auth_token = None
            request = MagicMock()
            result = require_bearer_auth(request, gateway)
        assert result is None

    def test_valid_token_returns_none(self):
        from navig.gateway.routes.common import require_bearer_auth
        mock_web = MagicMock()
        mock_web.json_response.return_value = MagicMock()
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = "secret"
            request = MagicMock()
            request.headers.get.return_value = "Bearer secret"
            result = require_bearer_auth(request, gateway)
        assert result is None

    def test_missing_bearer_returns_401(self):
        from navig.gateway.routes.common import require_bearer_auth
        mock_web = MagicMock()
        mock_web.json_response.return_value = "401_response"
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = "secret"
            request = MagicMock()
            request.headers.get.return_value = ""
            result = require_bearer_auth(request, gateway)
        assert result == "401_response"
