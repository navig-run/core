"""
Evening Log — lightweight daily capture for shipped work and tomorrow's anchor.

Persists to ~/.navig/engagement/eve_log.json keyed by ISO date (YYYY-MM-DD).
Designed for fast atomic reads/writes; no SQL, no migrations required.

Schema:
    {
        "2026-04-25": {
            "shipped": "Fixed login bug · Deployed v2.3 · Reviewed 4 PRs",
            "priority": "Ship the auth refactor",
            "shipped_at": "2026-04-25T20:13:00",
            "priority_at": "2026-04-25T20:15:00"
        },
        ...
    }

Only the last 30 days are retained to keep the file small.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from navig.core.json_io import atomic_write_json, load_json_for_update, load_json_safe

logger = logging.getLogger(__name__)

_MAX_DAYS: int = 30
_FILE_NAME: str = "eve_log.json"


class EveEntry(TypedDict, total=False):
    shipped: str
    priority: str
    shipped_at: str
    priority_at: str


def _log_path() -> Path:
    # `config_dir()` rather than a hand-rolled NAVIG_CONFIG_DIR-or-home: the canonical
    # resolver also handles the system-service case (`/etc/navig`), which the hand-rolled
    # copy silently missed. Re-deriving a canonical path is how the two spellings drift.
    from navig.platform.paths import config_dir

    path = config_dir() / "engagement" / _FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> dict[str, EveEntry]:
    # Read-only view (get_today/get_yesterday/get_entry): degrade to empty on any failure so a
    # briefing read never crashes. A read-modify-write MUST use _load_for_update instead, or a
    # transient lock here returns {} and the following _save wipes the 30-day journal.
    return load_json_safe(_log_path(), default={})


def _load_for_update() -> dict[str, EveEntry]:
    """Load the journal for a read-modify-write (save_shipped/save_priority).

    Raises ``JsonReadError`` when the file exists-with-content but is transiently unreadable
    (a Windows AV/backup lock), so the caller aborts before _save persists a single-day
    entry over the whole journal. telegram_commands wraps the save in try/except, so this
    surfaces as a benign "noted" with the history intact.
    """
    return load_json_for_update(_log_path(), default={})


def _save(data: dict[str, EveEntry]) -> None:
    """Persist the journal, or RAISE.

    This used to hand-roll the atomic write (NamedTemporaryFile + os.replace) and
    swallow every failure with a log line, which made it lie twice over: the
    ``.tmp`` file was left next to the journal on any error — and this module's own
    docstring says Windows AV/backup locks are the expected failure here — and,
    worse, ``save_shipped()`` returned normally so the bot replied
    "✅ Logged: <the thing you typed>" for an entry that was never written.

    It now delegates to :func:`atomic_write_json`, the canonical writer already used
    by 29 other call sites (temp-file + fsync + atomic replace, transient-lock retry
    with back-off, and the temp removed on every failure path), and lets failure
    propagate so the caller can tell the user the truth.
    """
    # Trim to last _MAX_DAYS entries
    if len(data) > _MAX_DAYS:
        keys = sorted(data.keys())[-_MAX_DAYS:]
        data = {k: data[k] for k in keys}
    atomic_write_json(data, _log_path())


def save_shipped(text: str, date: str | None = None) -> None:
    """Record what shipped today."""
    key = date or datetime.now().strftime("%Y-%m-%d")
    data = _load_for_update()
    entry: EveEntry = dict(data.get(key) or {})  # type: ignore[arg-type]
    entry["shipped"] = text.strip()
    entry["shipped_at"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save(data)


def save_priority(text: str, date: str | None = None) -> None:
    """Record tomorrow's anchor priority (entered on evening of date)."""
    key = date or datetime.now().strftime("%Y-%m-%d")
    data = _load_for_update()
    entry: EveEntry = dict(data.get(key) or {})  # type: ignore[arg-type]
    entry["priority"] = text.strip()
    entry["priority_at"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save(data)


def get_today() -> EveEntry:
    """Return today's entry (empty dict if nothing logged yet)."""
    key = datetime.now().strftime("%Y-%m-%d")
    return _load().get(key) or {}


def get_yesterday() -> EveEntry:
    """Return yesterday's entry — useful for morning briefing anchor."""
    key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return _load().get(key) or {}


def get_entry(date: str) -> EveEntry:
    """Return a specific date's entry (YYYY-MM-DD)."""
    return _load().get(date) or {}
