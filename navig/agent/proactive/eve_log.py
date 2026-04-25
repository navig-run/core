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

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

_MAX_DAYS: int = 30
_FILE_NAME: str = "eve_log.json"


class EveEntry(TypedDict, total=False):
    shipped: str
    priority: str
    shipped_at: str
    priority_at: str


def _log_path() -> Path:
    base = Path(os.environ.get("NAVIG_CONFIG_DIR", Path.home() / ".navig"))
    path = base / "engagement" / _FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> dict[str, EveEntry]:
    path = _log_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("eve_log: failed to load %s, starting fresh", path)
        return {}


def _save(data: dict[str, EveEntry]) -> None:
    # Trim to last _MAX_DAYS entries
    if len(data) > _MAX_DAYS:
        keys = sorted(data.keys())[-_MAX_DAYS:]
        data = {k: data[k] for k in keys}
    path = _log_path()
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        )
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        tmp.close()
        os.replace(tmp.name, path)
    except Exception as exc:
        logger.warning("eve_log: save failed: %s", exc)


def save_shipped(text: str, date: str | None = None) -> None:
    """Record what shipped today."""
    key = date or datetime.now().strftime("%Y-%m-%d")
    data = _load()
    entry: EveEntry = dict(data.get(key) or {})  # type: ignore[arg-type]
    entry["shipped"] = text.strip()
    entry["shipped_at"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save(data)


def save_priority(text: str, date: str | None = None) -> None:
    """Record tomorrow's anchor priority (entered on evening of date)."""
    key = date or datetime.now().strftime("%Y-%m-%d")
    data = _load()
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
