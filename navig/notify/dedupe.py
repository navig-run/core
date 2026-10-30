"""Announce once per cooldown — across processes and restarts.

An in-memory throttle forgets everything when the daemon restarts, and each
process (gateway, worker) has its own. That is how one unchanged heartbeat
finding ("Anthropic key rejected") reached Telegram five times in one afternoon
behind a 6-hour cooldown: the daemon was restarted in between, and every fresh
process thought it had never said it.

The claim lives in ``notify_settings`` (``dedupe:<key>`` → epoch seconds) and is
taken with ONE atomic UPSERT, so two processes racing for the same key cannot
both win: only a row that is absent or older than the cooldown is (re)written,
and ``changes()`` says whether this caller wrote it.
"""

from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger("navig.notify.dedupe")

_PREFIX = "dedupe:"

#: An identical notification inside this window is one event reported twice.
#: Five minutes is longer than any retry burst (the operator's five "💰 Sale —
#: $19.99" cards all landed inside one minute) and shorter than a genuine repeat —
#: a second sale, a re-fired reminder or a monitor that alerts again all differ in
#: a word or a number, and the same words twice inside five minutes is a bug.
COLLAPSE_WINDOW_S = 300.0


def payload_key(namespace: str, *parts: str) -> str:
    """A claim key for one rendered message, in *namespace* (a channel or route).

    Keying on the payload rather than on a provider event id is what makes this a
    wall behind every other dedupe: five deliveries that each carry a fresh event
    id — a retry loop outside our control, two senders with the same token, a
    producer with a loop, an operator tapping a test button five times — still
    render the same words, so the fifth copy is stopped even when the first four
    were not.
    """
    digest = hashlib.sha1("\n".join((namespace, *parts)).encode()).hexdigest()[:16]
    return f"payload:{namespace}:{digest}"


def claim(key: str, cooldown_s: float, *, now: float | None = None) -> bool:
    """True when the caller may announce *key* now (and has reserved it).

    An unreadable store answers True: a duplicate alert is better than a lost one.
    """
    now = time.time() if now is None else now
    try:
        from navig.notify import store

        store.init_db()
        c = store.conn()
        with c:
            cur = c.execute(
                "INSERT INTO notify_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
                "WHERE CAST(notify_settings.value AS REAL) <= ?",
                (_PREFIX + key, repr(now), now - cooldown_s),
            )
        return cur.rowcount == 1
    except Exception:  # noqa: BLE001
        logger.warning("dedupe claim for %r failed — announcing anyway", key, exc_info=True)
        return True


def release(key: str) -> None:
    """Give a claim back (the announcement reached no channel)."""
    try:
        from navig.notify import store

        c = store.conn()
        with c:
            c.execute("DELETE FROM notify_settings WHERE key = ?", (_PREFIX + key,))
    except Exception:  # noqa: BLE001
        logger.debug("dedupe release for %r failed", key, exc_info=True)
