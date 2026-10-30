"""BoardStore — persistence for the NAVIG Tasks / Goals "pipeline chain" Kanban.

The Deck/OS board route (`navig/gateway/deck/routes/board.py`) has always imported
`navig.store.board.get_board_store`, but the store module was never committed — so
`/api/deck/board` 500'd with ``No module named 'navig.store.board'`` and both the
Goals and Tasks apps showed "Couldn't load your board". This is that store.

Model
-----
* **goal**    — a bucket cards belong to (optional; deleting a goal orphans its
  cards via ``ON DELETE SET NULL``).
* **card**    — one task. Moves across ``stages`` (backlog → in_progress → agent →
  done by default). Carries an AI mission mode (``ai_mode``) and status.
* **dep**     — a directed edge ``card depends_on other``; the graph is a DAG
  (cycles are rejected). A card's derived ``gate`` is ``waiting`` while any
  prerequisite is unfinished, else ``ready``.
* **subtask** — a checklist item under a card.
* **history** — an append-only log of stage changes (who moved what, when).
* **settings**— board-wide defaults (``default_ai_mode``, ``default_auto_advance``)
  plus the ordered ``stages`` that define the columns.

``deps`` and ``gate`` are **derived** on read, never stored on the card row, so
they can never drift from the edge table.

DB: ``~/.navig/data/store/board.db`` (``paths.store_dir()``), a ``BaseStore``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# ── Vocabulary (kept in lock-step with apps/os/.../deck-types.ts) ─────────────

VALID_AI_MODES = ("inherit", "draft", "approval", "auto")
CONCRETE_AI_MODES = ("draft", "approval", "auto")  # what resolve_ai_mode returns
VALID_AGENT_STATUS = ("idle", "running", "awaiting_approval", "done", "failed")
VALID_PRIORITY = ("low", "normal", "high", "urgent")

# `kind` separates the two populations that share this table. The Kanban's Goals and
# Tasks apps show `card`; the PIM (`/todo`, `navig todo`) shows `todo`. Without it a
# personal errand lands in the desktop board's Backlog column, which is exactly the
# reason people end up with a second task app.
KIND_CARD = "card"
KIND_TODO = "todo"
# v6: an APPOINTMENT is its own kind, never a todo in a "rendezvous" category. It
# reuses `due_at` as its START so the reminder machinery (planned_fires, reschedule,
# board_todo_reminder, /reminders) works on it unchanged.
KIND_APPOINTMENT = "appointment"
VALID_KINDS = (KIND_CARD, KIND_TODO, KIND_APPOINTMENT)
#: The kinds the Planner owns. The PIM getters are scoped to these, and the board
#: (Kanban) routes to `card` only, so neither surface can reach the other's rows.
PIM_KINDS: tuple[str, ...] = (KIND_TODO, KIND_APPOINTMENT)

# Seeded categories, with their emoji. FREE-FORM still: `board_card.category` is plain
# TEXT, a name the operator invents works immediately, and its `board_category` row is
# created lazily. These are what the picker offers on a fresh install.
SEED_CATEGORIES: tuple[str, ...] = ("work", "project", "life", "business")
_SEED_CATEGORY_EMOJI: dict[str, str] = {
    "work": "💼", "project": "🛠", "life": "🌿", "business": "📈",
}

#: The personal list. Every todo with a NULL `list_id` belongs to it, so rows written
#: before v6 need no rewrite.
PERSONAL_LIST_ID = "me"
LIST_KIND_PERSONAL = "personal"
LIST_KIND_BIZ_CHAT = "biz_chat"

# Where a todo came from. An AI guess must never be indistinguishable from something
# the operator typed — that is the difference between a suggestion they can dismiss
# and a task they think they wrote.
VALID_ORIGINS = ("manual", "ai", "agent", "chat", "project")

#: The origins that mean "proposed, not yet agreed to". Every surface that marks a
#: suggestion reads THIS — three places used to decide it separately and two checked
#: only "ai", so the suggestions the system actually produces (`origin="agent"`, from
#: `navig todo scan` and the `task_add` tool) rendered as though the operator had
#: written them.
PROPOSED_ORIGINS = ("ai", "agent")

DEFAULT_STAGES: list[dict[str, Any]] = [
    {"key": "backlog", "label": "Backlog"},
    {"key": "in_progress", "label": "In Progress"},
    {"key": "agent", "label": "Agent"},
    {"key": "done", "label": "Done", "terminal": True},
]
DEFAULT_SETTINGS: dict[str, Any] = {
    "default_ai_mode": "approval",
    "default_auto_advance": False,
    "stages": DEFAULT_STAGES,
}

# Columns a client PATCH is allowed to write (everything else — id, created_at,
# derived fields — is silently ignored, exactly like ScheduledPostStore.update).
_GOAL_WRITABLE = {"title", "description", "color", "space", "status", "sort_order"}

# Standing-goal (pursuit) lifecycle. ``active`` is worked on schedule; ``paused`` keeps
# its history and resumes where it left off; ``done`` = the agent reported the
# objective met; ``stopped`` = a cap ended it (max cycles, or repeated failures).
PURSUIT_ACTIVE = "active"
PURSUIT_PAUSED = "paused"
PURSUIT_DONE = "done"
PURSUIT_STOPPED = "stopped"
PURSUIT_STATES = (PURSUIT_ACTIVE, PURSUIT_PAUSED, PURSUIT_DONE, PURSUIT_STOPPED)
_CARD_WRITABLE = {
    "title", "notes", "stage", "priority", "due_at", "reminder_id",
    "ai_mode", "auto_advance", "mission_id", "agent_status", "agent_result",
    "goal_id", "sort_order",
    # PIM fields. `kind` is DELIBERATELY absent: a card must never be able to mutate
    # into a todo (or back) through a generic PATCH. That is the one invariant the
    # two populations rely on, and a writable `kind` would let any client break it
    # with a field name typo.
    "category", "space", "recur", "remind_before", "origin", "origin_ref",
    # v6 Planner fields. `list_seq` is NOT writable: it is the number people type
    # ("done 2") and must never change under them.
    "list_id", "end_at", "location", "with_whom",
}
_LIST_WRITABLE = {"name", "peer_name", "bcid", "lang", "last_card_msg_id", "sort_order", "archived"}
_CATEGORY_WRITABLE = {"label", "emoji", "color", "sort_order", "archived"}
_SUBTASK_WRITABLE = {"title", "done", "sort_order"}
_CARD_BOOL_COLS = {"auto_advance"}
_SUBTASK_BOOL_COLS = {"done"}


def _row_get(row: Any, key: str, default: Any) -> Any:
    """Read a column that may not exist yet.

    A v2 database that has not been migrated in THIS process still answers reads, and
    ``sqlite3.Row`` raises IndexError for an unknown key rather than returning None. A
    missing PIM column means "this row predates the PIM", which is a default, not an
    error.
    """
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None and default is not None else value


def _encode_leads(leads: Any) -> str:
    """Lead times as JSON. Anything unparseable becomes "no reminders" rather than
    a stored value that later blows up the scheduler."""
    if not leads:
        return ""
    if isinstance(leads, str):
        return leads
    try:
        return json.dumps([str(x) for x in leads])
    except (TypeError, ValueError):
        return ""


def _decode_leads(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class BoardStore(BaseStore):
    SCHEMA_VERSION = 6
    PRAGMAS = {"cache_size": -4000}

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ───────────────────────────────────────────────────────────────

    def _create_schema(self, conn) -> None:  # noqa: ANN001 — sqlite3.Connection
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_goal (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                color        TEXT NOT NULL DEFAULT '#6366f1',
                space        TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                sort_order   INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                -- v5 standing-goal ("pursuit") columns; see _V5_GOAL_COLUMNS.
                objective        TEXT,
                every            TEXT,
                grant_scope      TEXT NOT NULL DEFAULT 'readonly',
                max_cycles       INTEGER NOT NULL DEFAULT 24,
                cycles_run       INTEGER NOT NULL DEFAULT 0,
                fail_streak      INTEGER NOT NULL DEFAULT 0,
                run_state        TEXT,
                cycle_started_at TEXT,
                last_run_at      TEXT,
                last_report      TEXT NOT NULL DEFAULT '',
                last_receipt_id  TEXT
            );

            CREATE TABLE IF NOT EXISTS board_card (
                id            TEXT PRIMARY KEY,
                goal_id       TEXT REFERENCES board_goal(id) ON DELETE SET NULL,
                title         TEXT NOT NULL,
                notes         TEXT NOT NULL DEFAULT '',
                stage         TEXT NOT NULL DEFAULT 'backlog',
                priority      TEXT NOT NULL DEFAULT 'normal',
                due_at        TEXT,
                reminder_id   INTEGER,
                ai_mode       TEXT NOT NULL DEFAULT 'inherit',
                auto_advance  INTEGER NOT NULL DEFAULT 0,
                mission_id    TEXT,
                agent_status  TEXT NOT NULL DEFAULT 'idle',
                agent_result  TEXT NOT NULL DEFAULT '',
                sort_order    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                completed_at  TEXT,
                -- v3: the PIM. See KIND_CARD / KIND_TODO above.
                kind          TEXT NOT NULL DEFAULT 'card',
                category      TEXT NOT NULL DEFAULT '',
                space         TEXT,
                recur         TEXT,
                remind_before TEXT NOT NULL DEFAULT '',
                origin        TEXT NOT NULL DEFAULT 'manual',
                origin_ref    TEXT,
                -- v6: the Planner. See _V6_CARD_COLUMNS.
                list_id       TEXT,
                list_seq      INTEGER,
                created_by    INTEGER,
                end_at        TEXT,
                location      TEXT,
                with_whom     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_card_goal ON board_card(goal_id);
            CREATE INDEX IF NOT EXISTS idx_card_stage ON board_card(stage);
            -- v3 indexes are created by _create_kind_indexes(); see it for why.

            -- card depends_on depends_on_id  (card must wait for depends_on_id)
            CREATE TABLE IF NOT EXISTS board_dep (
                card_id        TEXT NOT NULL REFERENCES board_card(id) ON DELETE CASCADE,
                depends_on_id  TEXT NOT NULL REFERENCES board_card(id) ON DELETE CASCADE,
                PRIMARY KEY (card_id, depends_on_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dep_prereq ON board_dep(depends_on_id);

            CREATE TABLE IF NOT EXISTS board_subtask (
                id          TEXT PRIMARY KEY,
                card_id     TEXT NOT NULL REFERENCES board_card(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                sort_order  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_subtask_card ON board_subtask(card_id);

            CREATE TABLE IF NOT EXISTS board_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id     TEXT NOT NULL,
                from_stage  TEXT,
                to_stage    TEXT NOT NULL,
                actor       TEXT NOT NULL DEFAULT 'user',
                created_at  TEXT NOT NULL
            );

            -- One row per scheduled reminder for a todo. A todo has SEVERAL (one per
            -- lead time), which is why `board_card.reminder_id` -- a single INTEGER --
            -- cannot carry them. Rows are deleted when the todo is edited, completed
            -- or removed; leaving one behind is a ghost ping for a task that is gone,
            -- and the reminder table has no idea what a todo is.
            CREATE TABLE IF NOT EXISTS board_todo_reminder (
                reminder_id  INTEGER PRIMARY KEY,
                card_id      TEXT NOT NULL REFERENCES board_card(id) ON DELETE CASCADE,
                lead         TEXT NOT NULL DEFAULT '',
                fire_at      TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_todo_reminder_card ON board_todo_reminder(card_id);

            -- A source line the operator was offered and said no to. Survives the
            -- card being deleted, which is the whole point: see _migrate_v3_to_v4.
            CREATE TABLE IF NOT EXISTS board_todo_dismissed (
                origin_ref   TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT '',
                dismissed_at TEXT NOT NULL
            );

            -- single-row KV; settings are a JSON blob keyed 'settings'
            CREATE TABLE IF NOT EXISTS board_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._create_planner_tables(conn)
        self._create_kind_indexes(conn)

    @staticmethod
    def _create_planner_tables(conn: sqlite3.Connection) -> None:
        """v6 tables + seeds. Idempotent, so it runs on EVERY open.

        ``BaseStore._init_schema`` stamps a FRESH database at SCHEMA_VERSION without
        running any migration, so anything a fresh install needs must exist here, not
        only in ``_migrate_v5_to_v6``.
        """
        now = _utcnow()
        conn.executescript(
            """
            -- A to-do LIST. 'me' is the operator's own; a Telegram Business chat gets
            -- 'tg:<chat_id>', created lazily. The id is deterministic, so two gateway
            -- processes creating the same list race to one INSERT OR IGNORE, not two
            -- rows.
            CREATE TABLE IF NOT EXISTS board_list (
                id               TEXT PRIMARY KEY,
                kind             TEXT NOT NULL DEFAULT 'personal',
                name             TEXT NOT NULL DEFAULT '',
                peer_name        TEXT NOT NULL DEFAULT '',
                chat_id          INTEGER,
                bcid             TEXT,
                owner_id         INTEGER,
                lang             TEXT NOT NULL DEFAULT 'en',
                last_card_msg_id INTEGER,
                sort_order       INTEGER NOT NULL DEFAULT 0,
                archived         INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );

            -- A category's look. `name` is the KEY board_card.category stores (lower
            -- case, never translated); `label` NULL means "use the localized name".
            CREATE TABLE IF NOT EXISTS board_category (
                name        TEXT PRIMARY KEY,
                label       TEXT,
                emoji       TEXT NOT NULL DEFAULT '',
                color       TEXT NOT NULL DEFAULT '',
                sort_order  INTEGER NOT NULL DEFAULT 0,
                archived    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO board_list(id, kind, name, created_at, updated_at)"
            " VALUES(?, ?, 'Me', ?, ?)",
            (PERSONAL_LIST_ID, LIST_KIND_PERSONAL, now, now),
        )
        for order, name in enumerate(SEED_CATEGORIES):
            conn.execute(
                "INSERT OR IGNORE INTO board_category(name, emoji, sort_order, created_at)"
                " VALUES(?, ?, ?, ?)",
                (name, _SEED_CATEGORY_EMOJI.get(name, ""), order, now),
            )

    @staticmethod
    def _create_kind_indexes(conn: sqlite3.Connection) -> None:
        """Index the v3 columns, but ONLY once they exist.

        ⚠ These cannot live in the CREATE script above. ``BaseStore._init_schema``
        runs ``_create_schema`` BEFORE reading the stored version, so on an existing v2
        database the CREATE TABLE statements are no-ops (IF NOT EXISTS) and the table
        still has no ``kind`` column -- at which point
        ``CREATE INDEX ... ON board_card(kind)`` raises ``no such column: kind`` and
        takes the whole open down. ``IF NOT EXISTS`` does not save it: the index does
        not exist either, so SQLite goes ahead and evaluates the column.

        Called twice on purpose -- from ``_create_schema`` (where a FRESH database
        already has the columns) and from ``_migrate_v2_to_v3`` (right after the ALTERs
        put them there). Both are guarded, both are idempotent.
        """
        columns = BoardStore._column_names(conn, "board_card")
        if "kind" not in columns:
            return
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_card_kind ON board_card(kind);
            -- Every PIM read is "todos in this category" or "todos in this space".
            CREATE INDEX IF NOT EXISTS idx_card_kind_category ON board_card(kind, category);
            -- An AI suggestion must never be proposed twice; this is the dedup key.
            CREATE INDEX IF NOT EXISTS idx_card_origin_ref ON board_card(origin_ref);
            """
        )
        # Same guard, one version later: a v5 database being opened has no list_id yet.
        if "list_id" in columns:
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_card_list ON board_card(list_id, kind);
                CREATE INDEX IF NOT EXISTS idx_card_kind_due ON board_card(kind, due_at);
                """
            )

    # ── Migrations ───────────────────────────────────────────────────────────

    #: Legacy table name -> canonical name. An earlier board implementation wrote
    #: these unprefixed tables; the current store was REWRITTEN with prefixed names
    #: (cc6a4da03 "write the missing BoardStore") and no migration was ever added,
    #: so that data became unreachable in place. Column sets are IDENTICAL between
    #: each pair -- verified against a real 2026-06 database -- which is what makes
    #: adoption a plain copy rather than a mapping.
    #: (legacy table, canonical table, {canonical_column: legacy_column}).
    #: An earlier board implementation wrote these unprefixed tables; the current
    #: store was REWRITTEN with prefixed names (cc6a4da03 "write the missing
    #: BoardStore") and no migration was ever added, so that data became unreachable
    #: in place.
    #:
    #: Measured against a real 2026-06 database: four of the five pairs have
    #: IDENTICAL column sets, and `card_history` differs by exactly one renamed
    #: column. That rename is why this carries an explicit map rather than an
    #: intersection -- a "shared columns only" copy silently omitted `created_at`
    #: and hit `NOT NULL constraint failed`, aborting the whole migration.
    _LEGACY_TABLES: tuple[tuple[str, str, dict[str, str]], ...] = (
        ("goals", "board_goal", {}),
        ("cards", "board_card", {}),
        ("subtasks", "board_subtask", {}),
        ("card_deps", "board_dep", {}),
        ("card_history", "board_history", {"created_at": "ts"}),
    )

    def _migrate(
        self, conn: sqlite3.Connection, from_version: int, to_version: int
    ) -> None:
        """Dispatch incremental steps, failing fast on a missing one.

        Mirrors ``RuntimeStore._migrate``. The fail-fast matters more here than the
        dispatch does: ``BaseStore._migrate`` is a silent ``pass``, so bumping
        SCHEMA_VERSION without this would stamp the new version having applied
        NOTHING -- the store would then read columns that do not exist, and the
        version number would swear everything was fine.
        """
        if from_version >= to_version:
            return
        for version in range(from_version, to_version):
            step_name = f"_migrate_v{version}_to_v{version + 1}"
            step = getattr(self, step_name, None)
            if not callable(step):
                raise RuntimeError(
                    f"BoardStore migration path missing: {version} -> {version + 1}. "
                    f"Implement {step_name}() before upgrading schema version."
                )
            step(conn)

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Adopt the orphaned pre-rename tables, if this database has them."""
        self._adopt_legacy_tables(conn)

    #: v3 columns, in the order they are added. Every one has a DEFAULT so existing
    #: rows are valid the instant the column appears -- an added NOT NULL column with
    #: no default is rejected by SQLite outright.
    _V3_CARD_COLUMNS: tuple[str, ...] = (
        "kind TEXT NOT NULL DEFAULT 'card'",
        "category TEXT NOT NULL DEFAULT ''",
        "space TEXT",
        "recur TEXT",
        "remind_before TEXT NOT NULL DEFAULT ''",
        "origin TEXT NOT NULL DEFAULT 'manual'",
        "origin_ref TEXT",
    )

    def _migrate_v2_to_v3(self, conn: sqlite3.Connection) -> None:
        """Add the PIM columns and the reminder link table.

        ⚠ Every ALTER is wrapped INDIVIDUALLY. ``BaseStore._init_schema`` calls
        ``_create_schema`` BEFORE it reads the stored version, so on a database that
        is merely being *opened* the columns already exist by the time this runs, and
        an unguarded ``ADD COLUMN`` dies with ``duplicate column name`` -- taking the
        whole migration with it and leaving the version stamped at 2 forever.
        `RuntimeStore._migrate_v1_to_v2` has the same shape for the same reason.
        """
        for column in self._V3_CARD_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE board_card ADD COLUMN {column}")  # noqa: S608 — literal
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_todo_reminder (
                reminder_id  INTEGER PRIMARY KEY,
                card_id      TEXT NOT NULL REFERENCES board_card(id) ON DELETE CASCADE,
                lead         TEXT NOT NULL DEFAULT '',
                fire_at      TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_todo_reminder_card ON board_todo_reminder(card_id);
            """
        )
        self._create_kind_indexes(conn)

    def _adopt_legacy_tables(self, conn: sqlite3.Connection) -> None:
        """Copy rows out of the pre-rename tables into the canonical ones.

        ⚠ ``BaseStore._init_schema`` calls ``_create_schema`` BEFORE it reads the
        schema version, so by the time this runs the canonical tables ALWAYS exist
        -- freshly created and empty. The condition is therefore "the canonical
        table is EMPTY", never "the canonical table is missing"; the latter is never
        true and would make this a silent no-op on exactly the databases it exists
        to rescue.

        Copies only into an empty table, and RENAMES rather than drops the legacy
        one: this is the only copy of that data. Re-running is a no-op.

        Each table is attempted independently. One awkward table must never abort
        the others, and must never prevent the version bump -- otherwise a single
        malformed history row keeps the operator's goals and cards invisible
        forever, which is the failure this whole method exists to end.
        """
        for legacy, canonical, renames in self._LEGACY_TABLES:
            try:
                if not self._table_exists(conn, legacy):
                    continue
                if self._row_count(conn, canonical) > 0:
                    continue  # never overwrite live data with an older snapshot

                legacy_cols = set(self._column_names(conn, legacy))
                pairs: list[tuple[str, str]] = []
                for canon_col in self._column_names(conn, canonical):
                    source = renames.get(canon_col, canon_col)
                    if source in legacy_cols:
                        pairs.append((canon_col, source))
                if not pairs:
                    continue

                missing = self._unsatisfied_not_null(
                    conn, canonical, {c for c, _ in pairs}
                )
                if missing:
                    # Copying would violate NOT NULL. Skipping loses nothing: the
                    # legacy table is left intact and named, so the rows remain
                    # recoverable by hand instead of being half-written.
                    logger.warning(
                        "board: not adopting %s -> %s, no source for required "
                        "column(s) %s; legacy table left in place",
                        legacy, canonical, sorted(missing),
                    )
                    continue

                cols = ", ".join(f'"{c}"' for c, _ in pairs)
                srcs = ", ".join(f'"{sql}"' for _, sql in pairs)
                conn.execute(
                    f"INSERT INTO {canonical} ({cols}) SELECT {srcs} FROM {legacy}"  # noqa: S608
                )
                conn.execute(f"ALTER TABLE {legacy} RENAME TO {legacy}_migrated_v1")
            except sqlite3.Error as exc:
                # sqlite3.Error, not OperationalError: the first real run raised
                # IntegrityError, which a narrower clause let escape and abort the
                # entire migration.
                logger.warning("board: adopting %s failed: %s", legacy, exc)
                continue

    @staticmethod
    def _unsatisfied_not_null(
        conn: sqlite3.Connection, table: str, provided: set[str]
    ) -> set[str]:
        """NOT NULL columns with no default that the copy would not fill."""
        missing: set[str] = set()
        for row in conn.execute(f"PRAGMA table_info({table})"):
            name, notnull, default, pk = row[1], row[3], row[4], row[5]
            if notnull and default is None and not pk and name not in provided:
                missing.add(name)
        return missing

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_count(conn: sqlite3.Connection, table: str) -> int:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        except sqlite3.OperationalError:
            return 0

    @staticmethod
    def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    # ── Settings ─────────────────────────────────────────────────────────────

    #: v5 goal columns. A goal with an ``objective`` is a STANDING goal (a pursuit):
    #: NAVIG works it on the ``every`` schedule, one mission per cycle, until the
    #: agent reports it done, ``max_cycles`` is reached, or it fails three cycles in a
    #: row. A goal without one is a plain board lane, exactly as before. Every column
    #: has a DEFAULT or is nullable, so existing rows are valid the moment it appears.
    _V5_GOAL_COLUMNS: tuple[str, ...] = (
        "objective TEXT",
        "every TEXT",
        "grant_scope TEXT NOT NULL DEFAULT 'readonly'",
        "max_cycles INTEGER NOT NULL DEFAULT 24",
        "cycles_run INTEGER NOT NULL DEFAULT 0",
        "fail_streak INTEGER NOT NULL DEFAULT 0",
        "run_state TEXT",
        "cycle_started_at TEXT",
        "last_run_at TEXT",
        "last_report TEXT NOT NULL DEFAULT ''",
        "last_receipt_id TEXT",
    )

    def _migrate_v4_to_v5(self, conn: sqlite3.Connection) -> None:
        """Add the standing-goal columns. Each ALTER guarded, as in v2->v3."""
        for column in self._V5_GOAL_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE board_goal ADD COLUMN {column}")  # noqa: S608 — literal
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    #: v6 columns. All nullable, so existing rows are valid the instant they appear and
    #: a NULL `list_id` simply means the personal list.
    _V6_CARD_COLUMNS: tuple[str, ...] = (
        "list_id TEXT",
        "list_seq INTEGER",
        "created_by INTEGER",
        "end_at TEXT",
        "location TEXT",
        "with_whom TEXT",
    )

    def _migrate_v5_to_v6(self, conn: sqlite3.Connection) -> None:
        """The Planner: lists, categories, and appointments as their own kind.

        Each ALTER guarded (see v2->v3). Then the one DATA move: every todo filed
        under the old "rendezvous" category becomes an appointment. Its id is
        unchanged, so its reminder links -- and every ping already scheduled for it --
        survive. Naturally idempotent: a second run matches nothing.
        """
        for column in self._V6_CARD_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE board_card ADD COLUMN {column}")  # noqa: S608 — literal
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        self._create_planner_tables(conn)
        self._create_kind_indexes(conn)
        conn.execute(
            "UPDATE board_card SET kind = ?, category = ''"
            " WHERE kind = ? AND category = 'rendezvous'",
            (KIND_APPOINTMENT, KIND_TODO),
        )
        # The categories the operator already uses become managed rows, so the
        # Planner's settings show them instead of only the seeds.
        now = _utcnow()
        conn.execute(
            "INSERT OR IGNORE INTO board_category(name, emoji, sort_order, created_at)"
            " SELECT DISTINCT category, '', 100, ? FROM board_card"
            " WHERE kind = ? AND category NOT IN ('', 'rendezvous')",
            (now, KIND_TODO),
        )

    def _migrate_v3_to_v4(self, conn: sqlite3.Connection) -> None:
        """Add the dismissal ledger."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_todo_dismissed (
                origin_ref   TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT '',
                dismissed_at TEXT NOT NULL
            );
            """
        )

    def get_settings(self) -> dict[str, Any]:
        row = self._read_one("SELECT value FROM board_settings WHERE key = 'settings'")
        if not row:
            return dict(DEFAULT_SETTINGS)
        try:
            stored = json.loads(row["value"])
        except (ValueError, TypeError):
            return dict(DEFAULT_SETTINGS)
        # Merge over defaults so a stored partial (or an older shape) always
        # yields every key the frontend reads.
        out = dict(DEFAULT_SETTINGS)
        if isinstance(stored, dict):
            out.update({k: v for k, v in stored.items() if v is not None})
        if not isinstance(out.get("stages"), list) or not out["stages"]:
            out["stages"] = DEFAULT_STAGES
        return out

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        if isinstance(patch, dict):
            if "default_ai_mode" in patch and patch["default_ai_mode"] in CONCRETE_AI_MODES:
                current["default_ai_mode"] = patch["default_ai_mode"]
            if "default_auto_advance" in patch:
                current["default_auto_advance"] = bool(patch["default_auto_advance"])
            if isinstance(patch.get("stages"), list) and patch["stages"]:
                current["stages"] = patch["stages"]
        self._write(
            "INSERT INTO board_settings(key, value) VALUES('settings', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(current),),
        )
        return current

    def _terminal_stages(self) -> set[str]:
        stages = self.get_settings().get("stages", DEFAULT_STAGES)
        terminal = {s["key"] for s in stages if isinstance(s, dict) and s.get("terminal")}
        return terminal or {"done"}

    def resolve_ai_mode(self, ai_mode: str | None) -> str:
        """Map a card's stored mode to a concrete run mode (never 'inherit')."""
        if ai_mode in CONCRETE_AI_MODES:
            return ai_mode
        default = self.get_settings().get("default_ai_mode", "approval")
        return default if default in CONCRETE_AI_MODES else "approval"

    # ── Row → public dict ────────────────────────────────────────────────────

    @staticmethod
    def _goal_to_dict(row) -> dict[str, Any]:  # noqa: ANN001
        return {
            "id": row["id"], "title": row["title"], "description": row["description"],
            "color": row["color"], "space": row["space"], "status": row["status"],
            "sort_order": row["sort_order"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "objective": _row_get(row, "objective", None),
            "every": _row_get(row, "every", None),
            "grant_scope": _row_get(row, "grant_scope", "readonly"),
            "max_cycles": _row_get(row, "max_cycles", 24),
            "cycles_run": _row_get(row, "cycles_run", 0),
            "fail_streak": _row_get(row, "fail_streak", 0),
            "run_state": _row_get(row, "run_state", None),
            "cycle_started_at": _row_get(row, "cycle_started_at", None),
            "last_run_at": _row_get(row, "last_run_at", None),
            "last_report": _row_get(row, "last_report", ""),
            "last_receipt_id": _row_get(row, "last_receipt_id", None),
        }

    @staticmethod
    def _subtask_to_dict(row) -> dict[str, Any]:  # noqa: ANN001
        return {
            "id": row["id"], "card_id": row["card_id"], "title": row["title"],
            "done": bool(row["done"]), "sort_order": row["sort_order"],
        }

    def _card_to_dict(self, row, *, deps: list[str] | None = None, terminal: set[str] | None = None) -> dict[str, Any]:  # noqa: ANN001
        if deps is None:
            deps = [r["depends_on_id"] for r in self._read_all(
                "SELECT depends_on_id FROM board_dep WHERE card_id = ?", (row["id"],))]
        if terminal is None:
            terminal = self._terminal_stages()
        gate = "ready"
        if deps:
            done = {r["id"] for r in self._read_all(
                f"SELECT id FROM board_card WHERE id IN ({','.join('?' * len(deps))})"  # noqa: S608 — ids are our own
                f" AND stage IN ({','.join('?' * len(terminal))})",
                (*deps, *terminal),
            )}
            if any(d not in done for d in deps):
                gate = "waiting"
        return {
            "id": row["id"], "goal_id": row["goal_id"], "title": row["title"],
            "notes": row["notes"], "stage": row["stage"], "priority": row["priority"],
            "due_at": row["due_at"], "reminder_id": row["reminder_id"],
            "ai_mode": row["ai_mode"], "auto_advance": bool(row["auto_advance"]),
            "mission_id": row["mission_id"], "agent_status": row["agent_status"],
            "agent_result": row["agent_result"], "sort_order": row["sort_order"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "completed_at": row["completed_at"], "deps": deps, "gate": gate,
            "kind": _row_get(row, "kind", KIND_CARD),
            "category": _row_get(row, "category", ""),
            "space": _row_get(row, "space", None),
            "recur": _row_get(row, "recur", None),
            "remind_before": _decode_leads(_row_get(row, "remind_before", "")),
            "origin": _row_get(row, "origin", "manual"),
            "origin_ref": _row_get(row, "origin_ref", None),
            "list_id": _row_get(row, "list_id", None) or PERSONAL_LIST_ID,
            "list_seq": _row_get(row, "list_seq", None),
            "created_by": _row_get(row, "created_by", None),
            "end_at": _row_get(row, "end_at", None),
            "location": _row_get(row, "location", None),
            "with_whom": _row_get(row, "with_whom", None),
        }

    # ── Goals ────────────────────────────────────────────────────────────────

    def list_goals(self, *, space: str | None = None) -> list[dict[str, Any]]:
        """Goals, optionally scoped to one project/space.

        ``space`` is the Space id — the same id the Projects hub uses — so a project
        rollup can read only its own goals without a new store or a second query.
        """
        if space is None:
            rows = self._read_all("SELECT * FROM board_goal ORDER BY sort_order, created_at")
        else:
            rows = self._read_all(
                "SELECT * FROM board_goal WHERE space = ? ORDER BY sort_order, created_at",
                (space,),
            )
        return [self._goal_to_dict(r) for r in rows]

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM board_goal WHERE id = ?", (goal_id,))
        return self._goal_to_dict(row) if row else None

    def create_goal(self, title: str, *, description: str = "", color: str = "#6366f1", space: str | None = None) -> dict[str, Any]:
        gid, now = _new_id(), _utcnow()
        nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_goal")
        self._write(
            "INSERT INTO board_goal(id, title, description, color, space, status, sort_order, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (gid, title, description or "", color or "#6366f1", space, int(nxt["n"]) if nxt else 0, now, now),
        )
        return self.get_goal(gid)  # type: ignore[return-value]

    def update_goal(self, goal_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.get_goal(goal_id):
            return None
        sets, params = self._build_update(fields, _GOAL_WRITABLE, set())
        if sets:
            self._write(f"UPDATE board_goal SET {sets}, updated_at = ? WHERE id = ?", (*params, _utcnow(), goal_id))
        return self.get_goal(goal_id)

    def delete_goal(self, goal_id: str) -> None:
        self._write("DELETE FROM board_goal WHERE id = ?", (goal_id,))

    # ── Standing goals (pursuits) ────────────────────────────────────────────
    # Written through their OWN methods, never update_goal(): the deck's generic
    # goal PATCH must not be able to widen a goal's grant or reset its cycle count.

    def create_pursuit(
        self,
        title: str,
        *,
        objective: str,
        every: str,
        grant_scope: str = "readonly",
        max_cycles: int = 24,
        space: str | None = None,
    ) -> dict[str, Any]:
        goal = self.create_goal(title, description=objective, space=space)
        self._write(
            "UPDATE board_goal SET objective = ?, every = ?, grant_scope = ?, max_cycles = ?,"
            " run_state = ?, updated_at = ? WHERE id = ?",
            (objective, every, grant_scope, int(max_cycles), PURSUIT_ACTIVE, _utcnow(), goal["id"]),
        )
        return self.get_goal(goal["id"])  # type: ignore[return-value]

    def list_pursuits(self) -> list[dict[str, Any]]:
        rows = self._read_all(
            "SELECT * FROM board_goal WHERE objective IS NOT NULL ORDER BY created_at"
        )
        return [self._goal_to_dict(r) for r in rows]

    def set_pursuit_state(self, goal_id: str, run_state: str) -> dict[str, Any] | None:
        if run_state not in PURSUIT_STATES:
            raise ValueError(f"unknown pursuit state: {run_state!r}")
        self._write(
            "UPDATE board_goal SET run_state = ?, updated_at = ? WHERE id = ? AND objective IS NOT NULL",
            (run_state, _utcnow(), goal_id),
        )
        return self.get_goal(goal_id)

    def claim_pursuit_cycle(self, goal_id: str, *, now: str, stale_before: str) -> bool:
        """Atomically claim the next cycle of an ACTIVE pursuit. True = it is yours.

        The claim lives in the database row, not in a process: the gateway's cron,
        a manual ``navig goal run``, and a second daemon all compete for the same
        row under BEGIN IMMEDIATE, so exactly one wins. A claim older than
        ``stale_before`` belonged to a run that died mid-cycle and may be taken over
        — otherwise one crash would freeze the goal forever.
        """
        cur = self._write(
            "UPDATE board_goal SET cycle_started_at = ?, updated_at = ?"
            " WHERE id = ? AND objective IS NOT NULL AND run_state = ?"
            " AND (cycle_started_at IS NULL OR cycle_started_at < ?)",
            (now, now, goal_id, PURSUIT_ACTIVE, stale_before),
        )
        return cur.rowcount == 1

    def finish_pursuit_cycle(
        self,
        goal_id: str,
        *,
        succeeded: bool,
        report: str,
        receipt_id: str | None,
        run_state: str,
        finished_at: str,
    ) -> dict[str, Any] | None:
        """Record one finished cycle and release the claim.

        ``run_state`` only replaces ``active``: an operator who paused or stopped the
        goal WHILE this cycle ran keeps that decision rather than having the cycle's
        "still active" silently resume it.
        """
        self._write(
            "UPDATE board_goal SET cycles_run = cycles_run + 1,"
            " fail_streak = CASE WHEN ? THEN 0 ELSE fail_streak + 1 END,"
            " last_report = ?, last_receipt_id = ?, last_run_at = ?,"
            " run_state = CASE WHEN run_state = ? THEN ? ELSE run_state END,"
            " cycle_started_at = NULL, updated_at = ? WHERE id = ?",
            (
                1 if succeeded else 0, report, receipt_id, finished_at,
                PURSUIT_ACTIVE, run_state, finished_at, goal_id,
            ),
        )
        return self.get_goal(goal_id)

    def release_pursuit_claim(self, goal_id: str) -> None:
        """Drop a claim without counting a cycle (the run never started)."""
        self._write(
            "UPDATE board_goal SET cycle_started_at = NULL, updated_at = ? WHERE id = ?",
            (_utcnow(), goal_id),
        )

    # ── Cards ────────────────────────────────────────────────────────────────

    def list_cards(self, *, space: str | None = None) -> list[dict[str, Any]]:
        """Board cards only.

        The `kind` filter is what keeps "Buy milk" out of the desktop Kanban's Backlog
        column. Without it the PIM would flood the board the first time it is used,
        which is precisely why people end up running two task apps.

        ``space`` (a Space/project id) narrows the result to one project's cards; the
        column already existed, this only exposes it as a read filter.
        """
        sql = "SELECT * FROM board_card WHERE kind = ?"
        params: list[Any] = [KIND_CARD]
        if space is not None:
            sql += " AND space = ?"
            params.append(space)
        rows = self._read_all(f"{sql} ORDER BY sort_order, created_at", tuple(params))
        terminal = self._terminal_stages()
        # Fetch all edges once, group by card — avoids an N+1 per-card query.
        edges: dict[str, list[str]] = {}
        for e in self._read_all("SELECT card_id, depends_on_id FROM board_dep"):
            edges.setdefault(e["card_id"], []).append(e["depends_on_id"])
        return [self._card_to_dict(r, deps=edges.get(r["id"], []), terminal=terminal) for r in rows]

    def get_card(self, card_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM board_card WHERE id = ?", (card_id,))
        return self._card_to_dict(row) if row else None

    def claim_card(self, card_id: str, holder: str) -> bool:
        """Atomically claim a card for a session. True = the claim is yours.

        The same single-statement CAS as ``claim_pursuit_cycle``: the UPDATE's
        WHERE clause is the lock, so two sessions racing this row get exactly
        one ``rowcount == 1``. A session re-claiming its own card succeeds
        (idempotent); claiming a card another session holds — or one an AI run
        left ``running`` — loses. ``holder`` is the claiming agent session id
        (``navig.agent_registry``); ``release_card`` clears it. There is no
        claim-timestamp column on purpose: staleness is the orchestrator's
        adjudication (``release --force``), not a clock hidden in the row.
        """
        cur = self._write(
            "UPDATE board_card SET agent_status = 'running', origin_ref = ?, updated_at = ?"
            " WHERE id = ? AND kind = ? AND (agent_status = 'idle' OR origin_ref = ?)",
            (holder, _utcnow(), card_id, KIND_CARD, holder),
        )
        return cur.rowcount == 1

    def release_card(self, card_id: str, holder: str, *, force: bool = False) -> bool:
        """Release a card claim. True = something was released.

        Only the holder may release; ``force`` is the orchestrator's
        adjudication for an abandoned claim (the caller records it in the task
        record's landing log — the store keeps no claim timestamp to age one
        out on).
        """
        cur = self._write(
            "UPDATE board_card SET agent_status = 'idle', origin_ref = NULL, updated_at = ?"
            " WHERE id = ? AND kind = ? AND (origin_ref = ? OR ?)",
            (_utcnow(), card_id, KIND_CARD, holder, 1 if force else 0),
        )
        return cur.rowcount == 1

    def card_history(self, card_id: str) -> list[dict[str, Any]]:
        """Stage transitions for one card, oldest first.

        The audit the HTTP surface never exposed: ``recent_history`` serves the
        briefing only, and a PATCH writes no row at all (stage moves do).
        """
        rows = self._read_all(
            "SELECT from_stage, to_stage, actor, created_at FROM board_history"
            " WHERE card_id = ? ORDER BY id",
            (card_id,),
        )
        return [dict(r) for r in rows]

    def card_subtasks(self, card_id: str) -> list[dict[str, Any]]:
        """One card's checklist rows — the per-card read ``_list_subtasks`` never offered."""
        rows = self._read_all(
            "SELECT * FROM board_subtask WHERE card_id = ? ORDER BY sort_order, id",
            (card_id,),
        )
        return [self._subtask_to_dict(r) for r in rows]

    def create_card(self, title: str, *, goal_id: str | None = None, notes: str = "", stage: str = "backlog",
                    priority: str = "normal", due_at: str | None = None, reminder_id: int | None = None,
                    ai_mode: str = "inherit", auto_advance: bool = False, mission_id: str | None = None,
                    space: str | None = None) -> dict[str, Any]:
        cid, now = _new_id(), _utcnow()
        nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_card WHERE stage = ?", (stage,))
        completed = now if stage in self._terminal_stages() else None
        self._write(
            "INSERT INTO board_card(id, goal_id, title, notes, stage, priority, due_at, reminder_id, ai_mode,"
            " auto_advance, mission_id, agent_status, agent_result, sort_order, created_at, updated_at, completed_at,"
            " space)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', '', ?, ?, ?, ?, ?)",
            (cid, goal_id, title, notes or "", stage or "backlog", priority or "normal", due_at, reminder_id,
             ai_mode if ai_mode in VALID_AI_MODES else "inherit", 1 if auto_advance else 0, mission_id,
             int(nxt["n"]) if nxt else 0, now, now, completed, space),
        )
        return self.get_card(cid)  # type: ignore[return-value]

    def create_chain(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create N cards and wire them into a dependency PIPELINE — each card
        depends on the one before it, so card 2 starts ``waiting`` until card 1
        is done. Same cycle-safe edge as ``add_dependency`` (a fresh linear chain
        can't cycle, but we still go through the guarded path)."""
        created: list[dict[str, Any]] = []
        prev_id: str | None = None
        for spec in specs:
            spec = dict(spec)
            title = spec.pop("title", "").strip() or "Untitled"
            card = self.create_card(title, **{k: v for k, v in spec.items() if k in {
                "goal_id", "notes", "stage", "priority", "due_at", "reminder_id",
                "ai_mode", "auto_advance", "mission_id", "space",
            }})
            if prev_id is not None:
                self.add_dependency(card["id"], prev_id)
            prev_id = card["id"]
            created.append(self.get_card(card["id"]))  # type: ignore[arg-type]
        return created

    # The three Kanban mutators are scoped to kind='card'. They used to accept ANY id,
    # so a todo's id sent to the board routes edited or deleted it behind the PIM's
    # back -- skipping the reminder cancellation, which leaves ghost pings for a task
    # that is gone or moved.
    def update_card(self, card_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self._read_one(
            "SELECT id FROM board_card WHERE id = ? AND kind = ?", (card_id, KIND_CARD)
        ):
            return None
        sets, params = self._build_update(fields, _CARD_WRITABLE, _CARD_BOOL_COLS)
        if sets:
            self._write(
                f"UPDATE board_card SET {sets}, updated_at = ? WHERE id = ? AND kind = ?",  # noqa: S608
                (*params, _utcnow(), card_id, KIND_CARD),
            )
        return self.get_card(card_id)

    def delete_card(self, card_id: str) -> None:
        self._write("DELETE FROM board_card WHERE id = ? AND kind = ?", (card_id, KIND_CARD))

    def move_card(self, card_id: str, stage: str, *, sort_order: int | None = None, actor: str = "user") -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT stage FROM board_card WHERE id = ? AND kind = ?", (card_id, KIND_CARD)
        )
        if not row:
            return None
        from_stage = row["stage"]
        terminal = self._terminal_stages()
        now = _utcnow()
        # completed_at is set on entering a terminal stage, cleared on leaving one.
        if stage in terminal:
            completed_sql, completed_val = "completed_at = ?", now
        else:
            completed_sql, completed_val = "completed_at = NULL", None
        if sort_order is None:
            nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_card WHERE stage = ?", (stage,))
            sort_order = int(nxt["n"]) if nxt else 0
        params: list[Any] = [stage, sort_order]
        if completed_val is not None:
            params.append(completed_val)
        params += [now, card_id]
        self._write(
            f"UPDATE board_card SET stage = ?, sort_order = ?, {completed_sql}, updated_at = ? WHERE id = ?",
            tuple(params),
        )
        if from_stage != stage:
            self._write(
                "INSERT INTO board_history(card_id, from_stage, to_stage, actor, created_at) VALUES(?, ?, ?, ?, ?)",
                (card_id, from_stage, stage, actor if actor in ("user", "agent") else "user", now),
            )
        return self.get_card(card_id)

    # ── Todos (the PIM) ──────────────────────────────────────────────────────
    #
    # A todo is a card with `kind='todo'` and, normally, no goal. Its lifecycle is
    # DERIVED, never a stage:
    #
    #     done       completed_at is not null
    #     overdue    due_at < now
    #     scheduled  due_at is not null
    #     inbox      due_at is null
    #
    # Deriving it is not a shortcut. `stage` values are user-editable through
    # `board_settings`, so renaming the "done" column in the Deck would silently stop
    # todos completing; `completed_at` cannot be renamed by anyone. It is also exactly
    # the flow the operator described -- capture into the inbox, give it a date, and
    # it becomes scheduled -- with no column to drag anything between.

    def create_todo(
        self,
        title: str,
        *,
        category: str = "",
        due_at: str | None = None,
        notes: str = "",
        priority: str = "normal",
        recur: str | None = None,
        remind_before: Sequence[str] = (),
        space: str | None = None,
        goal_id: str | None = None,
        origin: str = "manual",
        origin_ref: str | None = None,
        kind: str = KIND_TODO,
        list_id: str | None = None,
        created_by: int | None = None,
        end_at: str | None = None,
        location: str | None = None,
        with_whom: str | None = None,
    ) -> dict[str, Any]:
        """Capture one task — or, with ``kind="appointment"``, one appointment.

        Everything but the title is optional — that is the point. A todo with no date
        is not an incomplete todo; it is an INBOX item, which is the state most tasks
        are captured in and the one a capture flow must not demand you resolve up front.

        ``list_seq`` — the number people type in a shared list ("done 2") — is assigned
        INSIDE the INSERT, which runs under one BEGIN IMMEDIATE, so two processes adding
        to the same list at once cannot hand out the same number.
        """
        if kind not in PIM_KINDS:
            raise ValueError(f"not a Planner kind: {kind!r}")
        cid, now = _new_id(), _utcnow()
        list_key = None if (list_id in (None, "", PERSONAL_LIST_ID)) else str(list_id)
        nxt = self._read_one(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_card WHERE kind = ?",
            (kind,),
        )
        self._write(
            "INSERT INTO board_card(id, goal_id, title, notes, stage, priority, due_at,"
            " reminder_id, ai_mode, auto_advance, mission_id, agent_status, agent_result,"
            " sort_order, created_at, updated_at, completed_at,"
            " kind, category, space, recur, remind_before, origin, origin_ref,"
            " list_id, list_seq, created_by, end_at, location, with_whom)"
            " VALUES(?, ?, ?, ?, 'inbox', ?, ?, NULL, 'inherit', 0, NULL, 'idle', '',"
            " ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,"
            " ?, (SELECT COALESCE(MAX(list_seq), 0) + 1 FROM board_card WHERE list_id IS ?),"
            " ?, ?, ?, ?)",
            (
                cid, goal_id, title, notes or "",
                priority if priority in VALID_PRIORITY else "normal",
                due_at,
                int(nxt["n"]) if nxt else 0, now, now,
                kind,
                (category or "").strip().lower(),
                space,
                recur,
                _encode_leads(remind_before),
                origin if origin in VALID_ORIGINS else "manual",
                origin_ref,
                list_key, list_key,
                created_by, end_at, location, with_whom,
            ),
        )
        return self.get_todo(cid)  # type: ignore[return-value]

    def get_todo(self, card_id: str) -> dict[str, Any] | None:
        """A Planner row — todo OR appointment. Never a Kanban card."""
        row = self._read_one(
            "SELECT * FROM board_card WHERE id = ? AND kind IN (?, ?)",
            (card_id, *PIM_KINDS),
        )
        return self._card_to_dict(row) if row else None

    def move_to_list(self, card_id: str, list_id: str | None) -> dict[str, Any] | None:
        """Move a Planner row to another list, giving it the NEXT number there — its
        old number belongs to the old list and could collide in the new one."""
        list_key = None if (list_id in (None, "", PERSONAL_LIST_ID)) else str(list_id)
        self._write(
            "UPDATE board_card SET list_id = ?,"
            " list_seq = (SELECT COALESCE(MAX(list_seq), 0) + 1 FROM board_card WHERE list_id IS ?),"
            " updated_at = ? WHERE id = ? AND kind IN (?, ?)",
            (list_key, list_key, _utcnow(), card_id, *PIM_KINDS),
        )
        return self.get_todo(card_id)

    def get_by_seq(self, list_id: str | None, seq: int) -> dict[str, Any] | None:
        """The item people call "#2" in a list."""
        list_key = None if (list_id in (None, "", PERSONAL_LIST_ID)) else str(list_id)
        row = self._read_one(
            "SELECT * FROM board_card WHERE list_id IS ? AND list_seq = ? AND kind IN (?, ?)",
            (list_key, int(seq), *PIM_KINDS),
        )
        return self._card_to_dict(row) if row else None

    def list_todos(
        self,
        *,
        category: str | None = None,
        space: str | None = None,
        origin: str | None = None,
        include_done: bool = False,
        list_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Open todos, soonest first, undated last.

        The ordering IS the view: overdue and today rise to the top, and the inbox
        settles at the bottom where it reads as a to-sort pile rather than a backlog.
        `NULLS LAST` is spelled with a CASE because SQLite only learned the keyword in
        3.30 and this has to run on whatever Python the operator installed.
        """
        clauses = ["kind = ?"]
        params: list[Any] = [KIND_TODO]
        if not include_done:
            clauses.append("completed_at IS NULL")
        if category is not None:
            clauses.append("category = ?")
            params.append(category.strip().lower())
        if space is not None:
            clauses.append("space = ?")
            params.append(space)
        if origin is not None:
            clauses.append("origin = ?")
            params.append(origin)
        if list_id is not None:
            clauses.append("list_id IS ?")
            params.append(None if list_id in ("", PERSONAL_LIST_ID) else list_id)
        rows = self._read_all(
            f"SELECT * FROM board_card WHERE {' AND '.join(clauses)}"  # noqa: S608 — clauses are literals
            " ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, created_at",
            tuple(params),
        )
        terminal = self._terminal_stages()
        return [self._card_to_dict(r, deps=[], terminal=terminal) for r in rows]

    def list_appointments(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        include_done: bool = False,
        origin: str | None = None,
    ) -> list[dict[str, Any]]:
        """Appointments by start time. ``start``/``end`` are UTC ``…Z`` strings that
        compare as text against ``due_at`` — the same shape every writer uses."""
        clauses = ["kind = ?"]
        params: list[Any] = [KIND_APPOINTMENT]
        if not include_done:
            clauses.append("completed_at IS NULL")
        if start is not None:
            clauses.append("due_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("due_at < ?")
            params.append(end)
        if origin is not None:
            clauses.append("origin = ?")
            params.append(origin)
        rows = self._read_all(
            f"SELECT * FROM board_card WHERE {' AND '.join(clauses)}"  # noqa: S608 — clauses are literals
            " ORDER BY due_at, created_at",
            tuple(params),
        )
        terminal = self._terminal_stages()
        return [self._card_to_dict(r, deps=[], terminal=terminal) for r in rows]

    def update_todo(self, card_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.get_todo(card_id):
            return None
        patch = dict(fields)
        if "remind_before" in patch:
            patch["remind_before"] = _encode_leads(patch["remind_before"])
        if "category" in patch and isinstance(patch["category"], str):
            patch["category"] = patch["category"].strip().lower()
        if "list_id" in patch and patch["list_id"] in ("", PERSONAL_LIST_ID):
            patch["list_id"] = None
        sets, params = self._build_update(patch, _CARD_WRITABLE, _CARD_BOOL_COLS)
        if sets:
            self._write(
                f"UPDATE board_card SET {sets}, updated_at = ? WHERE id = ? AND kind IN (?, ?)",  # noqa: S608
                (*params, _utcnow(), card_id, *PIM_KINDS),
            )
        return self.get_todo(card_id)

    def complete_todo(self, card_id: str) -> dict[str, Any] | None:
        """Tick it off — or, for a recurring task, roll it to the next occurrence.

        A recurring todo is never "completed": completing it would take it off the
        list forever, which is the opposite of what a recurrence is for. Its due date
        advances and it stays open. The caller re-schedules the reminders, because
        this store deliberately knows nothing about the reminder table's contents.

        Returns the updated todo. A recurring one comes back with a NEW `due_at` and
        `completed_at` still null, which is how the caller tells the two apart.
        """
        todo = self.get_todo(card_id)
        if todo is None:
            return None
        now = _utcnow()
        recur = todo.get("recur")
        if recur and todo.get("due_at"):
            nxt = self._next_occurrence(str(todo["due_at"]), str(recur))
            if nxt is not None:
                self._write(
                    "UPDATE board_card SET due_at = ?, updated_at = ? WHERE id = ?",
                    (nxt, now, card_id),
                )
                self._log_history(card_id, todo.get("stage"), "recurred")
                return self.get_todo(card_id)
        self._write(
            "UPDATE board_card SET completed_at = ?, stage = 'done', updated_at = ? WHERE id = ?",
            (now, now, card_id),
        )
        self._log_history(card_id, todo.get("stage"), "done")
        return self.get_todo(card_id)

    def reopen_todo(self, card_id: str) -> dict[str, Any] | None:
        """Undo a completion. Ticking the wrong row is the most common slip on a
        phone keyboard, and a list you cannot un-tick is one you stop trusting."""
        if not self.get_todo(card_id):
            return None
        stage = "scheduled" if (self.get_todo(card_id) or {}).get("due_at") else "inbox"
        self._write(
            "UPDATE board_card SET completed_at = NULL, stage = ?, updated_at = ? WHERE id = ?",
            (stage, _utcnow(), card_id),
        )
        self._log_history(card_id, "done", stage)
        return self.get_todo(card_id)

    def delete_todo(self, card_id: str, *, dismiss: bool = True) -> bool:
        """Remove a todo. Returns whether it existed.

        Scoped by `kind` so a stray id can never delete a BOARD card through the PIM's
        surfaces — the two populations share a table and a mis-scoped delete here
        would take out a real project task with no confirmation.

        A todo that came from a SOURCE (an AI or agent suggestion) leaves a dismissal
        record behind. Without it, deleting a suggestion deletes the only evidence it
        was ever offered, and the next scan proposes the identical line again — which
        teaches the operator that dismissing does nothing.

        ``dismiss=False`` is a rollback, not a dismissal: the suggestion never reached
        the operator, so the next delivery of the same source must offer it again.
        """
        todo = self.get_todo(card_id)
        if todo is None:
            return False
        origin_ref = todo.get("origin_ref")
        if origin_ref and dismiss:
            self._write(
                "INSERT OR REPLACE INTO board_todo_dismissed(origin_ref, title, dismissed_at)"
                " VALUES(?, ?, ?)",
                (str(origin_ref), str(todo.get("title") or ""), _utcnow()),
            )
        cursor = self._write(
            "DELETE FROM board_card WHERE id = ? AND kind IN (?, ?)", (card_id, *PIM_KINDS)
        )
        return cursor.rowcount > 0

    def todo_categories(self) -> list[dict[str, Any]]:
        """Every category with an open todo, plus the seeds, with counts.

        Seeds are always present so the picker is never empty on a fresh install; a
        seed with nothing in it shows a zero rather than being hidden, because an
        empty category the operator can file into is more useful than one that only
        appears once it already has something in it.
        """
        rows = self._read_all(
            "SELECT category, COUNT(*) AS n FROM board_card"
            " WHERE kind = ? AND completed_at IS NULL GROUP BY category",
            (KIND_TODO,),
        )
        counts = {str(r["category"] or ""): int(r["n"]) for r in rows}
        managed = self.list_categories()
        names = [c["name"] for c in managed]
        # A category used on a row but never given a managed row (written by an older
        # version, or by a client that bypassed the Planner) still shows.
        names += sorted(c for c in counts if c and c not in names)
        out = [{"name": n, "open": counts.get(n, 0)} for n in names]
        if counts.get(""):
            out.append({"name": "", "open": counts[""]})
        return out

    # ── Categories (v6) ──────────────────────────────────────────────────────

    def list_categories(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM board_category"
        if not include_archived:
            sql += " WHERE archived = 0"
        rows = self._read_all(sql + " ORDER BY sort_order, name")
        return [self._category_to_dict(r) for r in rows]

    def get_category(self, name: str) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT * FROM board_category WHERE name = ?", ((name or "").strip().lower(),)
        )
        return self._category_to_dict(row) if row else None

    def ensure_category(self, name: str, *, emoji: str = "", label: str | None = None) -> dict[str, Any] | None:
        """Create the category if it is new. Returns it, or None for an empty name."""
        key = (name or "").strip().lower()
        if not key:
            return None
        nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_category")
        self._write(
            "INSERT OR IGNORE INTO board_category(name, label, emoji, sort_order, created_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (key, label, emoji or "", int(nxt["n"]) if nxt else 0, _utcnow()),
        )
        return self.get_category(key)

    def update_category(self, name: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        key = (name or "").strip().lower()
        if self.get_category(key) is None:
            return None
        sets, params = self._build_update(fields, _CATEGORY_WRITABLE, {"archived"})
        if sets:
            self._write(f"UPDATE board_category SET {sets} WHERE name = ?", (*params, key))  # noqa: S608
        return self.get_category(key)

    def rename_category(self, old: str, new: str) -> dict[str, Any] | None:
        """Rename a category AND every row filed under it, in one transaction.

        Returns None when ``old`` does not exist; raises ValueError when ``new`` is
        empty or already taken (merging two categories is a different operation).
        """
        src = (old or "").strip().lower()
        dst = (new or "").strip().lower()
        if not dst:
            raise ValueError("a category needs a name")
        if self.get_category(src) is None:
            return None
        if dst == src:
            return self.get_category(src)
        if self.get_category(dst) is not None:
            raise ValueError(f"category {dst!r} already exists")
        self._write_statements([
            ("UPDATE board_category SET name = ?, label = NULL WHERE name = ?", (dst, src)),
            ("UPDATE board_card SET category = ? WHERE category = ? AND kind IN (?, ?)",
             (dst, src, *PIM_KINDS)),
        ])
        return self.get_category(dst)

    def category_in_use(self, name: str) -> int:
        row = self._read_one(
            "SELECT COUNT(*) AS n FROM board_card WHERE category = ? AND kind IN (?, ?)",
            ((name or "").strip().lower(), *PIM_KINDS),
        )
        return int(row["n"]) if row else 0

    def delete_category(self, name: str) -> bool:
        """Delete an UNUSED category. One still filed on a row is archived instead —
        deleting it would leave rows pointing at a name no settings screen shows."""
        key = (name or "").strip().lower()
        if self.category_in_use(key):
            raise ValueError("category is in use; archive it instead")
        cursor = self._write("DELETE FROM board_category WHERE name = ?", (key,))
        return cursor.rowcount > 0

    def reorder_categories(self, names: Sequence[str]) -> list[dict[str, Any]]:
        self._write_many(
            "UPDATE board_category SET sort_order = ? WHERE name = ?",
            [(i, str(n).strip().lower()) for i, n in enumerate(names)],
        )
        return self.list_categories(include_archived=True)

    @staticmethod
    def _category_to_dict(row: Any) -> dict[str, Any]:
        return {
            "name": row["name"], "label": row["label"], "emoji": row["emoji"],
            "color": row["color"], "sort_order": row["sort_order"],
            "archived": bool(row["archived"]),
        }

    # ── Lists (v6) ───────────────────────────────────────────────────────────

    def ensure_list(
        self,
        list_id: str,
        *,
        kind: str = LIST_KIND_BIZ_CHAT,
        name: str = "",
        peer_name: str = "",
        chat_id: int | None = None,
        bcid: str | None = None,
        owner_id: int | None = None,
        lang: str = "en",
    ) -> dict[str, Any]:
        """Create the list if new; refresh what may change (bcid, peer name) if not.

        INSERT OR IGNORE on a deterministic id, so two processes calling this for the
        same chat at once end with ONE row.
        """
        now = _utcnow()
        self._write(
            "INSERT OR IGNORE INTO board_list(id, kind, name, peer_name, chat_id, bcid,"
            " owner_id, lang, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (list_id, kind, name, peer_name, chat_id, bcid, owner_id, lang or "en", now, now),
        )
        refresh: dict[str, Any] = {}
        if bcid:
            refresh["bcid"] = bcid
        if peer_name:
            refresh["peer_name"] = peer_name
        if refresh:
            self.update_list(list_id, refresh)
        return self.get_list(list_id)  # type: ignore[return-value]

    def get_list(self, list_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM board_list WHERE id = ?", (list_id,))
        return self._list_to_dict(row) if row else None

    def list_lists(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM board_list"
        if not include_archived:
            sql += " WHERE archived = 0"
        rows = self._read_all(sql + " ORDER BY CASE WHEN id = 'me' THEN 0 ELSE 1 END, sort_order, created_at")
        counts = {
            (r["list_id"] or PERSONAL_LIST_ID): int(r["n"])
            for r in self._read_all(
                "SELECT list_id, COUNT(*) AS n FROM board_card"
                " WHERE kind = ? AND completed_at IS NULL GROUP BY list_id",
                (KIND_TODO,),
            )
        }
        out = []
        for r in rows:
            item = self._list_to_dict(r)
            item["open"] = counts.get(item["id"], 0)
            out.append(item)
        return out

    def update_list(self, list_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if self.get_list(list_id) is None:
            return None
        sets, params = self._build_update(fields, _LIST_WRITABLE, {"archived"})
        if sets:
            self._write(
                f"UPDATE board_list SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608
                (*params, _utcnow(), list_id),
            )
        return self.get_list(list_id)

    @staticmethod
    def _list_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "kind": row["kind"], "name": row["name"],
            "peer_name": row["peer_name"], "chat_id": row["chat_id"], "bcid": row["bcid"],
            "owner_id": row["owner_id"], "lang": row["lang"],
            "last_card_msg_id": row["last_card_msg_id"], "sort_order": row["sort_order"],
            "archived": bool(row["archived"]),
        }

    def todo_exists_for_source(self, origin_ref: str) -> bool:
        """Has this source line already become a todo — accepted OR dismissed?

        The dedup key for AI suggestions. It must match a DISMISSED suggestion too,
        or the same checkbox is proposed again the next time the scan runs, which is
        how a helpful assistant turns into a nag.
        """
        row = self._read_one(
            "SELECT 1 AS hit FROM board_card WHERE origin_ref = ? LIMIT 1", (origin_ref,)
        )
        if row is not None:
            return True
        dismissed = self._read_one(
            "SELECT 1 AS hit FROM board_todo_dismissed WHERE origin_ref = ? LIMIT 1",
            (origin_ref,),
        )
        return dismissed is not None

    # ── Todo reminders ───────────────────────────────────────────────────────

    def link_reminder(self, card_id: str, reminder_id: int, *, lead: str, fire_at: str) -> None:
        """Record that `reminder_id` belongs to this todo."""
        self._write(
            "INSERT OR REPLACE INTO board_todo_reminder(reminder_id, card_id, lead, fire_at, created_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (int(reminder_id), card_id, lead, fire_at, _utcnow()),
        )

    def todo_reminder_ids(self, card_id: str) -> list[int]:
        return [
            int(r["reminder_id"])
            for r in self._read_all(
                "SELECT reminder_id FROM board_todo_reminder WHERE card_id = ? ORDER BY fire_at",
                (card_id,),
            )
        ]

    def reminder_links(self, reminder_ids: list[int]) -> dict[int, dict[str, Any]]:
        """``reminder_id → {card_id, lead, title}`` for the ids that belong to a todo.

        One query for the whole list, so the `/reminders` view can name every ping's
        task without a read per row. Ids with no link (a `/remindme`) are absent.
        """
        ids = [int(i) for i in reminder_ids]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self._read_all(
            "SELECT l.reminder_id, l.card_id, l.lead, c.title"
            " FROM board_todo_reminder l LEFT JOIN board_card c ON c.id = l.card_id"
            f" WHERE l.reminder_id IN ({marks})",
            tuple(ids),
        )
        return {
            int(r["reminder_id"]): {
                "card_id": r["card_id"],
                "lead": r["lead"] or "",
                "title": r["title"] or "",
            }
            for r in rows
        }

    def reminder_is_todo(self, reminder_id: int) -> bool:
        """Does this reminder row belong to a todo?

        The reverse of `todo_reminder_ids`, for the delivery side: a reminder carries
        no idea what created it, so this link table is the only way to tell a todo's
        ping apart from a `/remindme` one — which is what lets the Todo extension
        switch stop its own reminders without touching anybody else's.
        """
        row = self._read_one(
            "SELECT 1 AS hit FROM board_todo_reminder WHERE reminder_id = ? LIMIT 1",
            (int(reminder_id),),
        )
        return row is not None

    def unlink_reminders(self, card_id: str) -> list[int]:
        """Forget this todo's reminders and return their ids, so the caller can cancel
        them in the reminder table.

        Returning the ids rather than cancelling here is deliberate: this store must
        not import the reminder store. But it means the CALLER owns the cancellation —
        drop it and you get ghost pings for a task that no longer exists, which is the
        single most likely live bug in this feature.
        """
        ids = self.todo_reminder_ids(card_id)
        self._write("DELETE FROM board_todo_reminder WHERE card_id = ?", (card_id,))
        return ids

    @staticmethod
    def _next_occurrence(due_at: str, recur: str) -> str | None:
        """Advance an ISO due date by one period, on the LOCAL wall clock.

        "Every monday 10:30" means 10:30 every Monday. Advancing in UTC kept the UTC
        instant's time of day, so across a daylight-saving change the task slid to
        09:30 or 11:30 and stayed there.

        Returns None rather than raising for an unparseable stored value: a corrupt
        `due_at` must not make a task impossible to tick off.
        """
        from datetime import datetime as _dt  # noqa: PLC0415 — keeps `navig help` fast

        try:
            from navig.pim.clock import LOCAL  # noqa: PLC0415
            from navig.pim.dates import advance  # noqa: PLC0415

            base = _dt.fromisoformat(due_at.replace("Z", "+00:00")).astimezone(LOCAL)
            nxt = advance(base, recur)
        except Exception:  # noqa: BLE001 — a bad stored value must not block completion
            logger.warning("todo recurrence %r could not advance %r", recur, due_at)
            return None
        return nxt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _log_history(self, card_id: str, from_stage: Any, to_stage: str) -> None:
        self._write(
            "INSERT INTO board_history(card_id, from_stage, to_stage, actor, created_at)"
            " VALUES(?, ?, ?, 'user', ?)",
            (card_id, from_stage, to_stage, _utcnow()),
        )

    # ── Dependencies (DAG, cycle-checked) ────────────────────────────────────

    def add_dependency(self, card_id: str, depends_on_id: str) -> bool:
        """Add ``card_id depends_on depends_on_id``. Returns False (→ 409) for a
        self-edge or any edge that would introduce a cycle."""
        if card_id == depends_on_id:
            return False
        if not self._read_one("SELECT id FROM board_card WHERE id = ?", (card_id,)):
            return False
        if not self._read_one("SELECT id FROM board_card WHERE id = ?", (depends_on_id,)):
            return False
        # A cycle would form iff depends_on_id already (transitively) depends on
        # card_id — i.e. card_id is reachable following depends_on edges from
        # depends_on_id.
        if self._reaches(depends_on_id, card_id):
            return False
        self._write(
            "INSERT OR IGNORE INTO board_dep(card_id, depends_on_id) VALUES(?, ?)",
            (card_id, depends_on_id),
        )
        return True

    def remove_dependency(self, card_id: str, depends_on_id: str) -> None:
        self._write("DELETE FROM board_dep WHERE card_id = ? AND depends_on_id = ?", (card_id, depends_on_id))

    def _reaches(self, start: str, target: str) -> bool:
        """Is `target` reachable from `start` by following depends_on edges?"""
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for r in self._read_all("SELECT depends_on_id FROM board_dep WHERE card_id = ?", (cur,)):
                stack.append(r["depends_on_id"])
        return False

    def unlock_after_done(self, card_id: str) -> list[dict[str, Any]]:
        """`card_id` just reached a terminal stage. Return its direct dependents
        that are NOW fully unblocked (every prerequisite complete) — the cascade
        driver runs the auto-advancing ones."""
        terminal = self._terminal_stages()
        dependents = self._read_all("SELECT card_id FROM board_dep WHERE depends_on_id = ?", (card_id,))
        out: list[dict[str, Any]] = []
        for d in dependents:
            card = self.get_card(d["card_id"])
            if card and card["gate"] == "ready" and card["stage"] not in terminal:
                out.append(card)
        return out

    # ── Subtasks ─────────────────────────────────────────────────────────────

    def add_subtask(self, card_id: str, title: str) -> dict[str, Any]:
        sid = _new_id()
        nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_subtask WHERE card_id = ?", (card_id,))
        self._write(
            "INSERT INTO board_subtask(id, card_id, title, done, sort_order) VALUES(?, ?, ?, 0, ?)",
            (sid, card_id, title, int(nxt["n"]) if nxt else 0),
        )
        row = self._read_one("SELECT * FROM board_subtask WHERE id = ?", (sid,))
        return self._subtask_to_dict(row)  # type: ignore[arg-type]

    def update_subtask(self, subtask_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self._read_one("SELECT id FROM board_subtask WHERE id = ?", (subtask_id,)):
            return None
        sets, params = self._build_update(fields, _SUBTASK_WRITABLE, _SUBTASK_BOOL_COLS)
        if sets:
            self._write(f"UPDATE board_subtask SET {sets} WHERE id = ?", (*params, subtask_id))
        row = self._read_one("SELECT * FROM board_subtask WHERE id = ?", (subtask_id,))
        return self._subtask_to_dict(row) if row else None

    def delete_subtask(self, subtask_id: str) -> None:
        self._write("DELETE FROM board_subtask WHERE id = ?", (subtask_id,))

    def _list_subtasks(self) -> list[dict[str, Any]]:
        rows = self._read_all("SELECT * FROM board_subtask ORDER BY card_id, sort_order")
        return [self._subtask_to_dict(r) for r in rows]

    # ── History ──────────────────────────────────────────────────────────────

    def recent_history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._read_all(
            "SELECT id, card_id, from_stage, to_stage, actor, created_at "
            "FROM board_history ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [dict(r) for r in rows]

    # ── Snapshot (the board GET) ─────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        # Built from list_cards()/list_goals(), both of which are kind-scoped, so the
        # desktop board never receives a personal errand. Asserted by
        # tests/store/test_board_todos.py::test_the_kanban_never_sees_a_todo.
        return {
            "goals": self.list_goals(),
            "cards": self.list_cards(),
            "subtasks": self._list_subtasks(),
            "stages": self.get_settings().get("stages", DEFAULT_STAGES),
            "settings": self.get_settings(),
        }

    # ── Shared allowlisted-PATCH helper ──────────────────────────────────────

    def _write_statements(self, statements: list[tuple[str, tuple]]) -> None:
        """Several DIFFERENT statements in one BEGIN IMMEDIATE transaction — all or
        nothing. `_write_many` repeats ONE statement; a rename must move the category
        row and every card filed under it together, or a crash between the two leaves
        cards pointing at a name that no longer exists."""
        with self._lock:
            conn = self._get_conn()
            old_iso = conn.isolation_level
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                for sql, params in statements:
                    conn.execute(sql, params)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; the original error is what matters
                raise
            finally:
                conn.isolation_level = old_iso

    @staticmethod
    def _build_update(fields: dict[str, Any], writable: set[str], bool_cols: set[str]) -> tuple[str, list[Any]]:
        """Build a safe ``SET a = ?, b = ?`` clause from a raw client PATCH —
        only allowlisted columns, booleans coerced to 0/1. Column names come
        from the fixed ``writable`` set (never from client input), so the f-string
        is not an injection vector."""
        cols: list[str] = []
        params: list[Any] = []
        for key, val in (fields or {}).items():
            if key not in writable:
                continue
            cols.append(f"{key} = ?")
            params.append((1 if val else 0) if key in bool_cols else val)
        return ", ".join(cols), params


# ── Module singleton ─────────────────────────────────────────────────────────

_store: BoardStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.store_dir() / "board.db"


def get_board_store() -> BoardStore:
    global _store
    if _store is None:
        _store = BoardStore()
    return _store
