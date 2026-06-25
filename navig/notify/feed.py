"""Deck notification feed — the in-app channel's backing store.

Every notification routed to the `deck` channel is appended here; the deck
renders it in the bell dropdown, the Inbox tab, and as a toast (live via the
`notification` SSE event). Append-only except for read flags.
"""

from __future__ import annotations

import json
from typing import Any

from navig.notify import store


def _row(r) -> dict[str, Any]:
    d = dict(r)
    raw = d.get("data")
    try:
        d["data"] = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        d["data"] = {}
    d["read"] = bool(d.get("read"))
    return d


def append(
    type_key: str,
    title: str,
    body: str = "",
    *,
    priority: str = "normal",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store.init_db()
    fid = store.new_id()
    c = store.conn()
    with c:
        c.execute(
            "INSERT INTO notify_feed (id, type, title, body, priority, data, created_at, read) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (fid, type_key, title, body or "", priority, json.dumps(data or {}), store.now_iso()),
        )
    return _row(c.execute("SELECT * FROM notify_feed WHERE id = ?", (fid,)).fetchone())


def list_items(limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
    store.init_db()
    sql = "SELECT * FROM notify_feed"
    if unread_only:
        sql += " WHERE read = 0"
    # rowid is monotonic with insertion, so it orders correctly even when several
    # items share the same whole-second created_at.
    sql += " ORDER BY rowid DESC LIMIT ?"
    return [_row(r) for r in store.conn().execute(sql, (min(int(limit), 200),)).fetchall()]


def unread_count() -> int:
    store.init_db()
    r = store.conn().execute("SELECT COUNT(*) AS n FROM notify_feed WHERE read = 0").fetchone()
    return int(r["n"])


def mark_read(feed_id: str) -> bool:
    store.init_db()
    c = store.conn()
    with c:
        cur = c.execute("UPDATE notify_feed SET read = 1 WHERE id = ?", (feed_id,))
    return (cur.rowcount or 0) > 0


def mark_all_read() -> int:
    store.init_db()
    c = store.conn()
    with c:
        cur = c.execute("UPDATE notify_feed SET read = 1 WHERE read = 0")
    return cur.rowcount or 0
