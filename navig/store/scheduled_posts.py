"""
ScheduledPostStore — persistence for Studio composer drafts and scheduled posts.

Backs the Studio deck API (``navig_social.deck_routes.studio``) and the always-on
:class:`navig_social.social.scheduler_service.ScheduledPostService`, which every
~20s pulls due posts via :meth:`due`, publishes them, and reschedules ``recurring``
ones. (The Studio surface lives in the optional navig-social plugin; this store
stays in core because it owns persistence.)

A *post* is a row with opaque JSON ``content`` (markdown body + media refs),
a list of publish ``targets``, a ``status`` lifecycle, and scheduling fields:

    status          draft → scheduled → publishing → published|partial|failed
                    (recurring posts return to ``scheduled`` with a fresh run_at)
    schedule_kind   "now" | "once" | "recurring"
    run_at          ISO-8601 UTC (e.g. "2026-06-27T15:30:00Z"). Compared
                    LEXICOGRAPHICALLY in :meth:`due`, so callers must store a
                    zero-padded, Z-suffixed UTC timestamp — the same format the
                    scheduler's CronParser path emits.

Backed by :class:`BaseStore` for WAL, schema versioning, and write serialisation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# Columns that store JSON-encoded structures; (de)serialised transparently.
_JSON_FIELDS = {"content": "content_json", "targets": "targets_json", "receipts": "receipts_json"}
# Scalar columns a caller may set via create()/update().
_SCALAR_FIELDS = {"body", "status", "schedule_kind", "run_at", "cron_expr", "last_error"}


def _canonical_run_at(value: Any) -> Any:
    """Normalise a caller-supplied ``run_at`` to canonical UTC ``%Y-%m-%dT%H:%M:%SZ``.

    ``due()`` compares ``run_at`` LEXICOGRAPHICALLY, so every stored value must
    share one exact format or a post fires early / late / never.  Callers (the
    deck route, the API, an agent) may send anything ISO-ish — JS
    ``toISOString()`` → ``…:00.000Z`` (milliseconds), ``datetime.isoformat()`` →
    ``…+00:00`` (offset), a ``datetime-local`` value → ``…T18:00`` (no seconds,
    naive), or a non-UTC offset like ``…+05:30`` (which sorts by its *local*
    wall-clock, not the real UTC instant).  Parse robustly, convert to UTC, drop
    sub-seconds.  A non-ISO value is returned unchanged so it fails loudly
    downstream rather than being silently zeroed.
    """
    if not value or not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return value  # not ISO-8601 — leave as-is
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # naive input is assumed UTC
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScheduledPostStore(BaseStore):
    """SQLite store for Studio posts. See module docstring for the lifecycle."""

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -2000}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                body           TEXT NOT NULL DEFAULT '',
                content_json   TEXT NOT NULL DEFAULT '{}',
                targets_json   TEXT NOT NULL DEFAULT '[]',
                status         TEXT NOT NULL DEFAULT 'draft',
                schedule_kind  TEXT NOT NULL DEFAULT 'now',
                run_at         TEXT,
                cron_expr      TEXT,
                receipts_json  TEXT NOT NULL DEFAULT '[]',
                last_error     TEXT,
                created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            -- The scheduler's hot path: due() filters on (status, run_at).
            CREATE INDEX IF NOT EXISTS idx_sched_due
                ON scheduled_posts (status, run_at);
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        pass  # v1 is initial

    # ── Row conversion ────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "body": row["body"] or "",
            "content": json.loads(row["content_json"] or "{}"),
            "targets": json.loads(row["targets_json"] or "[]"),
            "status": row["status"],
            "schedule_kind": row["schedule_kind"],
            "run_at": row["run_at"],
            "cron_expr": row["cron_expr"],
            "receipts": json.loads(row["receipts_json"] or "[]"),
            "last_error": row["last_error"],
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }

    # ── CRUD ──────────────────────────────────────────────────

    def create(
        self,
        *,
        body: str = "",
        content: dict[str, Any] | None = None,
        targets: list[Any] | None = None,
        status: str = "draft",
        schedule_kind: str = "now",
        run_at: str | None = None,
        cron_expr: str | None = None,
    ) -> int:
        """Insert a post and return its new id."""
        run_at = _canonical_run_at(run_at)
        now = _utcnow()
        cursor = self._write(
            "INSERT INTO scheduled_posts "
            "(body, content_json, targets_json, status, schedule_kind, run_at, "
            " cron_expr, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body or "",
                json.dumps(content or {}),
                json.dumps(targets or []),
                status,
                schedule_kind,
                run_at,
                cron_expr,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid or 0)

    def get(self, post_id: int) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
        return self._row_to_dict(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """List posts, newest first, optionally filtered by status."""
        if status:
            rows = self._read_all(
                "SELECT * FROM scheduled_posts WHERE status = ? "
                "ORDER BY COALESCE(run_at, created_at) DESC, id DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self._read_all(
                "SELECT * FROM scheduled_posts "
                "ORDER BY COALESCE(run_at, created_at) DESC, id DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_dict(r) for r in rows]

    def update(self, post_id: int, **fields: Any) -> bool:
        """Update an allowlisted set of fields. Unknown keys are ignored.

        JSON fields (``content``/``targets``/``receipts``) accept Python
        structures and are serialised; scalar fields pass through. ``updated_at``
        is always bumped. Returns True if a row was changed.
        """
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key in _JSON_FIELDS:
                sets.append(f"{_JSON_FIELDS[key]} = ?")
                params.append(json.dumps(value if value is not None else ([] if key != "content" else {})))
            elif key in _SCALAR_FIELDS:
                sets.append(f"{key} = ?")
                params.append(_canonical_run_at(value) if key == "run_at" else value)
            # silently ignore unknown / non-updatable keys (e.g. id, created_at)

        if not sets:
            return False

        sets.append("updated_at = ?")
        params.append(_utcnow())
        params.append(post_id)
        cursor = self._write(
            f"UPDATE scheduled_posts SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return cursor.rowcount > 0

    def delete(self, post_id: int) -> bool:
        cursor = self._write("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))
        return cursor.rowcount > 0

    # ── Scheduler hot path ────────────────────────────────────

    def due(self, now_iso: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return scheduled posts whose ``run_at`` has arrived.

        Only ``status = 'scheduled'`` rows are eligible; ``run_at`` is compared
        lexicographically (ISO-8601 UTC). Drafts, terminal posts, and rows
        mid-publish (``status = 'publishing'``) are excluded, so a slow publish
        cannot double-fire on the next tick.
        """
        rows = self._read_all(
            "SELECT * FROM scheduled_posts "
            "WHERE status = 'scheduled' AND run_at IS NOT NULL AND run_at <= ? "
            "ORDER BY run_at ASC LIMIT ?",
            (now_iso, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    def count(self, *, status: str | None = None) -> int:
        if status:
            row = self._read_one(
                "SELECT COUNT(*) AS cnt FROM scheduled_posts WHERE status = ?", (status,)
            )
        else:
            row = self._read_one("SELECT COUNT(*) AS cnt FROM scheduled_posts")
        return row["cnt"] if row else 0


# ── Singleton ─────────────────────────────────────────────────

_store: ScheduledPostStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "scheduled_posts.db"


def get_scheduled_posts() -> ScheduledPostStore:
    """Return the global :class:`ScheduledPostStore` singleton."""
    global _store
    if _store is None:
        _store = ScheduledPostStore()
    return _store
