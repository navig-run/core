from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from navig.platform.paths import config_dir

DEFAULT_DB_PATH = config_dir() / "data" / "pattern_log.sqlite"


@dataclass
class PatternRecord:
    command: str


class PatternObserver:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)

    def get_recent(self, limit: int = 500) -> list[PatternRecord]:
        if not self.db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.execute(
                "SELECT command FROM patterns ORDER BY ts DESC LIMIT ?", (int(limit),)
            )
            rows = [PatternRecord(command=str(r[0])) for r in cur.fetchall() if r and r[0]]
            conn.close()
            return rows
        except Exception:
            return []
