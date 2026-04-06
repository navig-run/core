"""SQLite-backed identity store.

Schema:
    navig_identities (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        display_name TEXT,
        ton_wallet_address TEXT,
        ton_verified INTEGER DEFAULT 0,
        preferred_channel TEXT DEFAULT 'telegram',
        matrix_user_id TEXT,
        language TEXT DEFAULT 'en',
        timezone TEXT,
        socials TEXT,      -- JSON array
        metadata TEXT,     -- JSON object
        created_at TEXT,
        updated_at TEXT
    )
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from navig.identity.models import SocialLink, UserProfile
from navig.platform import paths

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS navig_identities (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    display_name    TEXT,
    ton_wallet_address TEXT,
    ton_verified    INTEGER DEFAULT 0,
    preferred_channel TEXT DEFAULT 'telegram',
    matrix_user_id  TEXT,
    language        TEXT DEFAULT 'en',
    timezone        TEXT,
    socials         TEXT DEFAULT '[]',
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT,
    updated_at      TEXT
);
"""

# ── Singleton ───────────────────────────────────────────────────────────

_store: IdentityStore | None = None


def get_identity_store(db_path: Path | None = None) -> IdentityStore:
    """Return (or create) the global IdentityStore singleton."""
    global _store
    if _store is None:
        if db_path is None:
            db_path = paths.data_dir() / "identity.db"
        _store = IdentityStore(db_path)
    return _store


def get_user_preferred_channel(user_id: str) -> str | None:
    """Helper called by comms dispatcher for channel="auto".

    ``user_id`` may be a stringified telegram_id.
    """
    try:
        store = get_identity_store()
        tid = int(user_id)
        profile = store.get(tid)
        return profile.preferred_channel if profile else None
    except (ValueError, Exception):
        return None


# ── Store class ─────────────────────────────────────────────────────────


class IdentityStore:
    """SQLite-backed identity CRUD."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # connections shared across threads via lock
        )
        self._lock = threading.Lock()  # guards all write operations
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        logger.info("IdentityStore initialised at %s", self.db_path)

    # ---- CRUD ----------------------------------------------------------

    def get(self, telegram_id: int) -> UserProfile | None:
        row = self._conn.execute(
            "SELECT * FROM navig_identities WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return self._row_to_profile(row) if row else None

    def get_or_create(self, telegram_id: int, **kwargs) -> UserProfile:
        profile = self.get(telegram_id)
        if profile:
            return profile
        now = datetime.now().isoformat()  # utcnow() deprecated in Py3.12+
        profile = UserProfile(telegram_id=telegram_id, **kwargs)
        with self._lock:
            self._conn.execute(
                """INSERT INTO navig_identities
               (telegram_id, username, display_name, preferred_channel,
                language, socials, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '[]', '{}', ?, ?)""",
                (
                    telegram_id,
                    profile.username,
                    profile.display_name,
                    profile.preferred_channel,
                    profile.language,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return self.get(telegram_id)

    def save(self, profile: UserProfile) -> None:
        now = datetime.now().isoformat()  # utcnow() deprecated in Py3.12+
        socials_json = json.dumps(
            [
                {"platform": s.platform, "handle": s.handle, "verified": s.verified}
                for s in profile.socials
            ]
        )
        metadata_json = json.dumps(profile.metadata)
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO navig_identities
               (telegram_id, username, display_name, ton_wallet_address,
                ton_verified, preferred_channel, matrix_user_id, language,
                timezone, socials, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile.telegram_id,
                    profile.username,
                    profile.display_name,
                    profile.ton_wallet_address,
                    int(profile.ton_verified),
                    profile.preferred_channel,
                    profile.matrix_user_id,
                    profile.language,
                    profile.timezone,
                    socials_json,
                    metadata_json,
                    profile.created_at.isoformat(),
                    now,
                ),
            )
            self._conn.commit()

    def delete(self, telegram_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM navig_identities WHERE telegram_id = ?",
            (telegram_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_all(self, limit: int = 100) -> list[UserProfile]:
        rows = self._conn.execute(
            "SELECT * FROM navig_identities ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_profile(r) for r in rows]

    def search_by_wallet(self, wallet: str) -> UserProfile | None:
        row = self._conn.execute(
            "SELECT * FROM navig_identities WHERE ton_wallet_address = ?",
            (wallet,),
        ).fetchone()
        return self._row_to_profile(row) if row else None

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM navig_identities").fetchone()[0]

    # ---- Private -------------------------------------------------------

    def _row_to_profile(self, row) -> UserProfile:
        socials_raw = json.loads(row["socials"] or "[]")
        socials = [
            SocialLink(
                platform=s["platform"],
                handle=s["handle"],
                verified=s.get("verified", False),
            )
            for s in socials_raw
        ]
        return UserProfile(
            telegram_id=row["telegram_id"],
            username=row["username"],
            display_name=row["display_name"],
            ton_wallet_address=row["ton_wallet_address"],
            ton_verified=bool(row["ton_verified"]),
            socials=socials,
            preferred_channel=row["preferred_channel"] or "telegram",
            matrix_user_id=row["matrix_user_id"],
            language=row["language"] or "en",
            timezone=row["timezone"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else datetime.now()  # utcnow() deprecated in Py3.12+
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else datetime.now()  # utcnow() deprecated in Py3.12+
            ),
        )

    def close(self):
        self._conn.close()
