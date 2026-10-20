"""
DecideUsageStore — how often the decision model (Clef) was called, and how the last call went.

Two readers need this and neither can get it anywhere else:

* the **daily cap** (``decide.daily_cap``) — the gateway and the Telegram worker are
  separate processes, so an in-memory counter would let each spend the whole allowance.
  The count lives here, where both see it.
* **status / doctor** — ``navig decider status`` and the doctor row may only claim the
  model works after a call actually succeeded, so the last call's outcome is kept.
* **shadow mode** — a use in shadow mode lets the previous logic decide and records here
  whether Clef AGREED with it. The agreement rate is the evidence for switching that use
  on, and the recent disagreements are what a human reviews before doing so.

Days are UTC on purpose: the cap protects Cloudflare's daily Workers AI allowance, which
resets at 00:00 UTC.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

#: Days of per-day rows to keep; older ones are pruned on write.
KEEP_DAYS = 30


def usage_day() -> str:
    """The cap's day, in UTC on purpose (Cloudflare's allowance resets at 00:00 UTC).
    The ONE place this day is computed — written and read only by the decider."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")



#: Most recent shadow disagreements kept for review.
KEEP_DISAGREEMENTS = 200


class DecideUsageStore(BaseStore):
    SCHEMA_VERSION = 2
    PRAGMAS = {"cache_size": -500}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usage (
                day           TEXT NOT NULL,
                use           TEXT NOT NULL,
                calls         INTEGER NOT NULL DEFAULT 0,
                ok            INTEGER NOT NULL DEFAULT 0,
                failed        INTEGER NOT NULL DEFAULT 0,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, use)
            );
            CREATE TABLE IF NOT EXISTS last_call (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                at          TEXT NOT NULL,
                use         TEXT NOT NULL,
                ok          INTEGER NOT NULL,
                kind        TEXT NOT NULL DEFAULT '',
                latency_ms  INTEGER NOT NULL DEFAULT 0,
                model       TEXT NOT NULL DEFAULT '',
                detail      TEXT NOT NULL DEFAULT ''
            );
        """)
        self._create_shadow_tables(conn)

    def _create_shadow_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shadow (
                day      TEXT NOT NULL,
                use      TEXT NOT NULL,
                question TEXT NOT NULL,
                total    INTEGER NOT NULL DEFAULT 0,
                agree    INTEGER NOT NULL DEFAULT 0,
                abstain  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, use, question)
            );
            CREATE TABLE IF NOT EXISTS disagreements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                at          TEXT NOT NULL,
                use         TEXT NOT NULL,
                question    TEXT NOT NULL,
                clef        TEXT NOT NULL,
                legacy      TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 0,
                sample      TEXT NOT NULL DEFAULT ''
            );
        """)

    def _migrate(self, conn: sqlite3.Connection, from_v: int, to_v: int) -> None:
        if from_v < 2:
            self._create_shadow_tables(conn)

    def record_call(
        self,
        *,
        use: str,
        ok: bool,
        kind: str = "",
        latency_ms: int = 0,
        model: str = "",
        input_tokens: int = 0,
        detail: str = "",
    ) -> None:
        """Count one call against today and remember it as the last call."""
        day = usage_day()
        self._write(
            """INSERT INTO usage (day, use, calls, ok, failed, input_tokens)
               VALUES (?, ?, 1, ?, ?, ?)
               ON CONFLICT(day, use) DO UPDATE SET
                   calls = calls + 1,
                   ok = ok + excluded.ok,
                   failed = failed + excluded.failed,
                   input_tokens = input_tokens + excluded.input_tokens""",
            (day, use, 1 if ok else 0, 0 if ok else 1, max(0, int(input_tokens))),
        )
        self._write(
            """INSERT INTO last_call (id, at, use, ok, kind, latency_ms, model, detail)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   at = excluded.at, use = excluded.use, ok = excluded.ok,
                   kind = excluded.kind, latency_ms = excluded.latency_ms,
                   model = excluded.model, detail = excluded.detail""",
            (_utcnow(), use, 1 if ok else 0, kind, int(latency_ms), model, detail[:300]),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        self._write("DELETE FROM usage WHERE day < ?", (cutoff,))
        self._write("DELETE FROM shadow WHERE day < ?", (cutoff,))

    def record_comparison(
        self,
        *,
        use: str,
        question: str,
        clef: str | None,
        legacy: str,
        confidence: float = 0.0,
        sample: str = "",
    ) -> None:
        """One shadow comparison. ``clef=None`` = Clef was not confident enough to answer
        (an ABSTAIN — counted, never as agreement or disagreement)."""
        agree = clef is not None and clef == legacy
        self._write(
            """INSERT INTO shadow (day, use, question, total, agree, abstain)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(day, use, question) DO UPDATE SET
                   total = total + 1,
                   agree = agree + excluded.agree,
                   abstain = abstain + excluded.abstain""",
            (usage_day(), use, question, 1 if agree else 0, 1 if clef is None else 0),
        )
        if clef is not None and not agree:
            self._write(
                """INSERT INTO disagreements (at, use, question, clef, legacy, confidence, sample)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_utcnow(), use, question, clef[:80], legacy[:80], float(confidence), sample[:120]),
            )
            self._write(
                "DELETE FROM disagreements WHERE id <= "
                "(SELECT COALESCE(MAX(id), 0) FROM disagreements) - ?",
                (KEEP_DISAGREEMENTS,),
            )

    def agreement(self, *, days: int = 7) -> dict[str, dict[str, int]]:
        """Per use, over the last *days* UTC days: compared · agreed · abstained."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._read_all(
            """SELECT use, SUM(total) AS total, SUM(agree) AS agree, SUM(abstain) AS abstain
               FROM shadow WHERE day >= ? GROUP BY use ORDER BY use""",
            (cutoff,),
        )
        return {
            r["use"]: {"total": int(r["total"]), "agree": int(r["agree"]), "abstain": int(r["abstain"])}
            for r in rows
        }

    def recent_disagreements(self, *, limit: int = 20, use: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT at, use, question, clef, legacy, confidence, sample FROM disagreements"
        params: tuple = ()
        if use:
            sql += " WHERE use = ?"
            params = (use,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self._read_all(sql, (*params, int(limit)))
        return [dict(r) for r in rows]

    def calls_today(self) -> int:
        row = self._read_one("SELECT COALESCE(SUM(calls), 0) AS n FROM usage WHERE day = ?", (usage_day(),))
        return int(row["n"]) if row else 0

    def usage_today(self) -> dict[str, dict[str, int]]:
        """Per use, today (UTC): calls · ok · failed · input tokens."""
        rows = self._read_all(
            "SELECT use, calls, ok, failed, input_tokens FROM usage WHERE day = ? ORDER BY use",
            (usage_day(),),
        )
        return {
            r["use"]: {
                "calls": int(r["calls"]),
                "ok": int(r["ok"]),
                "failed": int(r["failed"]),
                "input_tokens": int(r["input_tokens"]),
            }
            for r in rows
        }

    def last_call(self) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT at, use, ok, kind, latency_ms, model, detail FROM last_call WHERE id = 1"
        )
        if row is None:
            return None
        return {
            "at": row["at"],
            "use": row["use"],
            "ok": bool(row["ok"]),
            "kind": row["kind"],
            "latency_ms": int(row["latency_ms"]),
            "model": row["model"],
            "detail": row["detail"],
        }


# ── Singleton ─────────────────────────────────────────────────

_store: DecideUsageStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "decide_usage.db"


def get_decide_usage_store() -> DecideUsageStore:
    global _store
    if _store is None:
        _store = DecideUsageStore()
    return _store


def reset_decide_usage_store() -> None:
    """Tests only: drop the singleton so the next call re-reads NAVIG_DATA_DIR."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:  # noqa: BLE001
            pass
    _store = None
