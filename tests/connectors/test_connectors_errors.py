"""Tests for navig.connectors.errors — exception hierarchy."""
from __future__ import annotations

import pytest

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
        e = ConnectorError("my-connector", "test error")
        assert isinstance(e, Exception)

    def test_str_includes_connector_id(self):
        e = ConnectorError("gmail", "auth failed")
        assert "gmail" in str(e)
        assert "auth failed" in str(e)

    def test_connector_id_attribute(self):
        e = ConnectorError("slack", "oops")
        assert e.connector_id == "slack"


class TestConnectorAuthError:
    def test_is_connector_error(self):
        e = ConnectorAuthError("calendar", "token expired")
        assert isinstance(e, ConnectorError)

    def test_message_formatted(self):
        e = ConnectorAuthError("calendar", "token expired")
        assert "calendar" in str(e)
        assert "token expired" in str(e)


class TestConnectorNotFoundError:
    def test_default_message(self):
        e = ConnectorNotFoundError("missing-id")
        assert "missing-id" in str(e)
        assert "not registered" in str(e)

    def test_custom_message(self):
        e = ConnectorNotFoundError("x", "custom msg")
        assert "custom msg" in str(e)

    def test_is_connector_error(self):
        assert isinstance(ConnectorNotFoundError("x"), ConnectorError)


class TestConnectorDegradedError:
    def test_default_message(self):
        e = ConnectorDegradedError("api")
        assert "degraded" in str(e).lower()

    def test_custom_message(self):
        e = ConnectorDegradedError("api", "breaker is open")
        assert "breaker is open" in str(e)


class TestConnectorAPIError:
    def test_attributes(self):
        e = ConnectorAPIError("rest", 404, "not found")
        assert e.status_code == 404
        assert e.detail == "not found"

    def test_message_includes_status_code(self):
        e = ConnectorAPIError("rest", 500)
        assert "500" in str(e)

    def test_message_includes_detail(self):
        e = ConnectorAPIError("rest", 400, "bad request")
        assert "bad request" in str(e)

    def test_no_detail(self):
        e = ConnectorAPIError("rest", 503)
        assert "503" in str(e)
        assert e.detail == ""


class TestConnectorRateLimitError:
    def test_is_api_error(self):
        e = ConnectorRateLimitError("api")
        assert isinstance(e, ConnectorAPIError)

    def test_status_code_is_429(self):
        e = ConnectorRateLimitError("api")
        assert e.status_code == 429

    def test_retry_after_attribute(self):
        e = ConnectorRateLimitError("api", retry_after=30.0)
        assert e.retry_after == 30.0
        assert "30" in str(e)

    def test_no_retry_after(self):
        e = ConnectorRateLimitError("api", retry_after=None)
        assert e.retry_after is None
