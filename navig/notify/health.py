"""Per-channel delivery health — "did the last one actually go out?"

The router already works out ``ok``/``detail`` for every channel on every
dispatch and then throws it away, so a channel that had been failing for weeks
looked identical to a healthy one: the appointment SMS sender was broken across
processes for as long as anyone could remember and nothing on any surface said
so.

One row per channel, written **only when the state changes** (or, for a channel
that keeps working, at most once an hour) — a store that is written on every
delivery would be a write amplification tax on the hot path.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("navig.notify.health")

#: While a channel keeps succeeding, refresh "last delivered" this often at most.
_REFRESH_OK_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_delivery_health (
  channel TEXT PRIMARY KEY,
  ok INTEGER NOT NULL,
  detail TEXT NOT NULL,
  state_since REAL NOT NULL,
  last_ok_at REAL,
  updated_at REAL NOT NULL
)
"""


def _db():
    from navig.notify import store

    store.init_db()
    c = store.conn()
    c.execute(_SCHEMA)
    return c


def record(channel: str, ok: bool, detail: str, *, now: float | None = None) -> None:
    """Note a delivery attempt. Never raises — health must not cost a delivery."""
    now = time.time() if now is None else now
    detail = str(detail or "")
    try:
        c = _db()
        row = c.execute(
            "SELECT ok, detail, state_since, last_ok_at FROM notify_delivery_health "
            "WHERE channel = ?",
            (channel,),
        ).fetchone()
        if row is None:
            with c:
                c.execute(
                    "INSERT INTO notify_delivery_health "
                    "(channel, ok, detail, state_since, last_ok_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (channel, 1 if ok else 0, detail, now, now if ok else None, now),
                )
            return

        changed = bool(row["ok"]) != bool(ok) or row["detail"] != detail
        # A healthy channel needs its "last delivered" time to stay fresh, but only
        # to hour resolution — one write an hour, not one per notification.
        stale_ok = ok and (row["last_ok_at"] is None or now - float(row["last_ok_at"]) >= _REFRESH_OK_S)
        if not changed and not stale_ok:
            return
        with c:
            c.execute(
                "UPDATE notify_delivery_health SET ok = ?, detail = ?, state_since = ?, "
                "last_ok_at = ?, updated_at = ? WHERE channel = ?",
                (
                    1 if ok else 0,
                    detail,
                    now if changed else float(row["state_since"]),
                    now if ok else row["last_ok_at"],
                    now,
                    channel,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("delivery health for %s not recorded: %s", channel, exc)


def record_attempt(channel: str, ok: bool, detail: str) -> None:
    """The router's entry point (kept separate so a caller may pass ``now``)."""
    record(channel, ok, detail)


def snapshot() -> dict[str, dict[str, Any]]:
    """``{channel: {ok, detail, state_since, last_ok_at, updated_at}}`` for the UI."""
    try:
        c = _db()
        rows = c.execute("SELECT * FROM notify_delivery_health").fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("delivery health unreadable: %s", exc)
        return {}
    return {r["channel"]: _row(r) for r in rows}


def _row(r) -> dict[str, Any]:
    return {
        "channel": r["channel"],
        "ok": bool(r["ok"]),
        "detail": r["detail"],
        "state_since": float(r["state_since"]),
        "last_ok_at": float(r["last_ok_at"]) if r["last_ok_at"] is not None else None,
        "updated_at": float(r["updated_at"]),
    }
