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
VALID_KINDS = (KIND_CARD, KIND_TODO)

# Seeded categories. FREE-FORM on purpose: the column is plain TEXT, so a category the
# operator invents works immediately and needs no migration. These are what the picker
# offers before they have any of their own.
SEED_CATEGORIES: tuple[str, ...] = ("life", "business", "project", "rendezvous")

# Where a todo came from. An AI guess must never be indistinguishable from something
# the operator typed — that is the difference between a suggestion they can dismiss
# and a task they think they wrote.
VALID_ORIGINS = ("manual", "ai", "agent")

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
_CARD_WRITABLE = {
    "title", "notes", "stage", "priority", "due_at", "reminder_id",
    "ai_mode", "auto_advance", "mission_id", "agent_status", "agent_result",
    "goal_id", "sort_order",
    # PIM fields. `kind` is DELIBERATELY absent: a card must never be able to mutate
    # into a todo (or back) through a generic PATCH. That is the one invariant the
    # two populations rely on, and a writable `kind` would let any client break it
    # with a field name typo.
    "category", "space", "recur", "remind_before", "origin", "origin_ref",
}
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
    SCHEMA_VERSION = 4
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
                updated_at   TEXT NOT NULL
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
                origin_ref    TEXT
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
        self._create_kind_indexes(conn)

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
        if "kind" not in BoardStore._column_names(conn, "board_card"):
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
        }

    # ── Goals ────────────────────────────────────────────────────────────────

    def list_goals(self) -> list[dict[str, Any]]:
        rows = self._read_all("SELECT * FROM board_goal ORDER BY sort_order, created_at")
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

    # ── Cards ────────────────────────────────────────────────────────────────

    def list_cards(self) -> list[dict[str, Any]]:
        """Board cards only.

        The `kind` filter is what keeps "Buy milk" out of the desktop Kanban's Backlog
        column. Without it the PIM would flood the board the first time it is used,
        which is precisely why people end up running two task apps.
        """
        rows = self._read_all(
            "SELECT * FROM board_card WHERE kind = ? ORDER BY sort_order, created_at",
            (KIND_CARD,),
        )
        terminal = self._terminal_stages()
        # Fetch all edges once, group by card — avoids an N+1 per-card query.
        edges: dict[str, list[str]] = {}
        for e in self._read_all("SELECT card_id, depends_on_id FROM board_dep"):
            edges.setdefault(e["card_id"], []).append(e["depends_on_id"])
        return [self._card_to_dict(r, deps=edges.get(r["id"], []), terminal=terminal) for r in rows]

    def get_card(self, card_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM board_card WHERE id = ?", (card_id,))
        return self._card_to_dict(row) if row else None

    def create_card(self, title: str, *, goal_id: str | None = None, notes: str = "", stage: str = "backlog",
                    priority: str = "normal", due_at: str | None = None, reminder_id: int | None = None,
                    ai_mode: str = "inherit", auto_advance: bool = False, mission_id: str | None = None) -> dict[str, Any]:
        cid, now = _new_id(), _utcnow()
        nxt = self._read_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_card WHERE stage = ?", (stage,))
        completed = now if stage in self._terminal_stages() else None
        self._write(
            "INSERT INTO board_card(id, goal_id, title, notes, stage, priority, due_at, reminder_id, ai_mode,"
            " auto_advance, mission_id, agent_status, agent_result, sort_order, created_at, updated_at, completed_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', '', ?, ?, ?, ?)",
            (cid, goal_id, title, notes or "", stage or "backlog", priority or "normal", due_at, reminder_id,
             ai_mode if ai_mode in VALID_AI_MODES else "inherit", 1 if auto_advance else 0, mission_id,
             int(nxt["n"]) if nxt else 0, now, now, completed),
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
                "ai_mode", "auto_advance", "mission_id",
            }})
            if prev_id is not None:
                self.add_dependency(card["id"], prev_id)
            prev_id = card["id"]
            created.append(self.get_card(card["id"]))  # type: ignore[arg-type]
        return created

    def update_card(self, card_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self._read_one("SELECT id FROM board_card WHERE id = ?", (card_id,)):
            return None
        sets, params = self._build_update(fields, _CARD_WRITABLE, _CARD_BOOL_COLS)
        if sets:
            self._write(f"UPDATE board_card SET {sets}, updated_at = ? WHERE id = ?", (*params, _utcnow(), card_id))
        return self.get_card(card_id)

    def delete_card(self, card_id: str) -> None:
        self._write("DELETE FROM board_card WHERE id = ?", (card_id,))

    def move_card(self, card_id: str, stage: str, *, sort_order: int | None = None, actor: str = "user") -> dict[str, Any] | None:
        row = self._read_one("SELECT stage FROM board_card WHERE id = ?", (card_id,))
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
    ) -> dict[str, Any]:
        """Capture one task. Everything but the title is optional — that is the point.

        A todo with no date is not an incomplete todo; it is an INBOX item, which is
        the state most tasks are captured in and the one a capture flow must not
        demand you resolve up front.
        """
        cid, now = _new_id(), _utcnow()
        nxt = self._read_one(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM board_card WHERE kind = ?",
            (KIND_TODO,),
        )
        self._write(
            "INSERT INTO board_card(id, goal_id, title, notes, stage, priority, due_at,"
            " reminder_id, ai_mode, auto_advance, mission_id, agent_status, agent_result,"
            " sort_order, created_at, updated_at, completed_at,"
            " kind, category, space, recur, remind_before, origin, origin_ref)"
            " VALUES(?, ?, ?, ?, 'inbox', ?, ?, NULL, 'inherit', 0, NULL, 'idle', '',"
            " ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid, goal_id, title, notes or "",
                priority if priority in VALID_PRIORITY else "normal",
                due_at,
                int(nxt["n"]) if nxt else 0, now, now,
                KIND_TODO,
                (category or "").strip().lower(),
                space,
                recur,
                _encode_leads(remind_before),
                origin if origin in VALID_ORIGINS else "manual",
                origin_ref,
            ),
        )
        return self.get_todo(cid)  # type: ignore[return-value]

    def get_todo(self, card_id: str) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT * FROM board_card WHERE id = ? AND kind = ?", (card_id, KIND_TODO)
        )
        return self._card_to_dict(row) if row else None

    def list_todos(
        self,
        *,
        category: str | None = None,
        space: str | None = None,
        origin: str | None = None,
        include_done: bool = False,
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
        rows = self._read_all(
            f"SELECT * FROM board_card WHERE {' AND '.join(clauses)}"  # noqa: S608 — clauses are literals
            " ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, created_at",
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
        sets, params = self._build_update(patch, _CARD_WRITABLE, _CARD_BOOL_COLS)
        if sets:
            self._write(
                f"UPDATE board_card SET {sets}, updated_at = ? WHERE id = ? AND kind = ?",  # noqa: S608
                (*params, _utcnow(), card_id, KIND_TODO),
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

    def delete_todo(self, card_id: str) -> bool:
        """Remove a todo. Returns whether it existed.

        Scoped by `kind` so a stray id can never delete a BOARD card through the PIM's
        surfaces — the two populations share a table and a mis-scoped delete here
        would take out a real project task with no confirmation.

        A todo that came from a SOURCE (an AI or agent suggestion) leaves a dismissal
        record behind. Without it, deleting a suggestion deletes the only evidence it
        was ever offered, and the next scan proposes the identical line again — which
        teaches the operator that dismissing does nothing.
        """
        todo = self.get_todo(card_id)
        if todo is None:
            return False
        origin_ref = todo.get("origin_ref")
        if origin_ref:
            self._write(
                "INSERT OR REPLACE INTO board_todo_dismissed(origin_ref, title, dismissed_at)"
                " VALUES(?, ?, ?)",
                (str(origin_ref), str(todo.get("title") or ""), _utcnow()),
            )
        cursor = self._write(
            "DELETE FROM board_card WHERE id = ? AND kind = ?", (card_id, KIND_TODO)
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
        names = list(SEED_CATEGORIES)
        names += sorted(c for c in counts if c and c not in SEED_CATEGORIES)
        out = [{"name": n, "open": counts.get(n, 0)} for n in names]
        if counts.get(""):
            out.append({"name": "", "open": counts[""]})
        return out

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
        """Advance an ISO due date by one period, staying in UTC.

        Returns None rather than raising for an unparseable stored value: a corrupt
        `due_at` must not make a task impossible to tick off.
        """
        from datetime import datetime as _dt  # noqa: PLC0415 — keeps `navig help` fast

        try:
            from navig.pim.dates import advance  # noqa: PLC0415

            base = _dt.fromisoformat(due_at.replace("Z", "+00:00"))
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
