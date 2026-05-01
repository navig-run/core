"""
Batch 51 — hermetic unit tests for:
  navig/ui/bars.py                         — _make_bar (pure logic)
  navig/connectors/gmail/mappers.py        — _extract_header, _parse_timestamp, gmail_message_to_resource
  navig/personas/resolver.py               — _find_project_navig_root, resolve_persona, discover_persona_paths
  navig/storage/tx_helpers.py              — begin_immediate, savepoint
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# navig/ui/bars.py — _make_bar
# ---------------------------------------------------------------------------

from navig.ui.bars import _EMPTY_RICH, _EMPTY_SAFE, _FILL_RICH, _FILL_SAFE, _make_bar


class TestMakeBar:
    def test_zero_fill_all_empty(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", False):
            filled, empty = _make_bar(0.0, width=10)
            assert filled == ""
            assert empty == _EMPTY_RICH * 10

    def test_full_fill_no_empty(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", False):
            filled, empty = _make_bar(1.0, width=10)
            assert filled == _FILL_RICH * 10
            assert empty == ""

    def test_half_fill(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", False):
            filled, empty = _make_bar(0.5, width=10)
            assert len(filled) + len(empty) == 10

    def test_safe_mode_uses_ascii(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", True):
            filled, empty = _make_bar(0.5, width=10)
            assert set(filled) <= {_FILL_SAFE}
            assert set(empty) <= {_EMPTY_SAFE}

    def test_clamp_below_zero(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", False):
            filled, empty = _make_bar(-0.5, width=10)
            assert filled == ""

    def test_clamp_above_one(self):
        from unittest.mock import patch

        with patch("navig.ui.bars.SAFE_MODE", False):
            filled, empty = _make_bar(1.5, width=10)
            assert empty == ""

    def test_total_length_equals_width(self):
        from unittest.mock import patch

        for fill in [0.0, 0.25, 0.5, 0.75, 1.0]:
            with patch("navig.ui.bars.SAFE_MODE", False):
                filled, empty = _make_bar(fill, width=20)
                assert len(filled) + len(empty) == 20


# ---------------------------------------------------------------------------
# navig/connectors/gmail/mappers.py — _extract_header, _parse_timestamp, mapper
# ---------------------------------------------------------------------------

from navig.connectors.gmail.mappers import (
    _extract_header,
    _parse_timestamp,
    gmail_message_list_entry_to_resource,
    gmail_message_to_resource,
)
from navig.connectors.types import ResourceType


class TestExtractHeader:
    def _headers(self):
        return [
            {"name": "Subject", "value": "Hello World"},
            {"name": "From", "value": "alice@example.com"},
            {"name": "Date", "value": "Thu, 01 Jan 2024 10:00:00 +0000"},
        ]

    def test_extract_subject(self):
        assert _extract_header(self._headers(), "Subject") == "Hello World"

    def test_extract_from(self):
        assert _extract_header(self._headers(), "From") == "alice@example.com"

    def test_case_insensitive(self):
        assert _extract_header(self._headers(), "subject") == "Hello World"

    def test_missing_header_returns_empty(self):
        assert _extract_header(self._headers(), "X-Custom") == ""

    def test_empty_headers_list(self):
        assert _extract_header([], "Subject") == ""


class TestParseTimestamp:
    def test_valid_rfc2822_date(self):
        result = _parse_timestamp("Thu, 01 Jan 2024 10:00:00 +0000")
        assert "2024" in result
        assert "T" in result

    def test_empty_string_returns_now(self):
        result = _parse_timestamp("")
        assert "T" in result  # ISO 8601

    def test_invalid_date_returns_now(self):
        result = _parse_timestamp("NOT A DATE")
        assert "T" in result

    def test_result_is_iso_format(self):
        result = _parse_timestamp("Thu, 01 Jan 2024 12:00:00 +0000")
        from datetime import datetime
        # Should be parseable
        datetime.fromisoformat(result.replace("Z", "+00:00"))


class TestGmailMessageToResource:
    def _msg(self, msg_id="msg001", snippet="Hello", subject="Test Subject",
             from_addr="a@b.com", to_addr="c@d.com", labels=None):
        return {
            "id": msg_id,
            "snippet": snippet,
            "threadId": "thread001",
            "labelIds": labels or ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "From", "value": from_addr},
                    {"name": "To", "value": to_addr},
                    {"name": "Date", "value": "Thu, 01 Jan 2024 10:00:00 +0000"},
                    {"name": "Message-ID", "value": "<mid001@example.com>"},
                ]
            },
        }

    def test_resource_id(self):
        r = gmail_message_to_resource(self._msg(msg_id="xyz"))
        assert r.id == "xyz"

    def test_resource_source_gmail(self):
        r = gmail_message_to_resource(self._msg())
        assert r.source == "gmail"

    def test_resource_title_is_subject(self):
        r = gmail_message_to_resource(self._msg(subject="My Subject"))
        assert r.title == "My Subject"

    def test_no_subject_fallback(self):
        msg = {"id": "x", "payload": {"headers": []}}
        r = gmail_message_to_resource(msg)
        assert r.title == "(no subject)"

    def test_resource_type_email(self):
        r = gmail_message_to_resource(self._msg())
        assert r.resource_type == ResourceType.EMAIL

    def test_preview_is_snippet(self):
        r = gmail_message_to_resource(self._msg(snippet="Short preview"))
        assert "Short preview" in r.preview

    def test_url_contains_message_id(self):
        r = gmail_message_to_resource(self._msg(msg_id="abc123"))
        assert "abc123" in r.url

    def test_metadata_from_address(self):
        r = gmail_message_to_resource(self._msg(from_addr="sender@example.com"))
        assert r.metadata["from"] == "sender@example.com"

    def test_metadata_labels(self):
        r = gmail_message_to_resource(self._msg(labels=["INBOX", "UNREAD"]))
        assert "INBOX" in r.metadata["labels"]


class TestGmailListEntryToResource:
    def test_id_set(self):
        r = gmail_message_list_entry_to_resource({"id": "list001", "threadId": "t001"})
        assert r.id == "list001"

    def test_thread_id_in_metadata(self):
        r = gmail_message_list_entry_to_resource({"id": "x", "threadId": "th99"})
        assert r.metadata["thread_id"] == "th99"

    def test_resource_type_email(self):
        r = gmail_message_list_entry_to_resource({"id": "x"})
        assert r.resource_type == ResourceType.EMAIL


# ---------------------------------------------------------------------------
# navig/personas/resolver.py — _find_project_navig_root, resolve_persona
# ---------------------------------------------------------------------------

from navig.personas.resolver import (
    _find_project_navig_root,
    discover_persona_paths,
    resolve_persona,
)


class TestFindProjectNavigRoot:
    def test_finds_navig_dir_in_cwd(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        result = _find_project_navig_root(tmp_path)
        assert result == navig_dir

    def test_finds_navig_dir_in_parent(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        result = _find_project_navig_root(subdir)
        assert result == navig_dir

    def test_returns_none_when_no_navig_dir(self, tmp_path):
        # Use a completely fresh isolated path with no .navig ancestor
        isolated = tmp_path / "isolated_fresh_dir_with_no_navig"
        isolated.mkdir()
        # Search from within this fresh dir only — no .navig should be found here
        # NOTE: may still find a .navig if the temp dir tree has one; skip if so
        parent_chain = [isolated, *isolated.parents]
        if any((p / ".navig").is_dir() for p in parent_chain):
            pytest.skip("System has a .navig in the path hierarchy — cannot test absence")
        result = _find_project_navig_root(isolated)
        assert result is None


class TestResolvePersona:
    def test_project_local_persona_found(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        persona_dir = navig_dir / "personas" / "assistant"
        persona_dir.mkdir(parents=True)
        result = resolve_persona("assistant", cwd=tmp_path)
        assert result == persona_dir

    def test_nonexistent_persona_returns_none(self, tmp_path):
        (tmp_path / ".navig").mkdir()
        result = resolve_persona("nonexistent-persona-xyz", cwd=tmp_path)
        assert result is None

    def test_name_is_lowercased(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        persona_dir = navig_dir / "personas" / "mybot"
        persona_dir.mkdir(parents=True)
        result = resolve_persona("MYBOT", cwd=tmp_path)
        assert result is not None


class TestDiscoverPersonaPaths:
    def test_discovers_project_persona(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        persona = navig_dir / "personas" / "agent1"
        persona.mkdir(parents=True)
        all_personas = discover_persona_paths(cwd=tmp_path)
        assert "agent1" in all_personas

    def test_returns_dict(self, tmp_path):
        (tmp_path / ".navig").mkdir()
        result = discover_persona_paths(cwd=tmp_path)
        assert isinstance(result, dict)

    def test_project_overrides_package(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        persona = navig_dir / "personas" / "shared"
        persona.mkdir(parents=True)
        result = discover_persona_paths(cwd=tmp_path)
        if "shared" in result:
            # project path should win
            assert str(tmp_path) in str(result["shared"])


# ---------------------------------------------------------------------------
# navig/storage/tx_helpers.py — begin_immediate, savepoint
# ---------------------------------------------------------------------------

from navig.storage.tx_helpers import begin_immediate, savepoint


def _in_memory_db():
    """Create a fresh in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
    return conn


class TestBeginImmediate:
    def test_commit_on_success(self):
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            c.execute("INSERT INTO items (value) VALUES ('hello')")
        rows = conn.execute("SELECT value FROM items").fetchall()
        assert rows == [("hello",)]

    def test_rollback_on_exception(self):
        conn = _in_memory_db()
        try:
            with begin_immediate(conn) as c:
                c.execute("INSERT INTO items (value) VALUES ('should-rollback')")
                raise ValueError("forced failure")
        except ValueError:
            pass
        rows = conn.execute("SELECT value FROM items").fetchall()
        assert rows == []

    def test_exception_propagates(self):
        conn = _in_memory_db()
        with pytest.raises(ValueError, match="test error"):
            with begin_immediate(conn):
                raise ValueError("test error")

    def test_isolation_level_restored(self):
        conn = _in_memory_db()
        original = conn.isolation_level
        with begin_immediate(conn):
            pass
        assert conn.isolation_level == original

    def test_multiple_inserts_commit_atomically(self):
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            c.execute("INSERT INTO items (value) VALUES ('a')")
            c.execute("INSERT INTO items (value) VALUES ('b')")
        rows = conn.execute("SELECT value FROM items ORDER BY value").fetchall()
        assert rows == [("a",), ("b",)]


class TestSavepoint:
    def test_release_on_success(self):
        conn = _in_memory_db()
        conn.execute("BEGIN")
        with savepoint(conn, "sp1") as c:
            c.execute("INSERT INTO items (value) VALUES ('in-savepoint')")
        conn.execute("COMMIT")
        rows = conn.execute("SELECT value FROM items").fetchall()
        assert rows == [("in-savepoint",)]

    def test_rollback_to_savepoint_on_exception(self):
        conn = _in_memory_db()
        conn.execute("BEGIN")
        try:
            with savepoint(conn, "sp2") as c:
                c.execute("INSERT INTO items (value) VALUES ('rollback-me')")
                raise RuntimeError("savepoint failure")
        except RuntimeError:
            pass
        # Savepoint rolled back; outer tx can still commit (empty)
        conn.execute("COMMIT")
        rows = conn.execute("SELECT value FROM items").fetchall()
        assert rows == []

    def test_exception_propagates_from_savepoint(self):
        conn = _in_memory_db()
        conn.execute("BEGIN")
        with pytest.raises(RuntimeError, match="boom"):
            with savepoint(conn, "sp3"):
                raise RuntimeError("boom")
        conn.execute("ROLLBACK")
