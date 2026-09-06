"""The board's pre-rename tables must be adopted, not silently orphaned.

Found on the operator's own machine, 2026-09-05: `~/.navig/data/store/board.db`
was stamped `schema_version = 1` and held **5 goals, 8 cards, 3 dependencies and 14
history rows** -- *Pass the driver licence*, *Reduce Monthly Expenses*, *Audit
recurring charges* -- in tables named `goals` / `cards` / `subtasks` / `card_deps`
/ `card_history`.

`BoardStore` reads `board_goal` / `board_card` / `board_subtask` / `board_dep` /
`board_history`. It was REWRITTEN, not renamed (cc6a4da03 "write the missing
BoardStore -- Goals & Tasks were 500ing on every load"), and no migration
referencing the old names existed anywhere in the repo.

The failure mode is silent by construction: `_create_schema` is all
`CREATE TABLE IF NOT EXISTS board_*`, so the next open would create empty tables
beside the real data and the operator's tasks would simply never appear again. No
error, no log line, and `schema_version` would still read a confident `1`.

Two things this file pins that a hand-written migration gets wrong:

* **The canonical tables already exist when the migration runs.**
  `BaseStore._init_schema` calls `_create_schema` BEFORE reading the version, so a
  condition of "canonical table is missing" is never true and makes the whole
  rescue a no-op on exactly the databases it exists for. The test builds the legacy
  layout and asserts the rows arrive anyway.
* **`card_history` is NOT column-identical.** Four of the five pairs are; that one
  renamed `ts` to `created_at`. The first implementation copied "shared columns
  only", left `created_at` unfilled, and hit `NOT NULL constraint failed` -- which
  aborted the ENTIRE migration, taking the goals and cards with it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from navig.store.board import BoardStore

# The legacy schema, transcribed from the operator's real database. Column order and
# names are exact -- this is a reproduction, not an approximation.
_LEGACY_SQL = """
CREATE TABLE goals (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#6366f1', space TEXT,
    status TEXT NOT NULL DEFAULT 'active', sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE cards (
    id TEXT PRIMARY KEY, goal_id TEXT, title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'backlog',
    priority TEXT NOT NULL DEFAULT 'normal', due_at TEXT, reminder_id INTEGER,
    ai_mode TEXT NOT NULL DEFAULT 'inherit', auto_advance INTEGER NOT NULL DEFAULT 0,
    mission_id TEXT, agent_status TEXT NOT NULL DEFAULT 'idle',
    agent_result TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE subtasks (
    id TEXT PRIMARY KEY, card_id TEXT NOT NULL, title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, sort_order REAL DEFAULT 0
);
CREATE TABLE card_deps (
    card_id TEXT NOT NULL, depends_on_id TEXT NOT NULL,
    PRIMARY KEY (card_id, depends_on_id)
);
CREATE TABLE card_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL,
    from_stage TEXT, to_stage TEXT, actor TEXT NOT NULL DEFAULT 'user',
    ts TEXT NOT NULL
);
"""

_TS = "2026-06-18T08:07:00+00:00"


def _legacy_db(path: Path, *, cards: int = 3) -> None:
    """Build a v1 database in the OLD layout, stamped version 1."""
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_SQL)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute(
        "INSERT INTO goals (id, title, description, color, space, status, sort_order,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("g1", "Pass the driver licence", "", "#6366f1", None, "active", 0, _TS, _TS),
    )
    for i in range(cards):
        conn.execute(
            "INSERT INTO cards (id, goal_id, title, notes, stage, priority, sort_order,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"c{i}", "g1", f"Legacy task {i}", "", "backlog", "normal", i, _TS, _TS),
        )
    conn.execute(
        "INSERT INTO card_deps (card_id, depends_on_id) VALUES (?,?)", ("c1", "c0")
    )
    conn.execute(
        "INSERT INTO card_history (card_id, from_stage, to_stage, actor, ts)"
        " VALUES (?,?,?,?,?)",
        ("c0", None, "backlog", "operator", _TS),
    )
    conn.commit()
    conn.close()


def test_legacy_rows_are_adopted(tmp_path: Path) -> None:
    """The headline: the operator's tasks must survive the rename."""
    db = tmp_path / "board.db"
    _legacy_db(db, cards=3)

    store = BoardStore(db)

    titles = [c["title"] for c in store.list_cards()]
    assert len(titles) == 3, f"legacy cards were not adopted: {titles}"
    assert "Legacy task 0" in titles
    assert [g["title"] for g in store.list_goals()] == ["Pass the driver licence"]
    # Compared against the class constant, not a literal: this asserts "every
    # migration step ran", which is what the adoption depends on. Pinning the
    # number means a later, unrelated schema bump reads as a broken rescue.
    assert store.get_schema_version() == BoardStore.SCHEMA_VERSION


def test_the_renamed_history_column_is_mapped(tmp_path: Path) -> None:
    """`card_history.ts` -> `board_history.created_at`.

    The regression: copying only the columns whose names appear in BOTH tables
    leaves `created_at` unset, SQLite raises `NOT NULL constraint failed`, and
    because that is an IntegrityError rather than an OperationalError it escaped a
    narrower `except` and aborted the whole migration -- so the goals and cards
    above never arrived either. One renamed column, all the data lost.
    """
    db = tmp_path / "board.db"
    _legacy_db(db)
    BoardStore(db)

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT card_id, created_at FROM board_history").fetchall()
    assert rows, "history rows were dropped"
    assert rows[0][1] == _TS, f"created_at was not sourced from ts: {rows[0]}"


def test_dependencies_survive(tmp_path: Path) -> None:
    db = tmp_path / "board.db"
    _legacy_db(db)
    BoardStore(db)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM board_dep").fetchone()[0] == 1


def test_the_legacy_tables_are_kept_not_dropped(tmp_path: Path) -> None:
    """This is the operator's ONLY copy. Rename, never drop."""
    db = tmp_path / "board.db"
    _legacy_db(db)
    BoardStore(db)

    conn = sqlite3.connect(db)
    kept = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_migrated_v1'"
        )
    }
    assert "cards_migrated_v1" in kept, f"the source data was destroyed: {kept}"
    assert "goals_migrated_v1" in kept
    assert (
        conn.execute("SELECT COUNT(*) FROM cards_migrated_v1").fetchone()[0] == 3
    ), "the preserved copy is empty, which is not a preserved copy"


def test_reopening_does_not_duplicate(tmp_path: Path) -> None:
    """Idempotence. A migration that runs twice must not double the rows."""
    db = tmp_path / "board.db"
    _legacy_db(db, cards=3)

    first = len(BoardStore(db).list_cards())
    second = len(BoardStore(db).list_cards())
    third = len(BoardStore(db).list_cards())

    assert first == second == third == 3, f"rows multiplied: {first}/{second}/{third}"


def test_live_data_is_never_overwritten(tmp_path: Path) -> None:
    """If the canonical table already holds rows, the legacy copy must not clobber it.

    Restoring a backup over a live database is exactly when this matters, and it is
    the one case where "adopt the old rows" would destroy the newer ones.
    """
    db = tmp_path / "board.db"
    _legacy_db(db, cards=3)
    store = BoardStore(db)  # adopts; renames the legacy tables
    store.create_card(title="Written after the migration")

    # Re-introduce a legacy table alongside the now-populated canonical one.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE cards AS SELECT * FROM cards_migrated_v1;"
    )
    conn.commit()
    conn.close()

    titles = [c["title"] for c in BoardStore(db).list_cards()]
    assert "Written after the migration" in titles
    assert len(titles) == 4, f"a second adoption duplicated live rows: {titles}"


def test_a_fresh_database_needs_no_migration(tmp_path: Path) -> None:
    """The floor: a brand-new install must not depend on the rescue path."""
    store = BoardStore(tmp_path / "fresh.db")
    assert store.get_schema_version() == BoardStore.SCHEMA_VERSION
    assert store.list_cards() == []

    goal = store.create_goal(title="G")
    card = store.create_card(title="C", goal_id=goal["id"])
    assert card["title"] == "C"


def test_a_missing_migration_step_fails_loudly(tmp_path: Path) -> None:
    """`BaseStore._migrate` is a silent `pass`.

    Bumping SCHEMA_VERSION without a step would stamp the new version having applied
    nothing, and the store would then read columns that do not exist while the
    version number swore everything was fine. The dispatcher must raise instead.
    """
    store = BoardStore(tmp_path / "x.db")
    conn = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(RuntimeError, match="migration path missing"):
        store._migrate(conn, 98, 99)
