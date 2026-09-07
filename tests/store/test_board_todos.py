"""The PIM's half of BoardStore.

A todo and a Kanban card share one table, which buys the dependency graph, the history
log and the migration machinery for free — and costs exactly one invariant: the board
must never show the errands. That invariant, and the reminder bookkeeping that produces
ghost pings when it slips, are what these tests are for.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from navig.store.board import (
    KIND_CARD,
    KIND_TODO,
    SEED_CATEGORIES,
    BoardStore,
)


@pytest.fixture
def store(tmp_path: Path) -> BoardStore:
    return BoardStore(tmp_path / "board.db")


# ── the two populations stay apart ───────────────────────────────────────────

def test_the_kanban_never_sees_a_todo(store: BoardStore) -> None:
    """The invariant the whole design rests on.

    Without it, "Buy milk" appears in the desktop board's Backlog column the first
    time the PIM is used — which is the exact reason people end up running a second
    task app rather than the one they already have.
    """
    store.create_card("Ship the deck")
    store.create_todo("Buy milk")

    assert [c["title"] for c in store.list_cards()] == ["Ship the deck"]
    assert [t["title"] for t in store.list_todos()] == ["Buy milk"]
    assert [c["title"] for c in store.snapshot()["cards"]] == ["Ship the deck"]


def test_a_card_cannot_be_turned_into_a_todo_through_the_generic_patch(store: BoardStore) -> None:
    """`kind` is deliberately absent from `_CARD_WRITABLE`.

    A writable `kind` would let any client — or a field-name typo in one — move a real
    project task into the personal list, or an errand onto the board, with nothing to
    notice it by.
    """
    card = store.create_card("Ship the deck")
    store.update_card(card["id"], {"kind": KIND_TODO, "title": "Ship the deck v2"})

    assert store.get_card(card["id"])["kind"] == KIND_CARD
    assert store.get_card(card["id"])["title"] == "Ship the deck v2", "the patch itself must still work"
    assert store.list_todos() == []


def test_todo_lookups_are_kind_scoped_in_both_directions(store: BoardStore) -> None:
    card = store.create_card("Ship the deck")
    todo = store.create_todo("Buy milk")

    assert store.get_todo(card["id"]) is None
    assert store.get_card(todo["id"]) is not None, "a todo is still a card row"
    assert store.delete_todo(card["id"]) is False, "the PIM must not delete a board card"
    assert store.get_card(card["id"]) is not None


# ── capture ──────────────────────────────────────────────────────────────────

def test_a_todo_needs_only_a_title(store: BoardStore) -> None:
    """A dateless todo is an INBOX item, not an incomplete one.

    Demanding a date at capture time is what makes a task list something you stop
    opening.
    """
    todo = store.create_todo("Call the accountant")
    assert todo["due_at"] is None
    assert todo["stage"] == "inbox"
    assert todo["completed_at"] is None
    assert todo["origin"] == "manual"
    assert todo["remind_before"] == []


def test_capture_carries_every_field(store: BoardStore) -> None:
    todo = store.create_todo(
        "Renew the domain",
        category="Business",
        due_at="2026-09-12T09:00:00.000Z",
        recur="yearly",
        remind_before=["3d", "1d"],
        space="homelab-space",
        notes="registrar login is in the vault",
        priority="high",
        origin="ai",
        origin_ref="homelab-space:CURRENT_PHASE.md:14",
    )
    assert todo["category"] == "business", "categories are normalised, so filters match"
    assert todo["remind_before"] == ["3d", "1d"]
    assert todo["recur"] == "yearly"
    assert todo["space"] == "homelab-space"
    assert todo["origin"] == "ai"
    assert todo["priority"] == "high"


def test_an_unknown_origin_falls_back_to_manual_rather_than_being_stored(store: BoardStore) -> None:
    """`origin` is how a suggestion is told from something the operator typed.

    Storing an unrecognised value would create a third state that no filter matches,
    so an AI-authored row could quietly render as one the operator wrote.
    """
    assert store.create_todo("x", origin="smuggled")["origin"] == "manual"


# ── ordering IS the view ─────────────────────────────────────────────────────

def test_dated_todos_come_first_soonest_first_and_the_inbox_settles_last(store: BoardStore) -> None:
    store.create_todo("No date at all")
    store.create_todo("Later", due_at="2026-12-01T09:00:00.000Z")
    store.create_todo("Sooner", due_at="2026-09-01T09:00:00.000Z")

    assert [t["title"] for t in store.list_todos()] == ["Sooner", "Later", "No date at all"]


def test_done_todos_are_hidden_unless_asked_for(store: BoardStore) -> None:
    done = store.create_todo("Already handled")
    store.complete_todo(done["id"])
    store.create_todo("Still open")

    assert [t["title"] for t in store.list_todos()] == ["Still open"]
    assert len(store.list_todos(include_done=True)) == 2


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"category": "life"}, ["Dentist"]),
        ({"space": "homelab-space"}, ["Renew cert"]),
        ({"origin": "ai"}, ["Renew cert"]),
    ],
)
def test_filters(store: BoardStore, kwargs: dict, expected: list[str]) -> None:
    store.create_todo("Dentist", category="life")
    store.create_todo("Renew cert", category="business", space="homelab-space", origin="ai")
    assert [t["title"] for t in store.list_todos(**kwargs)] == expected


def test_a_category_filter_matches_whatever_case_it_was_typed_in(store: BoardStore) -> None:
    store.create_todo("Dentist", category="Life")
    assert [t["title"] for t in store.list_todos(category="LIFE")] == ["Dentist"]


# ── completion and recurrence ────────────────────────────────────────────────

def test_completing_a_plain_todo_stamps_completed_at(store: BoardStore) -> None:
    """`completed_at` is the truth, NOT the stage.

    Stages are user-editable through `board_settings`, so renaming the "done" column
    in the Deck would silently stop todos completing if the stage decided it.
    """
    todo = store.create_todo("Post the letter")
    done = store.complete_todo(todo["id"])

    assert done is not None
    assert done["completed_at"] is not None
    assert store.list_todos() == []


def test_completing_a_RECURRING_todo_rolls_it_forward_instead(store: BoardStore) -> None:
    """A recurring task must never leave the list.

    Completing it would remove it forever, which is the opposite of what a recurrence
    is for — and the operator would only find out the next time it failed to appear.
    """
    todo = store.create_todo("Water the plants", due_at="2026-09-04T19:00:00.000Z", recur="daily")
    rolled = store.complete_todo(todo["id"])

    assert rolled is not None
    assert rolled["completed_at"] is None, "a recurring todo stays open"
    assert rolled["due_at"] is not None
    assert rolled["due_at"].startswith("2026-09-05"), rolled["due_at"]
    assert [t["title"] for t in store.list_todos()] == ["Water the plants"]


def test_a_recurring_todo_with_no_date_completes_normally(store: BoardStore) -> None:
    """There is nothing to advance, so "every week" on an inbox item cannot mean
    anything except "tick it off"."""
    todo = store.create_todo("Someday", recur="weekly")
    assert store.complete_todo(todo["id"])["completed_at"] is not None


def test_a_corrupt_due_date_does_not_make_a_task_impossible_to_tick_off(store: BoardStore) -> None:
    """A stored value the parser cannot read must degrade to a plain completion.

    Raising here would strand the row: the operator can neither complete it nor see
    why, and the only route out is the database.
    """
    todo = store.create_todo("Odd one", due_at="not-a-date", recur="daily")
    assert store.complete_todo(todo["id"])["completed_at"] is not None


def test_reopening_puts_it_back_where_it_belongs(store: BoardStore) -> None:
    """Ticking the wrong row is the most common slip on a phone keyboard."""
    dated = store.create_todo("Dentist", due_at="2026-09-12T09:00:00.000Z")
    plain = store.create_todo("Someday")
    store.complete_todo(dated["id"])
    store.complete_todo(plain["id"])

    assert store.reopen_todo(dated["id"])["stage"] == "scheduled"
    assert store.reopen_todo(plain["id"])["stage"] == "inbox"
    assert len(store.list_todos()) == 2


def test_completion_is_recorded_in_the_history_log(store: BoardStore) -> None:
    todo = store.create_todo("Post the letter")
    store.complete_todo(todo["id"])
    assert any(h["to_stage"] == "done" for h in store.recent_history())


# ── categories ───────────────────────────────────────────────────────────────

def test_the_picker_is_never_empty_on_a_fresh_install(store: BoardStore) -> None:
    names = [c["name"] for c in store.todo_categories()]
    assert names == list(SEED_CATEGORIES)
    assert all(c["open"] == 0 for c in store.todo_categories())


def test_a_category_the_operator_invents_needs_no_migration(store: BoardStore) -> None:
    """The column is plain TEXT on purpose — a new category works immediately."""
    store.create_todo("Feed the cat", category="pets")
    names = [c["name"] for c in store.todo_categories()]
    assert "pets" in names
    assert next(c for c in store.todo_categories() if c["name"] == "pets")["open"] == 1


def test_counts_are_of_OPEN_todos_only(store: BoardStore) -> None:
    """A category badge counting done items reads as a pile of work that is finished."""
    a = store.create_todo("One", category="life")
    store.create_todo("Two", category="life")
    store.complete_todo(a["id"])
    assert next(c for c in store.todo_categories() if c["name"] == "life")["open"] == 1


def test_uncategorised_todos_are_counted_but_not_promoted_to_a_seed(store: BoardStore) -> None:
    store.create_todo("Loose end")
    rows = store.todo_categories()
    assert rows[-1]["name"] == ""
    assert rows[-1]["open"] == 1


# ── reminder bookkeeping ─────────────────────────────────────────────────────

def test_reminders_are_linked_and_returned_in_fire_order(store: BoardStore) -> None:
    todo = store.create_todo("Dentist", due_at="2026-09-12T09:00:00.000Z")
    store.link_reminder(todo["id"], 20, lead="1d", fire_at="2026-09-11T09:00:00.000Z")
    store.link_reminder(todo["id"], 10, lead="3d", fire_at="2026-09-09T09:00:00.000Z")

    assert store.todo_reminder_ids(todo["id"]) == [10, 20]


def test_unlink_hands_the_ids_back_so_the_caller_can_cancel_them(store: BoardStore) -> None:
    """This store must not import the reminder store, so the CALLER owns cancellation.

    Dropping the returned ids is the single most likely live bug in this feature: the
    rows vanish from the link table, the reminder table still holds them, and the
    operator gets pinged about a task that no longer exists.
    """
    todo = store.create_todo("Dentist", due_at="2026-09-12T09:00:00.000Z")
    store.link_reminder(todo["id"], 7, lead="1d", fire_at="2026-09-11T09:00:00.000Z")

    assert store.unlink_reminders(todo["id"]) == [7]
    assert store.todo_reminder_ids(todo["id"]) == []


def test_deleting_a_todo_takes_its_reminder_links_with_it(store: BoardStore) -> None:
    """ON DELETE CASCADE, so a deleted task cannot leave rows pointing at nothing."""
    todo = store.create_todo("Dentist", due_at="2026-09-12T09:00:00.000Z")
    store.link_reminder(todo["id"], 9, lead="1d", fire_at="2026-09-11T09:00:00.000Z")

    assert store.delete_todo(todo["id"]) is True
    assert store.todo_reminder_ids(todo["id"]) == []


def test_relinking_the_same_reminder_id_replaces_rather_than_duplicates(store: BoardStore) -> None:
    todo = store.create_todo("Dentist")
    store.link_reminder(todo["id"], 5, lead="1d", fire_at="2026-09-11T09:00:00.000Z")
    store.link_reminder(todo["id"], 5, lead="3d", fire_at="2026-09-09T09:00:00.000Z")
    assert store.todo_reminder_ids(todo["id"]) == [5]


# ── AI suggestion dedup ──────────────────────────────────────────────────────

def test_a_dismissed_suggestion_is_still_remembered(store: BoardStore) -> None:
    """The dedup key must match a DELETED suggestion too, or the same checkbox is
    proposed again on the next scan — which is how a helpful assistant becomes a nag.
    """
    ref = "homelab-space:CURRENT_PHASE.md:14"
    todo = store.create_todo("Renew cert", origin="ai", origin_ref=ref)
    assert store.todo_exists_for_source(ref) is True

    store.complete_todo(todo["id"])
    assert store.todo_exists_for_source(ref) is True, "completing must not un-remember it"
    assert store.todo_exists_for_source("some-other-line") is False


# ── migration ────────────────────────────────────────────────────────────────

def _v2_database(path: Path) -> None:
    """A populated v2 board, exactly as it exists on disk before this change."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE board_goal (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#6366f1', space TEXT,
            status TEXT NOT NULL DEFAULT 'active', sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE board_card (
            id TEXT PRIMARY KEY, goal_id TEXT, title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'backlog',
            priority TEXT NOT NULL DEFAULT 'normal', due_at TEXT, reminder_id INTEGER,
            ai_mode TEXT NOT NULL DEFAULT 'inherit', auto_advance INTEGER NOT NULL DEFAULT 0,
            mission_id TEXT, agent_status TEXT NOT NULL DEFAULT 'idle',
            agent_result TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
        );
        CREATE TABLE board_dep (
            card_id TEXT NOT NULL, depends_on_id TEXT NOT NULL,
            PRIMARY KEY (card_id, depends_on_id)
        );
        CREATE TABLE board_subtask (
            id TEXT PRIMARY KEY, card_id TEXT NOT NULL, title TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE board_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL, from_stage TEXT,
            to_stage TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL
        );
        CREATE TABLE board_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (2);
        INSERT INTO board_card(id, title, created_at, updated_at)
            VALUES ('keepme', 'Pass the driver licence', '2026-06-18T10:00:00Z', '2026-06-18T10:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def test_a_populated_v2_database_upgrades_without_losing_a_row(tmp_path: Path) -> None:
    """The acceptance case: the operator's real board survives the upgrade.

    ⚠ `BaseStore._init_schema` runs `_create_schema` BEFORE reading the version, so on
    a v2 file the CREATE statements are no-ops (IF NOT EXISTS) and the ALTERs then do
    the real work. Getting that order wrong is silent: the version stamps at 3 having
    added nothing, and the store then reads columns that do not exist.
    """
    db = tmp_path / "v2.db"
    _v2_database(db)

    store = BoardStore(db)

    assert store.get_schema_version() == BoardStore.SCHEMA_VERSION
    assert [c["title"] for c in store.list_cards()] == ["Pass the driver licence"]
    assert store.list_cards()[0]["kind"] == KIND_CARD, "existing rows default to board cards"
    assert store.list_todos() == []


def test_the_upgraded_database_can_immediately_take_a_todo(tmp_path: Path) -> None:
    """The columns are really there, not just stamped."""
    db = tmp_path / "v2.db"
    _v2_database(db)
    store = BoardStore(db)

    todo = store.create_todo("Buy milk", category="life", remind_before=["1d"])
    assert store.get_todo(todo["id"])["remind_before"] == ["1d"]
    store.link_reminder(todo["id"], 1, lead="1d", fire_at="2026-09-11T09:00:00.000Z")
    assert store.todo_reminder_ids(todo["id"]) == [1]


def test_reopening_the_same_database_is_a_no_op(tmp_path: Path) -> None:
    """Idempotence. Every ALTER is individually guarded, so a second open must not
    raise `duplicate column name` and abandon the migration half-applied."""
    db = tmp_path / "v2.db"
    _v2_database(db)
    BoardStore(db).create_todo("First open")

    reopened = BoardStore(db)
    assert [t["title"] for t in reopened.list_todos()] == ["First open"]
    assert reopened.get_schema_version() == BoardStore.SCHEMA_VERSION


def test_a_missing_migration_step_fails_loudly(tmp_path: Path) -> None:
    """`BaseStore._migrate` is a silent `pass`, so a version bump with no step would
    stamp the new number having applied nothing — and the version would then swear
    everything was fine while the store read columns that do not exist."""
    db = tmp_path / "future.db"
    store = BoardStore(db)
    with pytest.raises(RuntimeError, match="migration path missing"):
        store._migrate(sqlite3.connect(db), BoardStore.SCHEMA_VERSION, BoardStore.SCHEMA_VERSION + 1)


# ── v4: the dismissal ledger ─────────────────────────────────────────────────

def test_deleting_a_SUGGESTION_leaves_a_dismissal_record(store: BoardStore) -> None:
    """"No" has to mean no permanently.

    `todo_exists_for_source` used to ask only `board_card`, so deleting a suggestion
    deleted the only evidence it had been offered — and the next scan proposed the
    identical line again. That teaches the operator that dismissing does nothing, which
    is how a propose-and-confirm flow becomes a nag.
    """
    ref = "homelab-space:CURRENT_PHASE.md:14"
    todo = store.create_todo("Renew the cert", origin="ai", origin_ref=ref)

    assert store.delete_todo(todo["id"]) is True
    assert store.get_todo(todo["id"]) is None
    assert store.todo_exists_for_source(ref) is True, "the dismissal must outlive the row"


def test_deleting_a_HAND_WRITTEN_todo_records_nothing(store: BoardStore) -> None:
    """Only a task with a SOURCE can be re-proposed, so only that needs remembering.

    A ledger entry for every deletion would grow without bound and record nothing
    anyone can act on.
    """
    todo = store.create_todo("Something I typed myself")
    assert store.delete_todo(todo["id"]) is True
    assert store.todo_exists_for_source("") is False


def test_deleting_a_todo_that_is_already_gone_reports_false(store: BoardStore) -> None:
    assert store.delete_todo("neverexisted") is False


def test_a_populated_v3_database_upgrades_to_v4(tmp_path: Path) -> None:
    """The migration chain runs every step, not just the last one.

    `_migrate` dispatches 2→3 then 3→4; a database created before either still has to
    arrive with both the PIM columns AND the dismissal ledger.
    """
    db = tmp_path / "v2.db"
    _v2_database(db)

    store = BoardStore(db)
    assert store.get_schema_version() == BoardStore.SCHEMA_VERSION

    ref = "s:CURRENT_PHASE.md:1"
    todo = store.create_todo("From a plan", origin="agent", origin_ref=ref)
    store.delete_todo(todo["id"])
    assert store.todo_exists_for_source(ref) is True
    assert [c["title"] for c in store.list_cards()] == ["Pass the driver licence"]
