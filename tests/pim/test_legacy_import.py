"""Folding the pre-Planner ``~/.navig/tasks.json`` into the personal list.

The one-time import has to be safe to run twice (the Mini App, the OS and the CLI all
offer it), must keep a done task done, and must never turn a transient read failure
into "0 tasks, file retired" — that would silently erase the operator's list.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders as pim_reminders
from navig.pim import service
from navig.pim.clock import LOCAL
from navig.store.board import BoardStore

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)
OPERATOR = 7


class _Runtime:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    def create_reminder(self, user_id, chat_id, message, remind_at, *, bcid=None):  # noqa: ANN001
        rid = len(self.rows) + 1
        self.rows[rid] = {"id": rid, "user_id": user_id, "bcid": bcid}
        return rid

    def cancel_reminder(self, reminder_id, user_id):  # noqa: ANN001
        return self.rows.pop(reminder_id, None) is not None

    def get_reminder(self, reminder_id):  # noqa: ANN001
        return self.rows.get(int(reminder_id))

    def list_upcoming(self, limit=50):  # noqa: ANN001
        return list(self.rows.values())[:limit]


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> tuple[BoardStore, Path]:
    store = BoardStore(tmp_path / "board.db")
    legacy = tmp_path / "tasks.json"
    runtime = _Runtime()
    monkeypatch.setattr(service, "_store", lambda: store)
    monkeypatch.setattr(service, "_runtime", lambda: runtime)
    monkeypatch.setattr(pim_reminders, "_runtime_store", lambda: runtime)
    monkeypatch.setattr(pim_reminders, "_extension_is_off", lambda: False)
    monkeypatch.setattr(pim_reminders, "operator_delivery", lambda: (OPERATOR, OPERATOR))
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr(service, "_legacy_tasks_path", lambda: legacy)
    return store, legacy


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_no_file_is_a_quiet_no_op(env) -> None:
    store, _ = env
    report = service.import_legacy_tasks()
    assert report == {"found": 0, "imported": 0, "completed": 0, "skipped": 0, "path": None}
    assert store.list_todos(include_done=True) == []


def test_tasks_land_in_the_personal_list_and_done_stays_done(env) -> None:
    store, legacy = env
    _write(legacy, [
        {"id": "a", "title": "Buy paper", "done": False, "due_at": None},
        {"id": "b", "title": "Mail the form", "done": True, "due_at": None},
    ])
    report = service.import_legacy_tasks()
    assert (report["found"], report["imported"], report["completed"]) == (2, 2, 1)
    items = {t["title"]: t for t in store.list_todos(list_id="me", include_done=True)}
    assert set(items) == {"Buy paper", "Mail the form"}
    assert items["Mail the form"]["completed_at"]
    assert not items["Buy paper"]["completed_at"]
    # The file is retired, not deleted.
    assert not legacy.exists()
    assert legacy.with_name("tasks.json.imported").exists()


def test_running_twice_imports_nothing_new(env) -> None:
    store, legacy = env
    _write(legacy, [{"id": "a", "title": "Buy paper", "done": False}])
    service.import_legacy_tasks()
    # A second file with the SAME id must not duplicate (idempotent by origin_ref).
    _write(legacy, [{"id": "a", "title": "Buy paper", "done": False}])
    report = service.import_legacy_tasks()
    assert (report["imported"], report["skipped"]) == (0, 1)
    assert len(store.list_todos(list_id="me", include_done=True)) == 1


def test_a_dated_task_keeps_its_date(env) -> None:
    store, legacy = env
    _write(legacy, [{"id": "a", "title": "Renew pass", "due_at": "2026-10-20T09:00:00+02:00"}])
    service.import_legacy_tasks()
    (item,) = store.list_todos(list_id="me")
    assert item["due_at"]  # a stored UTC time, not None


def test_a_bad_date_is_imported_dateless_not_dropped(env) -> None:
    store, legacy = env
    _write(legacy, [{"id": "a", "title": "Weird one", "due_at": "not-a-date"}])
    report = service.import_legacy_tasks()
    assert report["imported"] == 1
    (item,) = store.list_todos(list_id="me")
    assert item["title"] == "Weird one" and item["due_at"] is None


def test_blank_rows_are_skipped(env) -> None:
    store, legacy = env
    _write(legacy, [{"id": "", "title": "no id"}, {"id": "x", "title": ""}, "garbage"])
    report = service.import_legacy_tasks()
    assert (report["found"], report["imported"], report["skipped"]) == (3, 0, 3)
    assert store.list_todos(include_done=True) == []


def test_a_corrupt_file_raises_and_is_left_in_place(env) -> None:
    store, legacy = env
    legacy.write_text("{not json", encoding="utf-8")
    with pytest.raises(service.PlannerError):
        service.import_legacy_tasks()
    # The file must survive — a failed read must never retire it.
    assert legacy.exists()
    assert not legacy.with_name("tasks.json.imported").exists()
