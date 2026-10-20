"""board.db v6 — the Planner: lists, managed categories, appointments as their own kind.

The migration is the risky part: an existing database must come through with every
row, every reminder link and every pending ping intact, and opening it twice must
change nothing the second time.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from navig.store.board import (
    KIND_APPOINTMENT,
    KIND_CARD,
    KIND_TODO,
    PERSONAL_LIST_ID,
    BoardStore,
)

#: board_card exactly as v5 wrote it — no Planner columns.
_V5_CARD = """
CREATE TABLE board_card (
    id TEXT PRIMARY KEY, goal_id TEXT, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'backlog', priority TEXT NOT NULL DEFAULT 'normal',
    due_at TEXT, reminder_id INTEGER, ai_mode TEXT NOT NULL DEFAULT 'inherit',
    auto_advance INTEGER NOT NULL DEFAULT 0, mission_id TEXT,
    agent_status TEXT NOT NULL DEFAULT 'idle', agent_result TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    completed_at TEXT, kind TEXT NOT NULL DEFAULT 'card', category TEXT NOT NULL DEFAULT '',
    space TEXT, recur TEXT, remind_before TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'manual', origin_ref TEXT
);
CREATE TABLE board_todo_reminder (
    reminder_id INTEGER PRIMARY KEY, card_id TEXT NOT NULL, lead TEXT NOT NULL DEFAULT '',
    fire_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES (5);
"""

_NOW = "2026-10-08T10:00:00.000Z"


def _v5_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V5_CARD)
    rows = [
        ("rdv1", "Visio Sarah", "2026-10-26T09:30:00.000Z", "todo", "rendezvous", '["3d","1d","1h"]'),
        ("t1", "Pay rent", None, "todo", "life", ""),
        ("t2", "Logo", None, "todo", "freelance", ""),
        ("c1", "Ship v2", None, "card", "", ""),
    ]
    for rid, title, due, kind, cat, leads in rows:
        conn.execute(
            "INSERT INTO board_card(id, title, due_at, kind, category, remind_before, created_at,"
            " updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, title, due, kind, cat, leads, _NOW, _NOW),
        )
    conn.execute(
        "INSERT INTO board_todo_reminder VALUES(41, 'rdv1', '1d', '2026-10-25T09:30:00.000Z', ?)",
        (_NOW,),
    )
    conn.commit()
    conn.close()


def test_a_v5_database_migrates_and_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "board.db"
    _v5_db(path)
    store = BoardStore(path)

    assert store.get_schema_version() == 6
    rdv = store.get_todo("rdv1")
    assert rdv is not None
    assert rdv["kind"] == KIND_APPOINTMENT
    assert rdv["category"] == ""
    assert rdv["remind_before"] == ["3d", "1d", "1h"]
    # The scheduled ping is still linked to it — no ghost, no lost reminder.
    assert store.reminder_links([41])[41]["card_id"] == "rdv1"

    assert sorted(t["id"] for t in store.list_todos()) == ["t1", "t2"]  # tasks only
    assert [a["id"] for a in store.list_appointments()] == ["rdv1"]
    assert store.get_card("c1")["kind"] == KIND_CARD  # the Kanban is untouched

    names = [c["name"] for c in store.list_categories()]
    assert names[:4] == ["work", "project", "life", "business"]
    assert "freelance" in names  # the operator's own category is adopted
    assert "rendezvous" not in names
    assert store.get_list(PERSONAL_LIST_ID) is not None


def test_opening_twice_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "board.db"
    _v5_db(path)
    first = BoardStore(path)
    snapshot = (first.list_todos(include_done=True), first.list_appointments(), first.list_categories())
    first.close()
    second = BoardStore(path)
    assert (second.list_todos(include_done=True), second.list_appointments(), second.list_categories()) == snapshot


def test_a_fresh_database_is_seeded(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    assert store.get_schema_version() == 6
    assert [c["name"] for c in store.list_categories()] == ["work", "project", "life", "business"]
    assert [lst["id"] for lst in store.list_lists()] == [PERSONAL_LIST_ID]


def test_list_numbers_count_per_list_and_never_collide(tmp_path: Path) -> None:
    """ "done 2" must mean the same item to both people in a shared list, even when
    they add at the same moment from two processes."""
    path = tmp_path / "board.db"
    a, b = BoardStore(path), BoardStore(path)
    a.ensure_list("tg:5", chat_id=5)
    errors: list[BaseException] = []

    def add(store: BoardStore, n: int) -> None:
        try:
            for i in range(n):
                store.create_todo(f"item {i}", list_id="tg:5")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(s, 15)) for s in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    seqs = sorted(t["list_seq"] for t in a.list_todos(list_id="tg:5"))
    assert seqs == list(range(1, 31))
    assert a.create_todo("mine")["list_seq"] == 1  # the personal list counts on its own


def test_the_kanban_routes_cannot_touch_a_planner_row(tmp_path: Path) -> None:
    """They used to accept any id — a todo edited or deleted through the board skipped
    its reminder cleanup and left ghost pings."""
    store = BoardStore(tmp_path / "board.db")
    todo = store.create_todo("Pay rent")
    assert store.update_card(todo["id"], {"title": "hijacked"}) is None
    assert store.move_card(todo["id"], "done") is None
    store.delete_card(todo["id"])
    assert store.get_todo(todo["id"])["title"] == "Pay rent"


def test_an_appointment_is_reachable_by_the_planner_getters(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    appt = store.create_todo("Dentist", kind=KIND_APPOINTMENT, due_at="2026-10-26T09:30:00.000Z",
                             location="Rue X", with_whom="Dr Y")
    assert store.get_todo(appt["id"])["location"] == "Rue X"
    assert store.update_todo(appt["id"], {"title": "Dentist (moved)"})["title"] == "Dentist (moved)"
    assert store.delete_todo(appt["id"]) is True
    with pytest.raises(ValueError):
        store.create_todo("nope", kind=KIND_CARD)


def test_rename_moves_every_row_and_delete_refuses_a_used_category(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.ensure_category("freelance")
    t = store.create_todo("Logo", category="freelance")
    store.rename_category("freelance", "clients")
    assert store.get_todo(t["id"])["category"] == "clients"
    assert store.get_category("freelance") is None
    with pytest.raises(ValueError):
        store.delete_category("clients")
    with pytest.raises(ValueError):
        store.rename_category("clients", "work")  # taken
    store.ensure_category("spare")
    assert store.delete_category("spare") is True


def test_moving_to_a_list_takes_the_next_number_there(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.ensure_list("tg:9", chat_id=9)
    store.create_todo("existing", list_id="tg:9")
    mine = store.create_todo("mine")
    moved = store.move_to_list(mine["id"], "tg:9")
    assert (moved["list_id"], moved["list_seq"]) == ("tg:9", 2)
    assert store.get_todo(mine["id"])["kind"] == KIND_TODO
