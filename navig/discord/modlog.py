"""Local moderation log — every action NAVIG takes on a Discord server, one JSON line each.

Discord keeps its own audit log (and every action carries a ``"<who> via NAVIG"``
reason there), but that log belongs to the server and expires after 45 days. This
one is the operator's: ``<data_dir>/discord/modlog.jsonl``. Recording never raises —
a moderation action must not fail because the log could not be written.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def path() -> Path:
    from navig.platform.paths import data_dir

    return data_dir() / "discord" / "modlog.jsonl"


def record(
    action: str,
    *,
    guild_id: str,
    target_id: str | None = None,
    actor_id: str | None = None,
    surface: str = "cli",
    reason: str | None = None,
    ok: bool = True,
    detail: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "guild_id": str(guild_id),
        "target_id": None if target_id is None else str(target_id),
        "actor_id": actor_id,
        "surface": surface,
        "reason": reason,
        "ok": ok,
    }
    if detail:
        entry["detail"] = detail[:500]
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("discord modlog write failed: %s", exc)


def tail(limit: int = 50, guild_id: str | None = None) -> list[dict[str, Any]]:
    """The newest *limit* entries, newest first (optionally for one server)."""
    p = path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if guild_id is None or row.get("guild_id") == str(guild_id):
            rows.append(row)
    return list(reversed(rows[-limit:]))
