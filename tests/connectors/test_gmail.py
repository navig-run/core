"""Tests for navig.connectors.gmail — mappers and connector."""

from __future__ import annotations

import asyncio

import pytest

from navig.connectors.gmail.mappers import (
    gmail_message_list_entry_to_resource,
    gmail_message_to_resource,
)

pytestmark = pytest.mark.integration

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


# ── Mailroom read/label/draft API ────────────────────────────────────────


class TestGmailMailroomApi:
    @pytest.fixture
    def connector(self):
        from navig.connectors.gmail.connector import GmailConnector

        c = GmailConnector()
        c.set_access_token("fake-token-123")
        return c

    def test_iter_message_ids_paginates_until_limit(self, connector):
        pages = {
            None: {"messages": [{"id": "a", "threadId": "t1"}, {"id": "b", "threadId": "t1"}],
                   "nextPageToken": "p2"},
            "p2": {"messages": [{"id": "c", "threadId": "t2"}], "nextPageToken": "p3"},
            "p3": {"messages": [{"id": "d", "threadId": "t3"}]},
        }
        seen_params = []

        async def mock_get(path, params=None):
            seen_params.append(dict(params or {}))
            return pages[(params or {}).get("pageToken")]

        connector._api_get = mock_get
        entries = asyncio.run(connector.iter_message_ids("in:sent", limit=3))
        assert [e["id"] for e in entries] == ["a", "b", "c"]
        assert seen_params[0]["q"] == "in:sent"

        # No limit → walks every page.
        seen_params.clear()
        entries = asyncio.run(connector.iter_message_ids("in:sent"))
        assert [e["id"] for e in entries] == ["a", "b", "c", "d"]

    def test_get_many_skips_failures_and_keeps_order(self, connector):
        from navig.connectors.errors import ConnectorAPIError

        async def mock_get(path, params=None):
            mid = path.rsplit("/", 1)[-1]
            if mid == "bad":
                raise ConnectorAPIError("gmail", 404, "gone")
            return {"id": mid}

        connector._api_get = mock_get
        out = asyncio.run(connector.get_many(["m1", "bad", "m2"]))
        assert [m["id"] for m in out] == ["m1", "m2"]

    def test_ensure_label_creates_nested_parents(self, connector):
        created = []

        async def mock_get(path, params=None):
            return {"labels": [{"id": "SPAM", "name": "SPAM", "type": "system"},
                               {"id": "L1", "name": "Cybesis"}]}

        async def mock_post(path, json_body=None):
            created.append(json_body["name"])
            return {"id": f"id-{json_body['name']}", "name": json_body["name"]}

        connector._api_get = mock_get
        connector._api_post = mock_post
        assert asyncio.run(connector.ensure_label("SPAM")) == "SPAM"
        assert asyncio.run(connector.ensure_label("cybesis")) == "L1"
        lid = asyncio.run(connector.ensure_label("Cybesis/PirateBay/2026"))
        assert created == ["Cybesis/PirateBay", "Cybesis/PirateBay/2026"]
        assert lid == "id-Cybesis/PirateBay/2026"

    def test_modify_thread_posts_label_changes(self, connector):
        calls = []

        async def mock_post(path, json_body=None):
            calls.append((path, json_body))
            return {}

        connector._api_post = mock_post
        res = asyncio.run(connector.modify_thread("t9", add=["L1"], remove=["UNREAD"]))
        assert res.success
        assert calls == [("/users/me/threads/t9/modify",
                          {"addLabelIds": ["L1"], "removeLabelIds": ["UNREAD"]})]
        assert asyncio.run(connector.modify_thread("", add=["L1"])).success is False

    def test_create_draft_threads_the_reply(self, connector):
        import base64

        calls = []

        async def mock_post(path, json_body=None):
            calls.append((path, json_body))
            return {"id": "d1"}

        connector._api_post = mock_post
        connector._user_email = "me@gmail.com"
        out = asyncio.run(connector.create_draft(
            to="spam@x.com", subject="Re: Offer", body="No.", thread_id="t1",
            in_reply_to="<orig@x.com>",
        ))
        assert out["id"] == "d1"
        path, body = calls[0]
        assert path == "/users/me/drafts"
        assert body["message"]["threadId"] == "t1"
        raw = base64.urlsafe_b64decode(body["message"]["raw"]).decode()
        assert "In-Reply-To: <orig@x.com>" in raw
        assert "References: <orig@x.com>" in raw
        assert "From: me@gmail.com" in raw

    def test_extract_body_html_fallback(self, connector):
        import base64

        html = "<html><style>p{}</style><body><p>Hello&nbsp;<b>world</b></p><br>Bye</body></html>"
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        payload = {"mimeType": "multipart/alternative",
                   "parts": [{"mimeType": "text/html", "body": {"data": encoded}}]}
        assert connector._extract_body(payload) == "Hello world\n\nBye"

    def test_extract_body_prefers_plain_over_html(self, connector):
        import base64

        plain = base64.urlsafe_b64encode(b"plain wins").decode()
        html = base64.urlsafe_b64encode(b"<p>html loses</p>").decode()
        payload = {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/html", "body": {"data": html}},
            {"mimeType": "text/plain", "body": {"data": plain}},
        ]}
        assert connector._extract_body(payload) == "plain wins"

    def test_list_history_raises_api_error_when_stale(self, connector):
        from navig.connectors.errors import ConnectorAPIError

        async def mock_get(path, params=None):
            raise ConnectorAPIError("gmail", 404, "startHistoryId too old")

        connector._api_get = mock_get
        with pytest.raises(ConnectorAPIError) as exc:
            asyncio.run(connector.list_history("123"))
        assert exc.value.status_code == 404

    def test_mapper_exposes_internal_date_and_in_reply_to(self):
        msg = {"id": "m", "threadId": "t", "internalDate": "1700000000000",
               "payload": {"headers": [{"name": "In-Reply-To", "value": "<x@y>"},
                                       {"name": "Cc", "value": "c@c.com"}]}}
        r = gmail_message_to_resource(msg)
        assert r.metadata["internal_date"] == "1700000000000"
        assert r.metadata["in_reply_to"] == "<x@y>"
        assert r.metadata["cc"] == "c@c.com"
