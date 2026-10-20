"""ProjectStore — navig's own state about projects (never the project's files).

Tables:
  project_pref   per-project preferences (pinned, color, tags, hidden scripts)
  context_cache  the last built context pack per project, keyed by fingerprint
  analysis       every AI analysis, with the fingerprint of its input
  run_history    finished script runs (exit code, duration)

Keyed on the project's normalized PATH, not its space id: an id is a derived
label that ``spaces.registry.rename`` may change, a path is what the row means.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

__all__ = ["ProjectStore", "get_project_store", "reset_project_store"]

_PREF_FIELDS = ("pinned", "color", "icon", "tags", "hidden_scripts", "sort_order", "notes")


def _default_db_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.store_dir() / "projects.db"


def _key(path: str | Path) -> str:
    """Normalized path: resolved, and case-folded where the filesystem is (Windows)."""
    import os  # noqa: PLC0415

    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except Exception:  # noqa: BLE001
        return os.path.normcase(str(path))


class ProjectStore(BaseStore):
    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | None = None, **kw: Any) -> None:
        super().__init__(db_path or _default_db_path(), **kw)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_pref (
                project_key TEXT PRIMARY KEY,
                data        TEXT NOT NULL DEFAULT '{}',
                updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_cache (
                project_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                pack        TEXT NOT NULL,
                built_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis (
                id          TEXT PRIMARY KEY,
                project_key TEXT NOT NULL,
                kind        TEXT NOT NULL,
                target      TEXT,
                fingerprint TEXT,
                output      TEXT NOT NULL,
                model       TEXT,
                status      TEXT NOT NULL DEFAULT 'proposed',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_project ON analysis(project_key, created_at);
            CREATE TABLE IF NOT EXISTS run_history (
                id          TEXT PRIMARY KEY,
                project_key TEXT NOT NULL,
                script_key  TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                ended_at    TEXT,
                exit_code   INTEGER,
                outcome     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_runs_project ON run_history(project_key, started_at);
            """
        )

    # ── prefs ────────────────────────────────────────────────────────────
    def get_prefs(self, path: str | Path) -> dict[str, Any]:
        row = self._read_one("SELECT data FROM project_pref WHERE project_key = ?", (_key(path),))
        if row is None:
            return {}
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def all_prefs(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self._read_all("SELECT project_key, data FROM project_pref"):
            try:
                data = json.loads(row[1])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                out[row[0]] = data
        return out

    def set_prefs(self, path: str | Path, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge *patch* (known fields only; ``None`` clears a field)."""
        current = self.get_prefs(path)
        for k, v in patch.items():
            if k not in _PREF_FIELDS:
                continue
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        self._write(
            "INSERT INTO project_pref (project_key, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (_key(path), json.dumps(current), _utcnow()),
        )
        return current

    # ── context cache ────────────────────────────────────────────────────
    def get_context(self, path: str | Path) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT fingerprint, pack, built_at FROM context_cache WHERE project_key = ?", (_key(path),)
        )
        if row is None:
            return None
        try:
            pack = json.loads(row[1])
        except (TypeError, ValueError):
            return None
        return {"fingerprint": row[0], "pack": pack, "built_at": row[2]}

    def put_context(self, path: str | Path, fingerprint: str, pack: dict[str, Any]) -> None:
        self._write(
            "INSERT INTO context_cache (project_key, fingerprint, pack, built_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET fingerprint = excluded.fingerprint, "
            "pack = excluded.pack, built_at = excluded.built_at",
            (_key(path), fingerprint, json.dumps(pack), _utcnow()),
        )

    # ── analyses ─────────────────────────────────────────────────────────
    def add_analysis(
        self, path: str | Path, kind: str, output: dict[str, Any], *,
        target: str | None = None, fingerprint: str | None = None, model: str | None = None,
    ) -> dict[str, Any]:
        aid = uuid.uuid4().hex[:12]
        created = _utcnow()
        self._write(
            "INSERT INTO analysis (id, project_key, kind, target, fingerprint, output, model, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)",
            (aid, _key(path), kind, target, fingerprint, json.dumps(output), model, created),
        )
        return {"id": aid, "kind": kind, "target": target, "fingerprint": fingerprint,
                "output": output, "model": model, "status": "proposed", "created_at": created}

    def set_analysis_status(self, analysis_id: str, status: str) -> bool:
        if status not in ("proposed", "accepted", "rejected"):
            raise ValueError("status must be proposed, accepted or rejected")
        cur = self._write("UPDATE analysis SET status = ? WHERE id = ?", (status, analysis_id))
        return bool(cur.rowcount)

    def list_analyses(self, path: str | Path, *, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, target, fingerprint, output, model, status, created_at FROM analysis WHERE project_key = ?"
        params: list[Any] = [_key(path)]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        out = []
        for r in self._read_all(sql, tuple(params)):
            try:
                output = json.loads(r[4])
            except (TypeError, ValueError):
                output = {}
            out.append({"id": r[0], "kind": r[1], "target": r[2], "fingerprint": r[3], "output": output,
                        "model": r[5], "status": r[6], "created_at": r[7]})
        return out

    # ── run history ──────────────────────────────────────────────────────
    def record_run(
        self, path: str | Path, script_key: str, *, started_at: str, ended_at: str | None,
        exit_code: int | None, outcome: str, run_id: str | None = None,
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO run_history (id, project_key, script_key, started_at, ended_at, exit_code, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id or uuid.uuid4().hex[:12], _key(path), script_key, started_at, ended_at, exit_code, outcome),
        )

    def list_runs(self, path: str | Path, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._read_all(
            "SELECT id, script_key, started_at, ended_at, exit_code, outcome FROM run_history "
            "WHERE project_key = ? ORDER BY started_at DESC LIMIT ?",
            (_key(path), max(1, min(int(limit), 200))),
        )
        return [{"id": r[0], "script_key": r[1], "started_at": r[2], "ended_at": r[3],
                 "exit_code": r[4], "outcome": r[5]} for r in rows]


_store: ProjectStore | None = None


def get_project_store() -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore()
    return _store


def reset_project_store() -> None:
    """Drop the singleton (tests, and after an isolation env change)."""
    global _store
    _store = None
