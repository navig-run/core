from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT,
    source      TEXT,
    meta_json   TEXT DEFAULT '{}',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


class ContactsStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path))
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def add(self, name: str, phone: str | None = None, source: str = "manual") -> int:
        normalized_phone = normalize_phone(phone)
        cur = self._con.execute(
            "INSERT INTO contacts(name, phone, source) VALUES (?,?,?)",
            (name, normalized_phone or None, source),
        )
        self._con.commit()
        return int(cur.lastrowid)

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT id, name, phone, source, created_at FROM contacts ORDER BY name ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_by_phone(self, phone: str) -> dict[str, Any] | None:
        normalized_phone = normalize_phone(phone)
        if not normalized_phone:
            return None
        row = self._con.execute(
            "SELECT id, name, phone, source FROM contacts WHERE phone = ?",
            (normalized_phone,),
        ).fetchone()
        return dict(row) if row else None


def normalize_phone(phone: str | None) -> str:
    raw = str(phone or "").strip()
    if not raw:
        return ""
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if has_plus else digits


_store: ContactsStore | None = None


def get_contacts_store() -> ContactsStore:
    global _store
    if _store is None:
        from navig.config import get_config

        cfg = get_config()
        db_path = Path(cfg.data_dir) / "contacts.db"
        _store = ContactsStore(db_path)
    return _store
