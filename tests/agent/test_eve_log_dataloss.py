"""A transient/unreadable read of eve_log.json must NOT wipe the 30-day journal.

save_shipped/save_priority load through `_load_for_update`, which routes to json_io's
`load_json_for_update` and RAISES `JsonReadError` on a file that exists-with-content but is
transiently unreadable (a Windows AV/backup lock). The caller (telegram_commands) wraps the
save in try/except, so this degrades to a benign "noted" with the journal intact. The old
`_load` returned {} on that lock, so the save then wrote only today's entry — wiping the
prior month of shipped/priority notes.
"""

from __future__ import annotations

import pytest

import navig.agent.proactive.eve_log as eve
import navig.core.json_io as jio

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    yield tmp_path


def test_saves_are_additive_across_days():
    eve.save_shipped("shipped A", date="2026-01-01")
    eve.save_priority("anchor B", date="2026-01-02")
    assert eve.get_entry("2026-01-01")["shipped"] == "shipped A"
    assert eve.get_entry("2026-01-02")["priority"] == "anchor B"


def test_save_during_lock_does_not_wipe_journal(monkeypatch):
    eve.save_shipped("shipped A", date="2026-01-01")
    eve.save_priority("anchor B", date="2026-01-02")
    log = eve._log_path()
    before = log.read_text(encoding="utf-8")
    assert "shipped A" in before and "anchor B" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # The mutating load raises (propagates to telegram_commands' try/except) rather than
    # returning {} and letting _save wipe the earlier days.
    with pytest.raises(jio.JsonReadError):
        eve.save_shipped("shipped C", date="2026-01-03")

    # Journal on disk intact (read directly — Path.read_text, not the patched json_io).
    assert log.read_text(encoding="utf-8") == before


def test_read_only_getters_degrade_on_lock(monkeypatch):
    eve.save_shipped("shipped A", date="2026-01-01")

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)
    # Briefing reads must not crash — they degrade to an empty entry.
    assert eve.get_entry("2026-01-01") == {}
    assert eve.get_today() == {}
