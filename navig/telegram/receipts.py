"""Durable per-update receipts: every accepted Telegram message reaches a terminal state.

Incident 2026-10-22 left no answer to "was the message processed and did the reply go
out?" — the conversation JSONL proves what was *stored*, not what the channel did with
the update. This store is the lifecycle record the mission asks for:

    received → terminal (responded | failed)

It is deliberately tiny: one SQLite row per update id, written at the single choke point
that already wraps every inbound message (`TelegramChannel._run_update_with_turn`), and
it survives a worker crash — `pending()` lists updates that never reached a terminal
state, which is exactly the recovery trace. No queue, no broker, no second brain.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["pending", "pending_count", "record_received", "record_terminal", "received"]

_SCHEMA = """
create table if not exists receipts (
    update_id   text primary key,
    chat_id     text,
    message_id  text,
    received_at real not null,
    terminal_at real,
    status      text not null default 'received',
    detail      text
)
"""


def _path() -> Path:
    from navig.platform.paths import data_dir

    return data_dir() / "telegram" / "receipts.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db = Path(path) if path is not None else _path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _ids(update: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    if not isinstance(update, dict):
        return None, None, None
    update_id = update.get("update_id")
    message = update.get("message") if isinstance(update.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return (
        str(update_id) if update_id is not None else None,
        str(chat.get("id")) if chat.get("id") is not None else None,
        str(message.get("message_id")) if message.get("message_id") is not None else None,
    )


def record_received(update: dict[str, Any], *, now: float | None = None) -> str | None:
    """Insert the ``received`` row (idempotent on update id). Returns the receipt key."""
    update_id, chat_id, message_id = _ids(update)
    if update_id is None:
        return None
    try:
        with _connect() as conn:
            conn.execute(
                "insert or ignore into receipts "
                "(update_id, chat_id, message_id, received_at, status) values (?, ?, ?, ?, 'received')",
                (update_id, chat_id, message_id, float(now if now is not None else time.time())),
            )
    except sqlite3.Error:
        logger.debug("receipt write failed", exc_info=True)
        return None
    return update_id


def record_terminal(
    update_id: str | None, status: str = "responded", detail: str = "", *, now: float | None = None
) -> None:
    """Mark the row terminal. Without a receipt (e.g. a callback update) this is a no-op."""
    if not update_id:
        return
    try:
        with _connect() as conn:
            conn.execute(
                "update receipts set terminal_at = ?, status = ?, detail = ? "
                "where update_id = ? and terminal_at is null",
                (float(now if now is not None else time.time()), status, detail[:400], update_id),
            )
    except sqlite3.Error:
        logger.debug("receipt terminal write failed", exc_info=True)


def received(update_id: str) -> dict[str, Any] | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "select update_id, chat_id, message_id, received_at, terminal_at, status, detail "
                "from receipts where update_id = ?",
                (update_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    keys = ("update_id", "chat_id", "message_id", "received_at", "terminal_at", "status", "detail")
    return dict(zip(keys, row))


def pending_count() -> int:
    """How many accepted updates never reached a terminal state (0 when the store is absent)."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "select count(*) from receipts where terminal_at is null"
            ).fetchone()
    except sqlite3.Error:
        return 0
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def pending(limit: int = 50) -> list[dict[str, Any]]:
    """Updates that never reached a terminal state — the recovery trace after a crash."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "select update_id, chat_id, message_id, received_at, terminal_at, status, detail "
                "from receipts where terminal_at is null order by received_at limit ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
    except sqlite3.Error:
        return []
    keys = ("update_id", "chat_id", "message_id", "received_at", "terminal_at", "status", "detail")
    return [dict(zip(keys, row)) for row in rows]
