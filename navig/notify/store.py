"""Shared SQLite store for the notification system.

One DB (`~/.navig/data/store/notify.db`, same engine as bizops) holds the
per-type×channel routing matrix, notification settings/targets, and the deck
feed. Mirrors the idempotent `init_db()` pattern used by bizops.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from navig.platform import paths
from navig.storage.engine import get_engine

from navig.notify.types import NOTIFICATION_TYPES, CHANNEL_KEYS

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


def new_id() -> str:
    return uuid.uuid4().hex[:16]
