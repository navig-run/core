"""A transient/corrupt read of the deck apps' JSON stores must NOT wipe the operator's
data. `_load_tasks` / `_load_cron_jobs` route through json_io's `load_json_for_update`,
which RAISES on a file that exists-with-content but is transiently unreadable (a Windows
AV/backup lock) — so the RMW add/toggle/reminders handlers abort their save instead of
writing `[]` over every record. The old `except: return []` silently did that = data loss.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.gateway.deck.routes import apps

pytestmark = pytest.mark.integration


def test_load_tasks_round_trips_and_treats_missing_as_empty(monkeypatch, tmp_path):
    p = tmp_path / "tasks.json"
    monkeypatch.setattr(apps, "_tasks_path", lambda: p)

    assert apps._load_tasks() == []  # missing file → [] (a fresh install)
    apps._save_tasks([{"id": "1", "title": "a", "done": False}])
    assert apps._load_tasks() == [{"id": "1", "title": "a", "done": False}]  # round-trip


def test_load_tasks_raises_on_transient_unreadable_rather_than_wiping(monkeypatch, tmp_path):
    p = tmp_path / "tasks.json"
    p.write_text('[{"id": "1", "title": "keep me", "done": false}]', encoding="utf-8")
    monkeypatch.setattr(apps, "_tasks_path", lambda: p)

    def _locked(*_a, **_k):
        raise OSError("file is locked")  # a lock that survived json_io's retries

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # RAISES instead of returning [] — so a caller's load→save can't wipe the file.
    with pytest.raises(jio.JsonReadError):
        apps._load_tasks()
    # The file's records survived (the aborted save never ran).
    assert "keep me" in p.read_text(encoding="utf-8")


def test_load_cron_jobs_raises_on_transient_unreadable_rather_than_wiping(monkeypatch, tmp_path):
    # Force the file fallback (no live scheduler in the test), then a persistent lock.
    monkeypatch.setattr("navig.scheduler.cron_service.get_live_service", lambda: None)
    p = tmp_path / "cron.json"
    p.write_text('{"jobs": [{"id": "r1"}], "counter": 1}', encoding="utf-8")
    monkeypatch.setattr(apps, "_cron_jobs_path", lambda: p)

    def _locked(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)
    with pytest.raises(jio.JsonReadError):
        apps._load_cron_jobs()
    assert '"r1"' in p.read_text(encoding="utf-8")  # reminders not wiped
