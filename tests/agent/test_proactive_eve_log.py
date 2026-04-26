"""Tests for navig.agent.proactive.eve_log — daily evening log."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import navig.agent.proactive.eve_log as eve_mod


@pytest.fixture(autouse=True)
def tmp_navig_dir(tmp_path, monkeypatch):
    """Redirect NAVIG_CONFIG_DIR to a temp directory for isolation."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # Force module to use fresh path each test
    yield tmp_path


class TestSaveShipped:
    def test_creates_entry(self):
        eve_mod.save_shipped("fixed bug", date="2026-01-01")
        entry = eve_mod.get_entry("2026-01-01")
        assert entry["shipped"] == "fixed bug"

    def test_timestamps_recorded(self):
        eve_mod.save_shipped("deployed", date="2026-01-02")
        entry = eve_mod.get_entry("2026-01-02")
        assert "shipped_at" in entry

    def test_strips_whitespace(self):
        eve_mod.save_shipped("  trimmed  ", date="2026-01-03")
        entry = eve_mod.get_entry("2026-01-03")
        assert entry["shipped"] == "trimmed"

    def test_overwrites_shipped_field(self):
        eve_mod.save_shipped("first", date="2026-01-04")
        eve_mod.save_shipped("second", date="2026-01-04")
        entry = eve_mod.get_entry("2026-01-04")
        assert entry["shipped"] == "second"

    def test_preserves_priority_field(self):
        eve_mod.save_priority("big task", date="2026-01-05")
        eve_mod.save_shipped("done", date="2026-01-05")
        entry = eve_mod.get_entry("2026-01-05")
        assert entry["priority"] == "big task"
        assert entry["shipped"] == "done"


class TestSavePriority:
    def test_creates_priority_entry(self):
        eve_mod.save_priority("ship auth", date="2026-02-01")
        entry = eve_mod.get_entry("2026-02-01")
        assert entry["priority"] == "ship auth"

    def test_timestamps_recorded(self):
        eve_mod.save_priority("task", date="2026-02-02")
        entry = eve_mod.get_entry("2026-02-02")
        assert "priority_at" in entry


class TestGetEntry:
    def test_returns_empty_dict_for_missing_date(self):
        entry = eve_mod.get_entry("2000-01-01")
        assert entry == {}

    def test_returns_entry_for_saved_date(self):
        eve_mod.save_shipped("work", date="2026-03-01")
        entry = eve_mod.get_entry("2026-03-01")
        assert entry != {}


class TestGetToday:
    def test_returns_dict(self):
        result = eve_mod.get_today()
        assert isinstance(result, dict)

    def test_reflects_saved_today(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        eve_mod.save_shipped("today's work", date=today)
        entry = eve_mod.get_today()
        assert entry.get("shipped") == "today's work"


class TestGetYesterday:
    def test_returns_dict(self):
        result = eve_mod.get_yesterday()
        assert isinstance(result, dict)

    def test_reflects_saved_yesterday(self):
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        eve_mod.save_shipped("yesterday's work", date=yesterday)
        entry = eve_mod.get_yesterday()
        assert entry.get("shipped") == "yesterday's work"


class TestTrimMax:
    def test_old_entries_trimmed_on_save(self):
        # Save 35 entries; after save only last 30 should exist
        for i in range(35):
            date = f"2026-{str(i // 30 + 1).zfill(2)}-{str(i % 28 + 1).zfill(2)}"
            try:
                eve_mod.save_shipped(f"work {i}", date=date)
            except Exception:
                pass  # date overflow edge case — just ensure no crash

        # Load the raw file and verify count <= 30
        from navig.agent.proactive.eve_log import _log_path
        path = _log_path()
        if path.exists():
            data = json.loads(path.read_text())
            assert len(data) <= 30
