"""Tests for navig.connectors.errors — Exception hierarchy."""

from __future__ import annotations

from navig.connectors.errors import (
    ConnectorAPIError,
    ConnectorAuthError,
    ConnectorDegradedError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
)


class TestConnectorErrors:
    def test_base_error(self):
        e = ConnectorError("gmail", "something broke")
        assert e.connector_id == "gmail"
        assert "something broke" in str(e)

    def test_auth_error_is_connector_error(self):
        e = ConnectorAuthError("gmail", "token expired")
        assert isinstance(e, ConnectorError)
        assert e.connector_id == "gmail"

    def test_not_found_error(self):
        e = ConnectorNotFoundError("unknown", "not registered")
        assert isinstance(e, ConnectorError)

    def test_degraded_error(self):
        e = ConnectorDegradedError("gmail", "circuit open")
        assert isinstance(e, ConnectorError)

    def test_api_error_has_status(self):
        e = ConnectorAPIError("gmail", 403, "forbidden")
        assert isinstance(e, ConnectorError)
        assert e.status_code == 403
        assert e.detail == "forbidden"
        assert "403" in str(e)

    def test_rate_limit_error(self):
        e = ConnectorRateLimitError("gmail", retry_after=30)
        assert isinstance(e, ConnectorError)
        assert e.retry_after == 30
        assert "rate limit" in str(e).lower() or "retry" in str(e).lower()
