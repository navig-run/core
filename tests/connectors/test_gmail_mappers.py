"""Tests for navig.connectors.gmail.mappers."""
from __future__ import annotations

import re
import pytest

from navig.connectors.gmail.mappers import (
    _extract_header,
    _parse_timestamp,
    gmail_message_list_entry_to_resource,
    gmail_message_to_resource,
)
from navig.connectors.types import ResourceType


# ---------------------------------------------------------------------------
# _extract_header
# ---------------------------------------------------------------------------

class TestExtractHeader:
    def test_returns_matching_header_value(self) -> None:
        headers = [{"name": "Subject", "value": "Hello World"}]
        assert _extract_header(headers, "Subject") == "Hello World"

    def test_case_insensitive_match(self) -> None:
        headers = [{"name": "subject", "value": "Hi"}]
        assert _extract_header(headers, "SUBJECT") == "Hi"

    def test_returns_empty_when_not_found(self) -> None:
        headers = [{"name": "From", "value": "a@b.com"}]
        assert _extract_header(headers, "To") == ""

    def test_returns_empty_on_empty_list(self) -> None:
        assert _extract_header([], "Subject") == ""

    def test_returns_first_matching_header(self) -> None:
        headers = [{"name": "X-Tag", "value": "first"}, {"name": "X-Tag", "value": "second"}]
        assert _extract_header(headers, "X-Tag") == "first"


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_returns_iso_string_on_valid_date(self) -> None:
        result = _parse_timestamp("Mon, 01 Jan 2024 12:00:00 +0000")
        assert "2024" in result
        assert "T" in result

    def test_returns_iso_string_on_empty_input(self) -> None:
        result = _parse_timestamp("")
        # Falls back to now; should still be a valid ISO string
        assert "T" in result or len(result) > 8

    def test_returns_iso_string_on_invalid_date(self) -> None:
        result = _parse_timestamp("not-a-date")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# gmail_message_to_resource
# ---------------------------------------------------------------------------

def _make_msg(
    msg_id="msg1",
    subject="Test Subject",
    from_addr="sender@example.com",
    to_addr="recipient@example.com",
    snippet="Short snippet",
    labels=None,
) -> dict:
    return {
        "id": msg_id,
        "snippet": snippet,
        "threadId": "thread1",
        "labelIds": labels or ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_addr},
                {"name": "To", "value": to_addr},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
                {"name": "Message-ID", "value": "<id@example.com>"},
            ]
        },
    }


class TestGmailMessageToResource:
    def test_id_mapped(self) -> None:
        r = gmail_message_to_resource(_make_msg(msg_id="abc123"))
        assert r.id == "abc123"

    def test_source_is_gmail(self) -> None:
        r = gmail_message_to_resource(_make_msg())
        assert r.source == "gmail"

    def test_title_is_subject(self) -> None:
        r = gmail_message_to_resource(_make_msg(subject="Hello"))
        assert r.title == "Hello"

    def test_default_subject_when_missing(self) -> None:
        msg = {"id": "x", "snippet": "", "payload": {"headers": []}}
        r = gmail_message_to_resource(msg)
        assert r.title == "(no subject)"

    def test_preview_from_snippet(self) -> None:
        r = gmail_message_to_resource(_make_msg(snippet="A short snippet"))
        assert "short snippet" in r.preview

    def test_preview_truncated_at_200(self) -> None:
        long_snippet = "x" * 300
        r = gmail_message_to_resource(_make_msg(snippet=long_snippet))
        assert len(r.preview) <= 200

    def test_url_contains_id(self) -> None:
        r = gmail_message_to_resource(_make_msg(msg_id="abc"))
        assert "abc" in r.url

    def test_resource_type_is_email(self) -> None:
        r = gmail_message_to_resource(_make_msg())
        assert r.resource_type == ResourceType.EMAIL

    def test_metadata_from_address(self) -> None:
        r = gmail_message_to_resource(_make_msg(from_addr="a@b.com"))
        assert r.metadata["from"] == "a@b.com"

    def test_metadata_labels(self) -> None:
        r = gmail_message_to_resource(_make_msg(labels=["INBOX", "STARRED"]))
        assert "INBOX" in r.metadata["labels"]


class TestGmailMessageListEntryToResource:
    def test_id_mapped(self) -> None:
        r = gmail_message_list_entry_to_resource({"id": "e1", "threadId": "t1"})
        assert r.id == "e1"

    def test_source_is_gmail(self) -> None:
        r = gmail_message_list_entry_to_resource({"id": "e1", "threadId": "t1"})
        assert r.source == "gmail"

    def test_thread_id_in_metadata(self) -> None:
        r = gmail_message_list_entry_to_resource({"id": "e1", "threadId": "t99"})
        assert r.metadata["thread_id"] == "t99"
