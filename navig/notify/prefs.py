"""Notification preferences — the per-type×channel routing matrix, global
settings (master toggle, quiet hours, briefing schedule), and per-channel
delivery targets. All persisted in notify.db."""

from __future__ import annotations

from typing import Any

from navig.notify import store
from navig.notify.types import CHANNEL_KEYS, TYPE_KEYS


# ── Matrix ───────────────────────────────────────────────────────────────────

def get_matrix() -> dict[str, dict[str, bool]]:
    """Return {type: {channel: enabled}} for every type/channel."""
    store.init_db()
    rows = store.conn().execute("SELECT type, channel, enabled FROM notify_matrix").fetchall()
    out: dict[str, dict[str, bool]] = {t: {c: False for c in CHANNEL_KEYS} for t in TYPE_KEYS}
    for r in rows:
        out.setdefault(r["type"], {})[r["channel"]] = bool(r["enabled"])
    return out


def is_enabled(type_key: str, channel: str) -> bool:
    store.init_db()
    r = store.conn().execute(
        "SELECT enabled FROM notify_matrix WHERE type = ? AND channel = ?",
        (type_key, channel),
    ).fetchone()
    return bool(r["enabled"]) if r else False


def enabled_channels(type_key: str) -> list[str]:
    store.init_db()
    rows = store.conn().execute(
        "SELECT channel FROM notify_matrix WHERE type = ? AND enabled = 1", (type_key,)
    ).fetchall()
    return [r["channel"] for r in rows]


def _is_known_type(type_key: str) -> bool:
    # Static taxonomy + dynamic per-source Signals rows (signal:<source>).
    return type_key in TYPE_KEYS or type_key.startswith("signal:")


def set_cell(type_key: str, channel: str, enabled: bool) -> None:
    if not _is_known_type(type_key) or channel not in CHANNEL_KEYS:
        raise ValueError("unknown notification type or channel")
    store.init_db()
    c = store.conn()
    with c:
        c.execute(
            "INSERT INTO notify_matrix (type, channel, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(type, channel) DO UPDATE SET enabled = excluded.enabled",
            (type_key, channel, 1 if enabled else 0),
        )


def seed_type(type_key: str, default_channels: list[str]) -> None:
    """Create matrix rows for a dynamic type (e.g. a new signal source).

    Idempotent — INSERT OR IGNORE preserves any toggles the user already set.
    """
    store.init_db()
    defaults = set(default_channels or [])
    c = store.conn()
    with c:
        for ch in CHANNEL_KEYS:
            c.execute(
                "INSERT OR IGNORE INTO notify_matrix (type, channel, enabled) VALUES (?, ?, ?)",
                (type_key, ch, 1 if ch in defaults else 0),
            )


def delete_type(type_key: str) -> None:
    """Drop all matrix rows for a dynamic type (when its source is removed)."""
    store.init_db()
    c = store.conn()
    with c:
        c.execute("DELETE FROM notify_matrix WHERE type = ?", (type_key,))


# ── Settings + targets ───────────────────────────────────────────────────────

def get_settings() -> dict[str, Any]:
    store.init_db()
    rows = store.conn().execute("SELECT key, value FROM notify_settings").fetchall()
    raw = {r["key"]: r["value"] for r in rows}

    def _bool(k: str) -> bool:
        return raw.get(k, "0") in ("1", "true", "yes", "True")

    def _int(k: str, default: int) -> int:
        try:
            return int(raw.get(k, default))
        except (TypeError, ValueError):
            return default

    return {
        "master_enabled": _bool("master_enabled"),
        "quiet_hours_enabled": _bool("quiet_hours_enabled"),
        "quiet_hours_start": _int("quiet_hours_start", 23),
        "quiet_hours_end": _int("quiet_hours_end", 7),
        "briefing_enabled": _bool("briefing_enabled"),
        "briefing_times": [t for t in (raw.get("briefing_times", "") or "").split(",") if t.strip()],
        "briefing_channels": [c for c in (raw.get("briefing_channels", "") or "").split(",") if c.strip()],
        "sms_webhook_url": raw.get("sms_webhook_url", "") or "",
        "targets": {
            ch: raw.get(f"target_{ch}", "") or "" for ch in CHANNEL_KEYS if ch != "deck"
        },
    }


def set_setting(key: str, value: Any) -> None:
    """Set a scalar setting. Lists (briefing_times/channels) are comma-joined."""
    store.init_db()
    allowed = {
        "master_enabled", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
        "briefing_enabled", "briefing_times", "briefing_channels",
        "sms_webhook_url", "sms_webhook_base",
    }
    allowed |= {f"target_{ch}" for ch in CHANNEL_KEYS}
    if key not in allowed:
        raise ValueError(f"unknown setting: {key}")
    if isinstance(value, bool):
        value = "1" if value else "0"
    elif isinstance(value, (list, tuple)):
        value = ",".join(str(v).strip() for v in value if str(v).strip())
    else:
        value = str(value)
    c = store.conn()
    with c:
        c.execute(
            "INSERT INTO notify_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_target(channel: str) -> str:
    store.init_db()
    r = store.conn().execute(
        "SELECT value FROM notify_settings WHERE key = ?", (f"target_{channel}",)
    ).fetchone()
    return (r["value"] if r else "") or ""


def get_raw(key: str, default: str = "") -> str:
    store.init_db()
    r = store.conn().execute("SELECT value FROM notify_settings WHERE key = ?", (key,)).fetchone()
    return (r["value"] if r else default) or default
