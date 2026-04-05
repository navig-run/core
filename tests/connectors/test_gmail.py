"""Tests for navig.connectors.gmail — mappers and connector."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.gmail.mappers import (
    gmail_message_list_entry_to_resource,
    gmail_message_to_resource,
)
from navig.connectors.types import ActionType, ResourceType

# ── Mapper tests ─────────────────────────────────────────────────────────


class TestGmailMappers:
    def test_gmail_message_to_resource(self):
        msg = {
            "id": "msg-001",
            "threadId": "thread-001",
            "snippet": "Hello, this is a test email",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Email Subject"},
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "To", "value": "bob@example.com"},
                    {"name": "Date", "value": "Mon, 15 Jan 2024 10:30:00 +0000"},
                ],
            },
            "labelIds": ["INBOX", "UNREAD"],
        }
        resource = gmail_message_to_resource(msg)
        assert resource.id == "msg-001"
        assert resource.source == "gmail"
        assert resource.title == "Test Email Subject"
        assert resource.preview == "Hello, this is a test email"
        assert resource.metadata["from"] == "alice@example.com"
        assert resource.metadata["to"] == "bob@example.com"
        assert "INBOX" in resource.metadata["labels"]

    def test_gmail_message_to_resource_missing_headers(self):
        msg = {
            "id": "msg-002",
            "snippet": "No headers",
            "payload": {"headers": []},
        }
        resource = gmail_message_to_resource(msg)
        assert resource.id == "msg-002"
        assert resource.title == "(no subject)"

    def test_gmail_list_entry_to_resource(self):
        entry = {"id": "msg-003", "threadId": "thread-003"}
        resource = gmail_message_list_entry_to_resource(entry)
        assert resource.id == "msg-003"
        assert resource.source == "gmail"
        assert resource.metadata["thread_id"] == "thread-003"


# ── Connector tests (mocked HTTP) ────────────────────────────────────────


def _has_httpx() -> bool:
    try:
        import httpx  # noqa: F401

        return True
    except ImportError:
        return False


class TestGmailConnector:
    @pytest.fixture
    def connector(self):
        from navig.connectors.gmail.connector import GmailConnector

        c = GmailConnector()
        c.set_access_token("fake-token-123")
        return c

    def test_manifest(self, connector):
        assert connector.manifest.id == "gmail"
        assert connector.manifest.requires_oauth is True
        assert connector.manifest.domain.value == "communication"

    def test_headers(self, connector):
        headers = connector._headers()
        assert headers["Authorization"] == "Bearer fake-token-123"

    @pytest.mark.skipif(not _has_httpx(), reason="httpx not installed")
    def test_search_returns_resources(self, connector):
        """Test search with mocked httpx responses."""
        # Mock the _api_get to avoid real HTTP
        list_response = {
            "messages": [{"id": "m1"}, {"id": "m2"}],
        }
        detail_response = {
            "id": "m1",
            "snippet": "Test snippet",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Meeting"},
                    {"name": "From", "value": "a@b.com"},
                    {"name": "Date", "value": "Mon, 15 Jan 2024 10:00:00 +0000"},
                ],
            },
            "labelIds": ["INBOX"],
        }

        async def mock_get(path, params=None):
            if "/messages/" in path and path.count("/") > 3:
                return detail_response
            return list_response

        connector._api_get = mock_get
        results = asyncio.run(connector.search("meeting"))
        assert len(results) >= 1
        assert results[0].source == "gmail"

    @pytest.mark.skipif(not _has_httpx(), reason="httpx not installed")
    def test_search_honours_limit(self, connector):
        """search(limit=N) must request and return at most N results."""
        list_response = {"messages": [{"id": f"m{i}"} for i in range(10)]}
        detail_response = {
            "id": "m0",
            "snippet": "snippet",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Limit Test"},
                    {"name": "From", "value": "x@y.com"},
                    {"name": "Date", "value": "Mon, 15 Jan 2024 10:00:00 +0000"},
                ],
            },
            "labelIds": ["INBOX"],
        }
        requested_max_results = []

        async def mock_get(path, params=None):
            if params and "maxResults" in params:
                requested_max_results.append(params["maxResults"])
                # Return as many IDs as requested (simulates a real API response)
                return {"messages": [{"id": f"m{i}"} for i in range(params["maxResults"])]}
            return detail_response

        connector._api_get = mock_get
        results = asyncio.run(connector.search("limit-test", limit=3))
        # maxResults was passed with the right value
        assert requested_max_results and requested_max_results[0] == 3
        # Result set is capped at the requested limit
        assert len(results) <= 3

    @pytest.mark.skipif(not _has_httpx(), reason="httpx not installed")
    def test_health_check(self, connector):
        """Test health check with mocked response."""
        profile_resp = {"emailAddress": "user@gmail.com"}

        async def mock_get(path, params=None):
            return profile_resp

        connector._api_get = mock_get
        health = asyncio.run(connector.health_check())
        assert health.ok is True
        assert connector._user_email == "user@gmail.com"

    def test_extract_body_plain(self, connector):
        import base64

        encoded = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
        body = connector._extract_body(payload)
        assert body == "Hello world"

    def test_extract_body_multipart(self, connector):
        import base64

        encoded = base64.urlsafe_b64encode(b"Body text").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": encoded},
                }
            ],
        }
        body = connector._extract_body(payload)
        assert body == "Body text"
