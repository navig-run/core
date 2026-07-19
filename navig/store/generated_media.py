"""
GeneratedMediaStore — persistence for the NAVIG Media generation workflow.

Every generated variant (image / video / audio) becomes one row here — the
queryable mirror of the on-disk ``refs`` ledger (:mod:`navig.media.refs_library`).
It backs the Media deck app's History/gallery and the "keep vs reject" decision.

A *variant* is a row with:

    id            opaque uuid (also the on-disk filename stem in refs)
    group_id      re-roll group — every "next" on the same brief shares this,
                  so a variant strip is ``list(group_id=…)``
    modality      image | video | audio
    status        generated → kept | rejected   (never deleted — rejects are retained)
    prompt        the ENRICHED prompt actually sent
    provider/model/seed   provenance for reproduce/repeat
    params        JSON: size/kind/n/duration/etc.
    context       JSON: palette + style-note ids + reference ref + placement note
    path          file location (staging, then promoted on keep)
    space         the active-space root the refs live under

Backed by :class:`BaseStore` (WAL, schema versioning, serialized writes),
mirroring :class:`navig.store.scheduled_posts.ScheduledPostStore`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

_JSON_FIELDS = {"params": "params_json", "context": "context_json"}
_SCALAR_FIELDS = {"status", "path", "seed", "model", "prompt", "license"}


class GeneratedMediaStore(BaseStore):
    """SQLite store for generated media variants. See module docstring."""

    SCHEMA_VERSION = 2

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS generated_media (
                id           TEXT PRIMARY KEY,
                group_id     TEXT NOT NULL,
                modality     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'generated',
                prompt       TEXT NOT NULL DEFAULT '',
                provider     TEXT NOT NULL DEFAULT '',
                model        TEXT,
                seed         INTEGER,
                params_json  TEXT NOT NULL DEFAULT '{}',
                context_json TEXT NOT NULL DEFAULT '{}',
                path         TEXT,
                space        TEXT,
                license      TEXT,
                created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            -- History hot path: recent rows, filterable by modality/status.
            CREATE INDEX IF NOT EXISTS idx_media_recent
                ON generated_media (status, created_at);
            CREATE INDEX IF NOT EXISTS idx_media_group
                ON generated_media (group_id);
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        if from_version < 2:
            # v2 adds a license column for provenance. ADD COLUMN is a no-op if a
            # fresh v2 CREATE already made it; guard against the duplicate.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(generated_media)").fetchall()}
            if "license" not in cols:
                conn.execute("ALTER TABLE generated_media ADD COLUMN license TEXT")

    # ── Row conversion ────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "group_id": row["group_id"],
            "modality": row["modality"],
            "status": row["status"],
            "prompt": row["prompt"] or "",
            "provider": row["provider"] or "",
            "model": row["model"],
            "seed": row["seed"],
            "params": json.loads(row["params_json"] or "{}"),
            "context": json.loads(row["context_json"] or "{}"),
            "path": row["path"],
            "space": row["space"],
            "license": row["license"] if "license" in row.keys() else None,
            "created_at": row["created_at"] or "",
        }

    # ── CRUD ──────────────────────────────────────────────────

    def create(
        self,
        *,
        id: str,
        group_id: str,
        modality: str,
        prompt: str = "",
        provider: str = "",
        model: str | None = None,
        seed: int | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        path: str | None = None,
        space: str | None = None,
        license: str | None = None,
        status: str = "generated",
    ) -> str:
        """Insert a variant row and return its id."""
        self._write(
            "INSERT INTO generated_media "
            "(id, group_id, modality, status, prompt, provider, model, seed, "
            " params_json, context_json, path, space, license, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                id,
                group_id,
                modality,
                status,
                prompt or "",
                provider or "",
                model,
                seed,
                json.dumps(params or {}),
                json.dumps(context or {}),
                path,
                space,
                license,
                _utcnow(),
            ),
        )
        return id

    def get(self, media_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM generated_media WHERE id = ?", (media_id,))
        return self._row_to_dict(row) if row else None

    def list(
        self,
        *,
        modality: str | None = None,
        status: str | None = None,
        space: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List variants, newest first, filtered by modality/status/space.

        Pass ``space`` (a space root) to scope to one project; omit for all spaces.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if modality:
            clauses.append("modality = ?")
            params.append(modality)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if space:
            clauses.append("space = ?")
            params.append(space)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        params.append(limit)
        rows = self._read_all(
            f"SELECT * FROM generated_media {where}ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [self._row_to_dict(r) for r in rows]

    def list_group(self, group_id: str) -> list[dict[str, Any]]:
        """Return every variant in a re-roll group, oldest first (the strip)."""
        rows = self._read_all(
            "SELECT * FROM generated_media WHERE group_id = ? ORDER BY created_at ASC, id ASC",
            (group_id,),
        )
        return [self._row_to_dict(r) for r in rows]

    def update(self, media_id: str, **fields: Any) -> bool:
        """Update an allowlisted set of fields. Unknown keys are ignored."""
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key in _JSON_FIELDS:
                sets.append(f"{_JSON_FIELDS[key]} = ?")
                params.append(json.dumps(value if value is not None else {}))
            elif key in _SCALAR_FIELDS:
                sets.append(f"{key} = ?")
                params.append(value)
            # silently ignore id / created_at / unknown
        if not sets:
            return False
        params.append(media_id)
        cursor = self._write(
            f"UPDATE generated_media SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return cursor.rowcount > 0

    def set_status(self, media_id: str, status: str, *, path: str | None = None) -> bool:
        """Mark a variant kept/rejected (and optionally record its promoted path)."""
        fields: dict[str, Any] = {"status": status}
        if path is not None:
            fields["path"] = path
        return self.update(media_id, **fields)

    def count(self, *, status: str | None = None) -> int:
        if status:
            row = self._read_one(
                "SELECT COUNT(*) AS cnt FROM generated_media WHERE status = ?", (status,)
            )
        else:
            row = self._read_one("SELECT COUNT(*) AS cnt FROM generated_media")
        return row["cnt"] if row else 0


# ── Singleton ─────────────────────────────────────────────────

_store: GeneratedMediaStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "generated_media.db"


def get_generated_media() -> GeneratedMediaStore:
    """Return the global :class:`GeneratedMediaStore` singleton."""
    global _store
    if _store is None:
        _store = GeneratedMediaStore()
    return _store
