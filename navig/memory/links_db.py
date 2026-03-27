"""
NAVIG Browser Links Database

SQLite-backed bookmark store with full-text search (FTS5).
Each link can be associated with a vault credential for auto-login.

Schema:
    links        — main table (url, title, notes, tags, vault_cred_id, ...)
    links_fts    — FTS5 virtual table (url, title, notes, tags)

Usage:
    from navig.memory.links_db import get_links_db

    db = get_links_db()
    link_id = db.add("https://github.com", title="GitHub", notes="Work account", vault_cred_id="abc12345")
    results  = db.search("github work")
    db.record_visit(link_id)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# ─────────────────────────── schema ──────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT,
    notes           TEXT,
    tags            TEXT DEFAULT '[]',         -- JSON array of strings
    vault_cred_id   TEXT,                      -- FK to NAVIG vault credential
    last_visited    DATETIME,
    visit_count     INTEGER DEFAULT 0,
    screenshot_path TEXT,
    favicon_path    TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS links_fts USING fts5(
    id UNINDEXED,
    url,
    title,
    notes,
    tags,
    content='links',
    content_rowid='rowid'
);

-- Keep FTS in sync via triggers
CREATE TRIGGER IF NOT EXISTS links_fts_insert AFTER INSERT ON links BEGIN
    INSERT INTO links_fts(rowid, id, url, title, notes, tags)
    VALUES (new.rowid, new.id, new.url, COALESCE(new.title,''), COALESCE(new.notes,''), COALESCE(new.tags,''));
END;

CREATE TRIGGER IF NOT EXISTS links_fts_update AFTER UPDATE ON links BEGIN
    UPDATE links_fts SET url=new.url, title=COALESCE(new.title,''), notes=COALESCE(new.notes,''), tags=COALESCE(new.tags,'')
    WHERE id=new.id;
END;

CREATE TRIGGER IF NOT EXISTS links_fts_delete AFTER DELETE ON links BEGIN
    DELETE FROM links_fts WHERE id=old.id;
END;
"""

# ─────────────────────────── data model ──────────────────────────────────────


class LinkRecord:
    """Represents a single bookmarked link."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.id: str = row["id"]
        self.url: str = row["url"]
        self.title: str | None = row.get("title")
        self.notes: str | None = row.get("notes")
        self.tags: list[str] = json.loads(row.get("tags") or "[]")
        self.vault_cred_id: str | None = row.get("vault_cred_id")
        self.last_visited: datetime | None = (
            datetime.fromisoformat(row["last_visited"]) if row.get("last_visited") else None
        )
        self.visit_count: int = int(row.get("visit_count") or 0)
        self.screenshot_path: str | None = row.get("screenshot_path")
        self.favicon_path: str | None = row.get("favicon_path")
        self.created_at: datetime = datetime.fromisoformat(row["created_at"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "notes": self.notes,
            "tags": self.tags,
            "vault_cred_id": self.vault_cred_id,
            "last_visited": (self.last_visited.isoformat() if self.last_visited else None),
            "visit_count": self.visit_count,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────── database class ──────────────────────────────────


class LinksDB:
    """SQLite-backed browser links database with FTS5 search."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    # ─────────────────────── write operations ──────────────────────────────

    def add(
        self,
        url: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        vault_cred_id: str | None = None,
        screenshot_path: str | None = None,
        favicon_path: str | None = None,
    ) -> str:
        """Add a new link. Returns the new link ID."""
        link_id = str(uuid.uuid4())[:8]
        tags_json = json.dumps(tags or [])
        self._con.execute(
            """INSERT INTO links
               (id, url, title, notes, tags, vault_cred_id, screenshot_path, favicon_path)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                link_id,
                url,
                title,
                notes,
                tags_json,
                vault_cred_id,
                screenshot_path,
                favicon_path,
            ),
        )
        self._con.commit()
        return link_id

    def update(
        self,
        link_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        vault_cred_id: str | None = None,
    ) -> bool:
        """Update fields on an existing link. Returns True if found."""
        link = self.get(link_id)
        if not link:
            return False
        new_title = title if title is not None else link.title
        new_notes = notes if notes is not None else link.notes
        new_tags = tags if tags is not None else link.tags
        new_cred = vault_cred_id if vault_cred_id is not None else link.vault_cred_id
        self._con.execute(
            """UPDATE links SET title=?, notes=?, tags=?, vault_cred_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (new_title, new_notes, json.dumps(new_tags), new_cred, link_id),
        )
        self._con.commit()
        return True

    def delete(self, link_id: str) -> bool:
        """Delete a link by ID. Returns True if found and deleted."""
        cur = self._con.execute("DELETE FROM links WHERE id=?", (link_id,))
        self._con.commit()
        return cur.rowcount > 0

    def record_visit(self, link_id: str) -> None:
        """Increment visit_count and update last_visited timestamp."""
        self._con.execute(
            """UPDATE links SET visit_count=visit_count+1, last_visited=CURRENT_TIMESTAMP
               WHERE id=?""",
            (link_id,),
        )
        self._con.commit()

    # ─────────────────────── read operations ───────────────────────────────

    def get(self, link_id: str) -> LinkRecord | None:
        """Get a link by its ID."""
        row = self._con.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
        return LinkRecord(dict(row)) if row else None

    def get_by_url(self, url: str) -> LinkRecord | None:
        """Get a link by exact URL."""
        row = self._con.execute("SELECT * FROM links WHERE url=?", (url,)).fetchone()
        return LinkRecord(dict(row)) if row else None

    def list_all(self, limit: int = 100, offset: int = 0) -> list[LinkRecord]:
        """List all links ordered by last_visited (most recent first)."""
        rows = self._con.execute(
            "SELECT * FROM links ORDER BY COALESCE(last_visited, created_at) DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [LinkRecord(dict(r)) for r in rows]

    def list_by_tag(self, tag: str) -> list[LinkRecord]:
        """List links that have a specific tag."""
        rows = self._con.execute(
            "SELECT * FROM links WHERE tags LIKE ?",
            (f'%"{tag}"%',),
        ).fetchall()
        return [LinkRecord(dict(r)) for r in rows]

    def list_with_vault_cred(self, vault_cred_id: str) -> list[LinkRecord]:
        """List all links associated with a specific vault credential."""
        rows = self._con.execute(
            "SELECT * FROM links WHERE vault_cred_id=?", (vault_cred_id,)
        ).fetchall()
        return [LinkRecord(dict(r)) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[LinkRecord]:
        """Full-text search across url, title, notes, and tags.

        Uses SQLite FTS5 for fast fuzzy matching. Query supports boolean
        operators: AND, OR, NOT, prefix* matching.
        """
        if not query.strip():
            return self.list_all(limit=limit)
        try:
            rows = self._con.execute(
                """SELECT l.* FROM links l
                   INNER JOIN links_fts fts ON l.id = fts.id
                   WHERE links_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [LinkRecord(dict(r)) for r in rows]
        except sqlite3.OperationalError:
            # FTS syntax error: fallback to LIKE search
            like = f"%{query}%"
            rows = self._con.execute(
                "SELECT * FROM links WHERE url LIKE ? OR title LIKE ? OR notes LIKE ? LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            return [LinkRecord(dict(r)) for r in rows]

    def close(self) -> None:
        self._con.close()


# ─────────────────────────── singleton ───────────────────────────────────────

_db_instance: LinksDB | None = None


def get_links_db() -> LinksDB:
    """Return the singleton LinksDB, initialised from the NAVIG data directory."""
    global _db_instance
    if _db_instance is None:
        from navig.config import get_config

        cfg = get_config()
        db_path = Path(cfg.data_dir) / "links.db"
        _db_instance = LinksDB(db_path)
    return _db_instance
