"""An alert the operator resolved does not come back the next time the monitor runs.

Every alert today is fire-and-forget: a unit fails, the monitor says so, the operator
reads it, fixes the unit — and the monitor's next run has already re-sent the same
words, because nothing recorded that a person had seen them. Pushover alone tracked
``acknowledged``; Telegram had no way to say "read, handled" at all.

A resolution is keyed on the alert's own words (``alert:<digest of title and body>``),
not on a timer: a *different* alert — a different unit, a different number — is a
different key and still arrives. The record lives in ``notify_settings`` next to the
dedupe claims, so it survives a daemon restart and every process sees it.
"""

from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger("navig.notify.acks")

_PREFIX = "ack:"


def key_for(title: str, body: str) -> str:
    """The stable key for one alert's words — reworded is a new alert, by design."""
    digest = hashlib.sha1(f"{title}\n{body}".encode()).hexdigest()[:16]
    return f"alert:{digest}"


def acknowledge(key: str, *, now: float | None = None) -> bool:
    """Record that the operator handled *key*. False when the record could not be written."""
    now = time.time() if now is None else now
    try:
        from navig.notify import store

        store.init_db()
        c = store.conn()
        with c:
            c.execute(
                "INSERT INTO notify_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_PREFIX + key, repr(now)),
            )
        return True
    except Exception:  # noqa: BLE001 — a lost ack repeats an alert; a crash costs the press
        logger.warning("acknowledging %r failed", key, exc_info=True)
        return False


def acknowledged(key: str) -> bool:
    """True when this exact alert has been resolved. An unreadable store answers False."""
    try:
        from navig.notify import store

        store.init_db()
        row = (
            store.conn()
            .execute("SELECT 1 FROM notify_settings WHERE key = ? LIMIT 1", (_PREFIX + key,))
            .fetchone()
        )
        return row is not None
    except Exception:  # noqa: BLE001 — better a duplicate than a swallowed alert
        logger.debug("reading the ack for %r failed", key, exc_info=True)
        return False


def forget(key: str) -> None:
    """Drop the resolution — the alert is live again (used when its condition returns)."""
    try:
        from navig.notify import store

        c = store.conn()
        with c:
            c.execute("DELETE FROM notify_settings WHERE key = ?", (_PREFIX + key,))
    except Exception:  # noqa: BLE001
        logger.debug("forgetting the ack for %r failed", key, exc_info=True)


__all__ = ["acknowledge", "acknowledged", "forget", "key_for"]
