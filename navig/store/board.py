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
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# ── Vocabulary (kept in lock-step with apps/os/.../deck-types.ts) ─────────────

VALID_AI_MODES = ("inherit", "draft", "approval", "auto")
CONCRETE_AI_MODES = ("draft", "approval", "auto")  # what resolve_ai_mode returns
VALID_AGENT_STATUS = ("idle", "running", "awaiting_approval", "done", "failed")
VALID_PRIORITY = ("low", "normal", "high", "urgent")

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
}
_SUBTASK_WRITABLE = {"title", "done", "sort_order"}
_CARD_BOOL_COLS = {"auto_advance"}
_SUBTASK_BOOL_COLS = {"done"}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class BoardStore(BaseStore):
    SCHEMA_VERSION = 2
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
                completed_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_card_goal ON board_card(goal_id);
            CREATE INDEX IF NOT EXISTS idx_card_stage ON board_card(stage);

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

            -- single-row KV; settings are a JSON blob keyed 'settings'
            CREATE TABLE IF NOT EXISTS board_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
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
        rows = self._read_all("SELECT * FROM board_card ORDER BY sort_order, created_at")
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
