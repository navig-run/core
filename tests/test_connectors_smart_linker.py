"""Tests for navig.connectors.smart_linker — SmartLinker, _DATE_PATTERN, _build_cross_query."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.smart_linker import SmartLinker, _DATE_PATTERN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resource(
    source: str,
    title: str = "Test Title",
    preview: str = "",
    resource_id: str = "r1",
    metadata: dict | None = None,
) -> "Resource":
    from navig.connectors.types import Resource
    return Resource(
        id=resource_id,
        source=source,
        title=title,
        preview=preview,
        metadata=metadata or {},
    )


def _mock_connector(results: list | None = None) -> MagicMock:
    """Return a fake connector whose search() is an async mock."""
    c = MagicMock()
    c.search = AsyncMock(return_value=results or [])
    return c


def _mock_registry(connectors: dict | None = None) -> MagicMock:
    """Return a fake connector registry."""
    reg = MagicMock()
    connectors = connectors or {}

    def _has(name):
        return name in connectors

    def _get(name):
        return connectors[name]

    class _FakeItem:
        def __init__(self, id_):
            self.manifest = MagicMock(id=id_)

    reg.has.side_effect = _has
    reg.get.side_effect = _get
    reg.list_connected.return_value = [_FakeItem(k) for k in connectors]
    return reg


# ---------------------------------------------------------------------------
# _DATE_PATTERN
# ---------------------------------------------------------------------------

class TestDatePattern:
    def test_matches_iso_date(self):
        assert _DATE_PATTERN.search("Meeting on 2024-03-15")

    def test_matches_today(self):
        assert _DATE_PATTERN.search("Happening today")

    def test_matches_tomorrow(self):
        assert _DATE_PATTERN.search("Due tomorrow")

    def test_matches_next_weekday(self):
        assert _DATE_PATTERN.search("Schedule next Monday")

    def test_matches_month_day(self):
        assert _DATE_PATTERN.search("Join us Jan 15")

    def test_matches_month_day_long(self):
        assert _DATE_PATTERN.search("Deadline December 31")

    def test_no_match_simple_text(self):
        assert not _DATE_PATTERN.search("Hello world")

    def test_no_match_numbers_only(self):
        assert not _DATE_PATTERN.search("version 3 release")

    def test_case_insensitive_month(self):
        assert _DATE_PATTERN.search("deadline JAN 5")

    def test_findall_returns_multiple(self):
        text = "Sync tomorrow and again on 2024-07-04"
        matches = _DATE_PATTERN.findall(text)
        assert len(matches) >= 2


# ---------------------------------------------------------------------------
# SmartLinker.__init__ and basic structure
# ---------------------------------------------------------------------------

class TestSmartLinkerInit:
    def test_init_fetches_registry(self):
        mock_reg = _mock_registry()
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg):
            linker = SmartLinker()
        assert linker._registry is mock_reg

    def test_init_called_once(self):
        mock_reg = _mock_registry()
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg) as mock_fn:
            SmartLinker()
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# SmartLinker.enrich — gmail path
# ---------------------------------------------------------------------------

class TestSmartLinkerEnrichGmail:
    def _linker(self, connectors):
        mock_reg = _mock_registry(connectors)
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg):
            return SmartLinker()

    def test_enrich_gmail_no_calendar_connector_returns_unchanged(self):
        linker = self._linker({})
        email = _make_resource("gmail", title="Hello tomorrow")
        result = asyncio.run(linker.enrich(email))
        assert result is email
        assert "related_events" not in result.metadata

    def test_enrich_gmail_date_match_attaches_events(self):
        from navig.connectors.types import Resource
        event_res = _make_resource("google_calendar", title="Team Sync", resource_id="e1")
        cal_connector = _mock_connector([event_res])
        linker = self._linker({"google_calendar": cal_connector})

        email = _make_resource("gmail", title="Meeting tomorrow", preview="")
        result = asyncio.run(linker.enrich(email))

        assert "related_events" in result.metadata
        assert len(result.metadata["related_events"]) >= 1

    def test_enrich_gmail_no_date_match_uses_sender(self):
        from navig.connectors.types import Resource
        event_res = _make_resource("google_calendar", title="1:1 with Alice", resource_id="e2")
        cal_connector = _mock_connector([event_res])
        linker = self._linker({"google_calendar": cal_connector})

        email = _make_resource("gmail", title="Status update", preview="", metadata={"from": "alice@example.com"})
        result = asyncio.run(linker.enrich(email))
        # Searched by sender as fallback
        cal_connector.search.assert_called()

    def test_enrich_gmail_search_exception_returns_resource(self):
        cal_connector = MagicMock()
        cal_connector.search = AsyncMock(side_effect=RuntimeError("network error"))
        linker = self._linker({"google_calendar": cal_connector})

        email = _make_resource("gmail", title="Meeting tomorrow")
        result = asyncio.run(linker.enrich(email))
        assert result is email  # returned unchanged

    def test_enrich_non_gmail_non_calendar_returns_unchanged(self):
        linker = self._linker({})
        r = _make_resource("slack", title="hello")
        result = asyncio.run(linker.enrich(r))
        assert result is r


# ---------------------------------------------------------------------------
# SmartLinker.enrich — google_calendar path
# ---------------------------------------------------------------------------

class TestSmartLinkerEnrichCalendar:
    def _linker(self, connectors):
        mock_reg = _mock_registry(connectors)
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg):
            return SmartLinker()

    def test_enrich_calendar_no_gmail_returns_unchanged(self):
        linker = self._linker({})
        event = _make_resource("google_calendar", title="Q4 Review")
        result = asyncio.run(linker.enrich(event))
        assert result is event
        assert "related_emails" not in result.metadata

    def test_enrich_calendar_with_title_attaches_emails(self):
        email_res = _make_resource("gmail", title="Re: Q4 Review", resource_id="m1")
        gmail_connector = _mock_connector([email_res])
        linker = self._linker({"gmail": gmail_connector})

        event = _make_resource("google_calendar", title="Q4 Review")
        result = asyncio.run(linker.enrich(event))

        assert "related_emails" in result.metadata

    def test_enrich_calendar_no_title_skips_title_search(self):
        gmail_connector = _mock_connector([])
        linker = self._linker({"gmail": gmail_connector})

        event = _make_resource("google_calendar", title="(no title)")
        asyncio.run(linker.enrich(event))
        # title=(no title) means no title search
        # neither search called for title
        # attendee search also not run (no attendees)
        gmail_connector.search.assert_not_called()

    def test_enrich_calendar_attendee_fallback(self):
        email_res = _make_resource("gmail", title="Re: project", resource_id="m2")
        gmail_connector = _mock_connector([email_res])
        linker = self._linker({"gmail": gmail_connector})

        event = _make_resource(
            "google_calendar",
            title="(no title)",
            metadata={"attendees": ["bob@example.com", "alice@example.com"]},
        )
        result = asyncio.run(linker.enrich(event))
        # Should have searched for attendee
        gmail_connector.search.assert_called()

    def test_enrich_calendar_exception_returns_resource(self):
        gmail_connector = MagicMock()
        gmail_connector.search = AsyncMock(side_effect=RuntimeError("fail"))
        linker = self._linker({"gmail": gmail_connector})

        event = _make_resource("google_calendar", title="Project Kickoff")
        result = asyncio.run(linker.enrich(event))
        assert result is event


# ---------------------------------------------------------------------------
# SmartLinker.find_related
# ---------------------------------------------------------------------------

class TestSmartLinkerFindRelated:
    def _linker(self, connectors):
        mock_reg = _mock_registry(connectors)
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg):
            return SmartLinker()

    def test_find_related_no_connectors(self):
        linker = self._linker({})
        r = _make_resource("gmail", title="test")
        result = asyncio.run(linker.find_related(r))
        assert result == []

    def test_find_related_specific_target_source(self):
        cal_res = _make_resource("google_calendar", title="Event", resource_id="e1")
        cal_connector = _mock_connector([cal_res])
        linker = self._linker({"google_calendar": cal_connector})

        r = _make_resource("gmail", title="Meeting")
        result = asyncio.run(linker.find_related(r, target_source="google_calendar"))
        assert len(result) >= 1

    def test_find_related_caps_at_5_per_connector(self):
        many_results = [_make_resource("google_calendar", title=f"e{i}", resource_id=str(i)) for i in range(10)]
        cal_connector = _mock_connector(many_results)
        linker = self._linker({"google_calendar": cal_connector})

        r = _make_resource("gmail", title="Meeting")
        result = asyncio.run(linker.find_related(r, target_source="google_calendar"))
        assert len(result) <= 5

    def test_find_related_missing_connector_skipped(self):
        linker = self._linker({})
        r = _make_resource("gmail", title="test")
        result = asyncio.run(linker.find_related(r, target_source="nonexistent"))
        assert result == []

    def test_find_related_exception_per_connector_swallowed(self):
        bad_connector = MagicMock()
        bad_connector.search = AsyncMock(side_effect=RuntimeError("oops"))
        linker = self._linker({"google_calendar": bad_connector})

        r = _make_resource("gmail", title="crash test")
        result = asyncio.run(linker.find_related(r, target_source="google_calendar"))
        assert result == []

    def test_find_related_skips_own_source(self):
        email_connector = _mock_connector([])
        linker = self._linker({"gmail": email_connector})

        r = _make_resource("gmail", title="self search")
        # When iterating connected connectors, gmail should be skipped
        asyncio.run(linker.find_related(r))
        # gmail connector.search should NOT be called (same source)
        email_connector.search.assert_not_called()


# ---------------------------------------------------------------------------
# SmartLinker._build_cross_query (static)
# ---------------------------------------------------------------------------

class TestBuildCrossQuery:
    def _linker(self):
        mock_reg = _mock_registry()
        with patch("navig.connectors.smart_linker.get_connector_registry", return_value=mock_reg):
            return SmartLinker()

    def test_gmail_to_calendar_uses_title(self):
        linker = self._linker()
        r = _make_resource("gmail", title="Budget Review")
        q = linker._build_cross_query(r, "google_calendar")
        assert q == "Budget Review"

    def test_calendar_to_gmail_with_attendees(self):
        linker = self._linker()
        r = _make_resource("google_calendar", title="Standup", metadata={"attendees": ["bob@x.com"]})
        q = linker._build_cross_query(r, "gmail")
        assert "bob@x.com" in q

    def test_calendar_to_gmail_no_attendees_uses_title(self):
        linker = self._linker()
        r = _make_resource("google_calendar", title="Sprint Planning")
        q = linker._build_cross_query(r, "gmail")
        assert q == "Sprint Planning"

    def test_unknown_source_fallback_to_title(self):
        linker = self._linker()
        r = _make_resource("slack", title="Slack Thread")
        q = linker._build_cross_query(r, "some_other")
        assert q == "Slack Thread"

    def test_empty_title_returns_empty_string(self):
        linker = self._linker()
        r = _make_resource("gmail", title="")
        q = linker._build_cross_query(r, "google_calendar")
        assert q == ""


# ---------------------------------------------------------------------------
# Resource.to_dict integration (used by smart_linker internally)
# ---------------------------------------------------------------------------

class TestResourceToDict:
    def test_to_dict_basic(self):
        r = _make_resource("gmail", title="Hello", preview="World", resource_id="x1")
        d = r.to_dict()
        assert d["id"] == "x1"
        assert d["source"] == "gmail"
        assert d["title"] == "Hello"
        assert d["preview"] == "World"

    def test_to_dict_metadata_included(self):
        r = _make_resource("gmail", title="t", metadata={"from": "a@b.com"})
        d = r.to_dict()
        assert d["metadata"]["from"] == "a@b.com"
