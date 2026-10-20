"""Shared SQLite store for the notification system.

One DB (`~/.navig/data/store/notify.db`, same engine as bizops) holds the
per-type×channel routing matrix, notification settings/targets, and the deck
feed. Mirrors the idempotent `init_db()` pattern used by bizops.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from navig.notify.types import CHANNEL_KEYS, NOTIFICATION_TYPES
from navig.platform import paths
from navig.storage.engine import get_engine

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_matrix (
  type TEXT NOT NULL,
  channel TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (type, channel)
);

CREATE TABLE IF NOT EXISTS notify_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notify_feed (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  priority TEXT NOT NULL DEFAULT 'normal',
  data TEXT,
  created_at TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_feed_created ON notify_feed(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_unread ON notify_feed(read, created_at DESC);

-- Inbound "Signals" ingest sources: one HMAC-signed endpoint per source. The
-- secret authenticates a website firing events at /api/ingest/<name>; the row
-- maps the payload onto a notification type/priority via optional templates.
CREATE TABLE IF NOT EXISTS notify_signal_sources (
  name TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  notify_type TEXT NOT NULL DEFAULT 'signal_event',
  priority TEXT NOT NULL DEFAULT 'normal',
  title_tmpl TEXT,
  body_tmpl TEXT,
  preset TEXT,
  created_at TEXT NOT NULL,
  last_event_at TEXT,
  hit_count INTEGER NOT NULL DEFAULT 0
);

-- Delivered provider event ids (Stripe ``evt_…``), so a redelivery is acked as a
-- duplicate instead of ringing twice. Durable on purpose: Stripe retries for
-- days, far past the in-memory replay window the HMAC sources rely on.
CREATE TABLE IF NOT EXISTS notify_signal_seen (
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (source, event_id)
);

-- What we LAST TOLD the operator about one (host, metric): "firing" or "cleared".
-- The system-events bridge reads it before announcing so a restart does not re-fire
-- an alert that is still unresolved, and so an all-clear only goes out for something
-- that was actually announced. `since` is when that state began, `last_value` the
-- reading that produced it (disk %, service latency…) when the event carried one.
-- `event_type` is WHICH event put the latch there — the bridge reads it to tell a
-- repeat of the same alert (suppress) from a severity escalation inside the same
-- family (a cert warning that has now expired → dispatch).
CREATE TABLE IF NOT EXISTS alert_state (
  host TEXT NOT NULL,
  metric TEXT NOT NULL,
  state TEXT NOT NULL,
  since REAL NOT NULL,
  last_value REAL,
  event_type TEXT,
  PRIMARY KEY (host, metric)
);
"""

# Settings seeded once on first init (user edits persist via INSERT OR IGNORE).
_DEFAULT_SETTINGS = {
    "master_enabled": "1",
    "quiet_hours_enabled": "0",
    "quiet_hours_start": "23",
    "quiet_hours_end": "7",
    "briefing_enabled": "0",
    "briefing_times": "08:00",        # comma-separated HH:MM
    "briefing_channels": "deck,telegram",
    # Resolved inbound-SMS webhook (auto-synced to the public tunnel/domain).
    "sms_webhook_url": "",
    "sms_webhook_base": "",
    # Per-channel delivery targets (blank → fall back to configured default).
    "target_telegram": "",
    "target_email": "",
    "target_sms": "",
    "target_discord": "",
    "target_whatsapp": "",
    "target_matrix": "",
}

_initialised = False


def _db_path() -> Path:
    return paths.data_dir() / "store" / "notify.db"


def conn() -> sqlite3.Connection:
    return get_engine().connect(_db_path())


def init_db() -> None:
    """Create tables + seed the matrix/settings once. Idempotent."""
    global _initialised
    if _initialised:
        return
    c = conn()
    with c:
        c.executescript(_SCHEMA)
        # Migrate dev DBs created before the `preset` column existed.
        cols = {r[1] for r in c.execute("PRAGMA table_info(notify_signal_sources)").fetchall()}
        if "preset" not in cols:
            c.execute("ALTER TABLE notify_signal_sources ADD COLUMN preset TEXT")
        # `provider` (NULL = NAVIG HMAC signing; "stripe" = a Stripe webhook endpoint).
        if "provider" not in cols:
            c.execute("ALTER TABLE notify_signal_sources ADD COLUMN provider TEXT")
        # Migrate latches created before `event_type` existed (NULL there just
        # means "an older latch — which event set it is unknown"). Its own `cols`:
        # rebinding the signal-sources one above would mis-check `provider`.
        alert_cols = {r[1] for r in c.execute("PRAGMA table_info(alert_state)").fetchall()}
        if "event_type" not in alert_cols:
            c.execute("ALTER TABLE alert_state ADD COLUMN event_type TEXT")
        # Seed the matrix: one row per (type, channel), enabled per the type's
        # defaults. INSERT OR IGNORE preserves user toggles across restarts and
        # auto-fills cells for any newly added type/channel.
        for t in NOTIFICATION_TYPES:
            defaults = set(t.get("default_channels") or [])
            for ch in CHANNEL_KEYS:
                c.execute(
                    "INSERT OR IGNORE INTO notify_matrix (type, channel, enabled) VALUES (?, ?, ?)",
                    (t["key"], ch, 1 if ch in defaults else 0),
                )
        for k, v in _DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO notify_settings (key, value) VALUES (?, ?)", (k, v))
    _initialised = True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── alert_state: the last announcement per (host, metric) ────────────────────
# Written by navig.notify.producers.system_events_bridge. Three tiny helpers, all
# going through `conn()` like every other writer here: `get_engine().connect()`
# hands back a THREAD-LOCAL connection, so each caller serialises on its own and
# the `with c:` block is what commits.


def load_alert_states() -> dict[tuple[str, str], dict]:
    """Every latch, keyed by ``(host, metric)`` → ``{state, since, last_value, event_type}``.

    Empty when nothing has been announced yet. `init_db()` first so a caller never
    sees "no such table" on a store this process has not opened before. ``event_type``
    is ``None`` on rows written before that column existed.
    """
    init_db()
    rows = conn().execute(
        "SELECT host, metric, state, since, last_value, event_type FROM alert_state"
    ).fetchall()
    return {
        (r["host"], r["metric"]): {
            "state": r["state"],
            "since": r["since"],
            "last_value": r["last_value"],
            "event_type": r["event_type"],
        }
        for r in rows
    }


def save_alert_state(
    host: str,
    metric: str,
    state: str,
    *,
    since: float | None = None,
    last_value: float | None = None,
    event_type: str | None = None,
) -> None:
    """Upsert the latch for ``(host, metric)``. ``since`` defaults to now.

    ``state`` is a producer vocabulary word — the bridge writes ``"firing"`` and
    ``"cleared"`` — so a reader must not assume more values than were written.
    ``event_type`` names the event that produced the state; a caller that omits it
    keeps whatever an earlier write recorded (COALESCE) rather than erasing it.
    """
    init_db()
    ts = time.time() if since is None else since
    c = conn()
    with c:
        c.execute(
            "INSERT INTO alert_state (host, metric, state, since, last_value, event_type) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(host, metric) DO UPDATE SET "
            "state = excluded.state, "
            "since = excluded.since, "
            "last_value = excluded.last_value, "
            "event_type = COALESCE(excluded.event_type, alert_state.event_type)",
            (host, metric, state, ts, last_value, event_type),
        )
    _maybe_prune_alert_state()


# alert_state is one row per (host, metric) — tiny while hosts churn slowly, but a
# retired host (or a metric nobody clears) leaves its latch forever, and the table
# is read on every monitor start. Retention mirrors notify/feed.py: amortized,
# best-effort, and NEVER allowed to break the write that triggered it.
ALERT_STATE_RETENTION_S = 30 * 86400
_ALERT_STATE_PRUNE_EVERY = 50
_alert_saves_since_prune = 0


def prune_alert_state_rows(retention_s: int = ALERT_STATE_RETENTION_S, *, now: float | None = None) -> int:
    """Delete latches whose last edge is older than *retention_s*. Returns rows removed.

    A firing latch that old belongs to a host that vanished months ago: dropping it
    means a host that returns and fires again ANNOUNCES once instead of being
    silently suppressed forever by a row nothing will ever update.
    """
    init_db()
    cutoff = (now if now is not None else time.time()) - int(retention_s)
    c = conn()
    with c:
        cur = c.execute("DELETE FROM alert_state WHERE since < ?", (cutoff,))
    return cur.rowcount or 0


def _maybe_prune_alert_state() -> None:
    """Amortized retention: one prune every _ALERT_STATE_PRUNE_EVERY saves."""
    global _alert_saves_since_prune
    _alert_saves_since_prune += 1
    if _alert_saves_since_prune >= _ALERT_STATE_PRUNE_EVERY:
        _alert_saves_since_prune = 0
        try:
            prune_alert_state_rows()
        except Exception:  # noqa: BLE001 — retention must never break a latch write
            pass


def clear_alert_state(host: str, metric: str) -> bool:
    """Forget the ``(host, metric)`` latch entirely. True when a row was removed.

    Distinct from :func:`save_alert_state` writing ``"cleared"``: that keeps the
    row (and its ``since``) so a reader can tell "we announced the all-clear" from
    "we never said anything". This drops the history — use it when the host or
    metric itself is retired, not to record a recovery.
    """
    init_db()
    c = conn()
    with c:
        cur = c.execute(
            "DELETE FROM alert_state WHERE host = ? AND metric = ?", (host, metric)
        )
    return cur.rowcount == 1


def new_id() -> str:
    return uuid.uuid4().hex[:16]
