"""
api.routes.telemetry — FastAPI router for anonymous install telemetry.

Endpoints:
  POST /telemetry/ping   — Receive one install ping per unique machine.
  GET  /telemetry/stats  — Summary statistics (total + by platform).

Deduplication strategy:
  The client sends anon_id = sha256(machine_uuid)[:16].
  Server stores sha256(anon_id) — a second hash pass — so the raw anon_id
  is never persisted on disk.

  UNIQUE constraint on anon_id_hash ensures upsert is idempotent —
  repeated pings from the same machine update last_seen but don't
  inflate the total_installs count.

Database:
  SQLite via aiosqlite.  DB path resolved in order:
    1. NAVIG_TELEMETRY_DB environment variable
    2. NAVIG_TELEMETRY_DB_DEFAULT fallback ("./installs.db")

  Use schema.sql (project root) to provision the DB manually:
    sqlite3 installs.db < schema.sql

Run:
  pip install "navig-core[api]"
  uvicorn api.main:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, status
from pydantic import BaseModel, Field

# ── Router configuration ───────────────────────────────────────────────────

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# ── Database path resolution ───────────────────────────────────────────────

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "installs.db"
_DB_PATH: Path = Path(os.environ.get("NAVIG_TELEMETRY_DB", str(_DEFAULT_DB)))

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS installs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    anon_id_hash   TEXT    UNIQUE NOT NULL,
    platform       TEXT    NOT NULL DEFAULT 'unknown',
    arch           TEXT    NOT NULL DEFAULT 'unknown',
    python         TEXT    NOT NULL DEFAULT 'unknown',
    first_seen     TEXT    NOT NULL,
    last_seen      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_installs_platform ON installs (platform);
"""


async def _get_db() -> aiosqlite.Connection:
    """Open (and lazily provision) the SQLite connection."""
    db = await aiosqlite.connect(_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA_SQL)
    await db.commit()
    return db


# ── Pydantic schemas ───────────────────────────────────────────────────────


class PingPayload(BaseModel):
    """Payload sent by the client on first install."""

    event: str = Field("install", max_length=32)
    platform: str = Field("unknown", max_length=64)
    arch: str = Field("unknown", max_length=64)
    python: str = Field("unknown", max_length=32)
    anon_id: str = Field(
        ..., min_length=8, max_length=64, description="First-pass hash from client"
    )


class PingResponse(BaseModel):
    ok: bool = True
    message: str = "recorded"


class StatsResponse(BaseModel):
    total_installs: int
    by_platform: dict[str, int]


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post(
    "/ping",
    response_model=PingResponse,
    status_code=status.HTTP_200_OK,
    summary="Record an anonymous install ping",
)
async def post_ping(payload: PingPayload) -> PingResponse:
    """
    Receive an install ping and upsert into the installs table.

    The server performs a second SHA-256 pass on the client-provided
    ``anon_id`` before writing it to the database, so the raw value
    is never stored at rest.
    """
    # Server-side second hash
    anon_id_hash = hashlib.sha256(payload.anon_id.encode()).hexdigest()

    now_iso = datetime.now(timezone.utc).isoformat()

    async with await _get_db() as db:
        await db.execute(
            """
            INSERT INTO installs (anon_id_hash, platform, arch, python, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (anon_id_hash) DO UPDATE SET
                last_seen = excluded.last_seen,
                platform  = excluded.platform,
                arch      = excluded.arch,
                python    = excluded.python
            """,
            (
                anon_id_hash,
                payload.platform[:64],
                payload.arch[:64],
                payload.python[:32],
                now_iso,
                now_iso,
            ),
        )
        await db.commit()

    return PingResponse()


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Retrieve aggregated install statistics",
)
async def get_stats() -> StatsResponse:
    """Return total install count and per-platform breakdown."""
    async with await _get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) AS total FROM installs")
        row = await cursor.fetchone()
        total = int(row["total"]) if row else 0

        cursor = await db.execute(
            "SELECT platform, COUNT(*) AS cnt FROM installs GROUP BY platform ORDER BY cnt DESC"
        )
        rows = await cursor.fetchall()
        by_platform = {r["platform"]: int(r["cnt"]) for r in rows}

    return StatsResponse(total_installs=total, by_platform=by_platform)
