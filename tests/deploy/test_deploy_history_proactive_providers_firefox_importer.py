"""
Batch 66: hermetic unit tests for
  - navig/deploy/history.py               (DeployHistory.append/read/_trim)
  - navig/agent/proactive/providers.py    (CalendarEvent, EmailMessage, MockCalendar, MockEmail)
  - navig/importers/sources/firefox.py    (FirefoxImporter._resolve_folder_chain, parse)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# navig/deploy/history.py
# ---------------------------------------------------------------------------

class TestDeployHistory:
    def _make(self, tmp_path: Path, keep: int = 50):
        from navig.deploy.history import DeployHistory
        return DeployHistory(tmp_path, keep=keep)

    def test_append_creates_file(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        dh.append({"app": "myapp", "host": "srv1", "status": "ok"})
        path = tmp_path / "deploy_history.jsonl"
        assert path.exists()

    def test_append_writes_valid_json(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        dh.append({"app": "myapp", "status": "ok"})
        lines = (tmp_path / "deploy_history.jsonl").read_text().splitlines()
        assert json.loads(lines[0])["app"] == "myapp"

    def test_read_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        assert dh.read() == []

    def test_read_returns_newest_first(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        dh.append({"idx": 1})
        dh.append({"idx": 2})
        dh.append({"idx": 3})
        entries = dh.read(limit=3)
        assert entries[0]["idx"] == 3
        assert entries[-1]["idx"] == 1

    def test_read_respects_limit(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        for i in range(10):
            dh.append({"idx": i})
        entries = dh.read(limit=3)
        assert len(entries) == 3

    def test_read_filters_by_app(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        dh.append({"app": "alpha", "host": "s1"})
        dh.append({"app": "beta", "host": "s1"})
        dh.append({"app": "alpha", "host": "s2"})
        entries = dh.read(app="alpha")
        assert all(e["app"] == "alpha" for e in entries)
        assert len(entries) == 2

    def test_read_filters_by_host(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        dh.append({"app": "a", "host": "prod"})
        dh.append({"app": "b", "host": "staging"})
        entries = dh.read(host="prod")
        assert len(entries) == 1
        assert entries[0]["host"] == "prod"

    def test_read_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "deploy_history.jsonl"
        path.write_text('{"app": "good"}\nNOT JSON\n{"app": "also good"}\n')
        from navig.deploy.history import DeployHistory
        dh = DeployHistory(tmp_path)
        entries = dh.read(limit=10)
        apps = [e["app"] for e in entries]
        assert "good" in apps or "also good" in apps

    def test_trim_removes_oldest_entries(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path, keep=5)
        for i in range(10):
            dh.append({"idx": i})
        # Read should return at most keep entries
        all_entries = dh.read(limit=100)
        assert len(all_entries) <= 5

    def test_append_multiple_entries(self, tmp_path: Path) -> None:
        dh = self._make(tmp_path)
        for i in range(3):
            dh.append({"idx": i, "status": "ok"})
        entries = dh.read(limit=10)
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# navig/agent/proactive/providers.py
# ---------------------------------------------------------------------------

class TestCalendarEvent:
    def test_basic_fields(self) -> None:
        from navig.agent.proactive.providers import CalendarEvent
        now = datetime.now()
        ev = CalendarEvent(id="1", title="Meeting", start=now, end=now)
        assert ev.id == "1"
        assert ev.title == "Meeting"
        assert ev.location is None
        assert ev.attendees is None

    def test_with_attendees(self) -> None:
        from navig.agent.proactive.providers import CalendarEvent
        now = datetime.now()
        ev = CalendarEvent(
            id="2", title="Sync", start=now, end=now,
            attendees=["a@x.com", "b@x.com"],
        )
        assert len(ev.attendees) == 2


class TestEmailMessage:
    def test_basic_fields(self) -> None:
        from navig.agent.proactive.providers import EmailMessage
        now = datetime.now()
        msg = EmailMessage(id="m1", subject="Hello", sender="x@y.com",
                           snippet="Hi there", received_at=now)
        assert msg.read is False
        assert msg.sender == "x@y.com"


class TestMockCalendar:
    @pytest.mark.asyncio
    async def test_list_events_returns_list(self) -> None:
        from navig.agent.proactive.providers import MockCalendar
        now = datetime.now()
        events = await MockCalendar().list_events(now, now)
        assert isinstance(events, list)
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_list_events_has_calendar_event_type(self) -> None:
        from navig.agent.proactive.providers import MockCalendar, CalendarEvent
        now = datetime.now()
        events = await MockCalendar().list_events(now, now)
        assert isinstance(events[0], CalendarEvent)

    @pytest.mark.asyncio
    async def test_create_event_returns_id(self) -> None:
        from navig.agent.proactive.providers import MockCalendar, CalendarEvent
        now = datetime.now()
        ev = CalendarEvent(id="x", title="Test", start=now, end=now)
        result = await MockCalendar().create_event(ev)
        assert isinstance(result, str) and result


class TestMockEmail:
    @pytest.mark.asyncio
    async def test_list_unread_returns_messages(self) -> None:
        from navig.agent.proactive.providers import MockEmail, EmailMessage
        msgs = await MockEmail().list_unread()
        assert isinstance(msgs, list)
        assert len(msgs) >= 1
        assert isinstance(msgs[0], EmailMessage)

    @pytest.mark.asyncio
    async def test_draft_email_returns_id(self) -> None:
        from navig.agent.proactive.providers import MockEmail
        result = await MockEmail().draft_email(["a@b.com"], "Subject", "Body")
        assert isinstance(result, str) and result


# ---------------------------------------------------------------------------
# navig/importers/sources/firefox.py
# ---------------------------------------------------------------------------

def _make_fake_places_db(path: Path) -> None:
    """Create a minimal moz_bookmarks / moz_places DB for testing."""
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE moz_places (
            id INTEGER PRIMARY KEY,
            url TEXT,
            title TEXT
        )
    """)
    con.execute("""
        CREATE TABLE moz_bookmarks (
            id INTEGER PRIMARY KEY,
            type INTEGER,
            parent INTEGER,
            title TEXT,
            fk INTEGER
        )
    """)
    # Insert a place
    con.execute("INSERT INTO moz_places (id, url, title) VALUES (1, 'https://example.com', 'Example')")
    # Insert a folder (type=2)
    con.execute("INSERT INTO moz_bookmarks (id, type, parent, title, fk) VALUES (1, 2, 0, 'Bookmarks', NULL)")
    # Insert a bookmark (type=1)
    con.execute("INSERT INTO moz_bookmarks (id, type, parent, title, fk) VALUES (2, 1, 1, 'Example', 1)")
    con.commit()
    con.close()


class TestFirefoxImporter:
    def _make(self):
        from navig.importers.sources.firefox import FirefoxImporter
        return FirefoxImporter()

    def test_detect_false_when_no_path(self) -> None:
        importer = self._make()
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=None):
            assert importer.detect() is False

    def test_detect_false_when_path_missing(self, tmp_path: Path) -> None:
        importer = self._make()
        fake_path = str(tmp_path / "missing.sqlite")
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=fake_path):
            assert importer.detect() is False

    def test_detect_true_when_path_exists(self, tmp_path: Path) -> None:
        importer = self._make()
        db = tmp_path / "places.sqlite"
        db.touch()
        with patch("navig.importers.sources.firefox.firefox_places_default_path", return_value=str(db)):
            assert importer.detect() is True

    def test_parse_empty_when_file_missing(self) -> None:
        importer = self._make()
        result = importer.parse("/nonexistent/path.sqlite")
        assert result == []

    def test_parse_returns_bookmarks(self, tmp_path: Path) -> None:
        db = tmp_path / "places.sqlite"
        _make_fake_places_db(db)
        importer = self._make()
        items = importer.parse(str(db))
        assert len(items) == 1
        assert items[0].value == "https://example.com"
        assert items[0].type == "bookmark"

    def test_parse_item_has_source(self, tmp_path: Path) -> None:
        db = tmp_path / "places.sqlite"
        _make_fake_places_db(db)
        importer = self._make()
        items = importer.parse(str(db))
        assert items[0].source == "firefox"

    def test_parse_item_has_folder_in_meta(self, tmp_path: Path) -> None:
        db = tmp_path / "places.sqlite"
        _make_fake_places_db(db)
        importer = self._make()
        items = importer.parse(str(db))
        assert "folder" in items[0].meta

    def test_resolve_folder_chain_empty(self) -> None:
        importer = self._make()
        result = importer._resolve_folder_chain(0, {})
        assert result == ""

    def test_resolve_folder_chain_single(self) -> None:
        importer = self._make()
        folders = {1: {"title": "Bookmarks", "parent": 0}}
        result = importer._resolve_folder_chain(1, folders)
        assert result == "Bookmarks"

    def test_resolve_folder_chain_nested(self) -> None:
        importer = self._make()
        folders = {
            1: {"title": "Root", "parent": 0},
            2: {"title": "Sub", "parent": 1},
        }
        result = importer._resolve_folder_chain(2, folders)
        assert "Root" in result
        assert "Sub" in result

    def test_resolve_folder_chain_cycle_guard(self) -> None:
        importer = self._make()
        # Cycle: 1 -> 2 -> 1
        folders = {
            1: {"title": "A", "parent": 2},
            2: {"title": "B", "parent": 1},
        }
        # Should terminate without infinite loop
        result = importer._resolve_folder_chain(1, folders)
        assert isinstance(result, str)
