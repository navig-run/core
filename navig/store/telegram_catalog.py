"""
TelegramCatalogStore — persistent catalog of the bot's Telegram rooms,
messages, and media for the deck "Telegram network manager" app.

Unlike :class:`navig.store.threads.ThreadStore` (one row per *conversation*),
this store keeps a row per *message* and per *media* item so the deck can
browse, filter, analyse, and search everything the bot has seen.

Hard Bot-API limit: a bot cannot read history from *before* it joined, so the
catalog only grows forward from the moment the bot is present in a room (and,
for groups, only when privacy mode is off). No backfill is possible without
MTProto — that is out of scope by product decision.

Backed by :class:`BaseStore` (WAL, write serialisation, schema versioning).
Quick search uses SQLite FTS5 when available, falling back to LIKE.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# Media kinds we recognise (mirrors Telegram message fields).
MEDIA_KINDS = ("photo", "video", "animation", "voice", "audio", "document", "sticker", "video_note")


class TelegramCatalogStore(BaseStore):
    """Per-message / per-media catalog of the bot's Telegram rooms."""

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -8000}

    def __init__(self, db_path: Path | None = None):
        super().__init__(db_path or _default_db_path())

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tg_rooms (
                chat_id          INTEGER PRIMARY KEY,
                type             TEXT,                       -- private|group|supergroup|channel
                title            TEXT,
                username         TEXT,
                member_count     INTEGER,
                bot_is_admin     INTEGER,                    -- 0/1/NULL(unknown)
                can_post         INTEGER,
                can_delete       INTEGER,
                privacy_ok       INTEGER,                    -- groups: privacy mode off?
                last_message_at  TEXT,
                last_synced      TEXT,
                meta_json        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS tg_media (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id          INTEGER NOT NULL,
                message_id       INTEGER,
                file_id          TEXT,
                file_unique_id   TEXT,
                kind             TEXT,
                mime             TEXT,
                size             INTEGER,
                filename         TEXT,
                local_path       TEXT,
                analysis_status  TEXT DEFAULT 'pending',     -- pending|running|done|error|skipped
                ocr_text         TEXT,
                transcript       TEXT,
                ai_description   TEXT,
                analysis_json    TEXT,
                analyzed_at      TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(file_unique_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tg_media_chat   ON tg_media (chat_id, kind);
            CREATE INDEX IF NOT EXISTS idx_tg_media_status ON tg_media (analysis_status);

            CREATE TABLE IF NOT EXISTS tg_messages (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id          INTEGER NOT NULL,
                message_id       INTEGER NOT NULL,
                sender_id        INTEGER,
                sender_name      TEXT,
                date             TEXT,
                text             TEXT,
                reply_to         INTEGER,
                media_ref        INTEGER,                    -- -> tg_media.id (no FK; insert order)
                kind             TEXT,                       -- 'text' or a media kind
                edited_at        TEXT,
                deleted          INTEGER DEFAULT 0,
                raw_json         TEXT,
                created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tg_msg_chat ON tg_messages (chat_id, message_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tg_msg_kind ON tg_messages (chat_id, kind);
            """
        )
        # Media organization: tags + category (idempotent ALTER for existing DBs).
        for _col in ("tags TEXT", "category TEXT"):
            try:
                conn.execute(f"ALTER TABLE tg_media ADD COLUMN {_col}")
            except sqlite3.OperationalError:
                pass  # column already present
        # Link index — tiktok/youtube/url pulled from messages.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tg_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER,
                url         TEXT NOT NULL,
                provider    TEXT,                 -- tiktok|youtube|telegram|url
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(chat_id, url)
            );
            CREATE INDEX IF NOT EXISTS idx_tg_links_chat ON tg_links (chat_id, provider);
            """
        )
        # FTS5 search index (best-effort — some sqlite builds omit FTS5).
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS tg_search USING fts5("
                "body, chat_id UNINDEXED, ref_kind UNINDEXED, ref_id UNINDEXED)"
            )
        except sqlite3.OperationalError as exc:  # FTS5 not compiled in
            logger.info("FTS5 unavailable (%s); Telegram catalog search falls back to LIKE.", exc)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        pass  # v1 is initial

    # ── FTS helpers ───────────────────────────────────────────

    def _has_fts(self) -> bool:
        row = self._read_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tg_search'"
        )
        return row is not None

    @staticmethod
    def _fts_rowid(ref_kind: str, ref_id: int) -> int:
        # Disjoint rowid space for messages vs media so they never collide.
        return ref_id * 2 + (1 if ref_kind == "media" else 0)

    def _index_fts(self, ref_kind: str, ref_id: int, chat_id: int, body: str) -> None:
        if not body or not body.strip() or not self._has_fts():
            return
        rid = self._fts_rowid(ref_kind, ref_id)
        try:
            self._write("DELETE FROM tg_search WHERE rowid = ?", (rid,))
            self._write(
                "INSERT INTO tg_search (rowid, body, chat_id, ref_kind, ref_id) VALUES (?, ?, ?, ?, ?)",
                (rid, body, chat_id, ref_kind, ref_id),
            )
        except sqlite3.OperationalError as exc:
            logger.debug("FTS index write failed: %s", exc)

    # ── Rooms ─────────────────────────────────────────────────

    def upsert_room(
        self,
        chat_id: int,
        *,
        type: str | None = None,
        title: str | None = None,
        username: str | None = None,
        member_count: int | None = None,
        bot_is_admin: bool | None = None,
        can_post: bool | None = None,
        can_delete: bool | None = None,
        privacy_ok: bool | None = None,
        last_message_at: str | None = None,
        meta: dict[str, Any] | None = None,
        touch_sync: bool = False,
    ) -> None:
        """Insert or merge a room. Only non-None fields overwrite existing values."""
        last_synced = _utcnow() if touch_sync else None
        meta_json = json.dumps(meta) if meta is not None else None
        self._write(
            """
            INSERT INTO tg_rooms
                (chat_id, type, title, username, member_count, bot_is_admin,
                 can_post, can_delete, privacy_ok, last_message_at, last_synced, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                type            = COALESCE(excluded.type, tg_rooms.type),
                title           = COALESCE(excluded.title, tg_rooms.title),
                username        = COALESCE(excluded.username, tg_rooms.username),
                member_count    = COALESCE(excluded.member_count, tg_rooms.member_count),
                bot_is_admin    = COALESCE(excluded.bot_is_admin, tg_rooms.bot_is_admin),
                can_post        = COALESCE(excluded.can_post, tg_rooms.can_post),
                can_delete      = COALESCE(excluded.can_delete, tg_rooms.can_delete),
                privacy_ok      = COALESCE(excluded.privacy_ok, tg_rooms.privacy_ok),
                last_message_at = COALESCE(excluded.last_message_at, tg_rooms.last_message_at),
                last_synced     = COALESCE(excluded.last_synced, tg_rooms.last_synced),
                meta_json       = COALESCE(excluded.meta_json, tg_rooms.meta_json)
            """,
            (
                chat_id, type, title, username, member_count,
                _b(bot_is_admin), _b(can_post), _b(can_delete), _b(privacy_ok),
                last_message_at, last_synced, meta_json,
            ),
        )

    def get_room(self, chat_id: int) -> dict[str, Any] | None:
        row = self._read_one(
            """
            SELECT r.*,
                   (SELECT COUNT(*) FROM tg_messages m WHERE m.chat_id = r.chat_id AND m.deleted = 0) AS message_count,
                   (SELECT COUNT(*) FROM tg_media   d WHERE d.chat_id = r.chat_id) AS media_count
            FROM tg_rooms r WHERE r.chat_id = ?
            """,
            (chat_id,),
        )
        return _room_dict(row) if row else None

    def list_rooms(self, *, type: str | None = None, admin_only: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if type:
            clauses.append("r.type = ?")
            params.append(type)
        if admin_only:
            clauses.append("r.bot_is_admin = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._read_all(
            f"""
            SELECT r.*,
                   (SELECT COUNT(*) FROM tg_messages m WHERE m.chat_id = r.chat_id AND m.deleted = 0) AS message_count,
                   (SELECT COUNT(*) FROM tg_media   d WHERE d.chat_id = r.chat_id) AS media_count
            FROM tg_rooms r {where}
            ORDER BY r.last_message_at DESC NULLS LAST, r.title
            """,
            tuple(params),
        )
        return [_room_dict(r) for r in rows]

    # ── Messages ──────────────────────────────────────────────

    def upsert_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        sender_id: int | None = None,
        sender_name: str | None = None,
        date: str | None = None,
        text: str | None = None,
        reply_to: int | None = None,
        media_ref: int | None = None,
        kind: str | None = None,
        edited_at: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> int:
        """Insert or update a message; returns its local ``id``."""
        raw_json = json.dumps(raw) if raw is not None else None
        self._write(
            """
            INSERT INTO tg_messages
                (chat_id, message_id, sender_id, sender_name, date, text,
                 reply_to, media_ref, kind, edited_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                sender_id   = COALESCE(excluded.sender_id, tg_messages.sender_id),
                sender_name = COALESCE(excluded.sender_name, tg_messages.sender_name),
                date        = COALESCE(excluded.date, tg_messages.date),
                text        = COALESCE(excluded.text, tg_messages.text),
                reply_to    = COALESCE(excluded.reply_to, tg_messages.reply_to),
                media_ref   = COALESCE(excluded.media_ref, tg_messages.media_ref),
                kind        = COALESCE(excluded.kind, tg_messages.kind),
                edited_at   = COALESCE(excluded.edited_at, tg_messages.edited_at),
                raw_json    = COALESCE(excluded.raw_json, tg_messages.raw_json),
                deleted     = 0
            """,
            (
                chat_id, message_id, sender_id, sender_name, date, text,
                reply_to, media_ref, kind or ("text" if media_ref is None else None),
                edited_at, raw_json,
            ),
        )
        row = self._read_one(
            "SELECT id FROM tg_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        mid = int(row["id"]) if row else 0
        if text:
            self._index_fts("message", mid, chat_id, text)
        return mid

    def get_message(self, local_id: int) -> dict[str, Any] | None:
        """Fetch a single message by its local catalog ``id``."""
        row = self._read_one("SELECT * FROM tg_messages WHERE id = ?", (local_id,))
        return _message_dict(row) if row else None

    def get_message_by_ref(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        """Fetch a single message by its Telegram ``(chat_id, message_id)`` ref."""
        row = self._read_one(
            "SELECT * FROM tg_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        return _message_dict(row) if row else None

    # ── Media tags / category + link index ────────────────────────

    def set_media_tags(self, media_id: int, *, tags: list[str] | None = None,
                       category: str | None = None) -> None:
        """Tag/categorize a media row (organization). Only non-None fields change."""
        if tags is not None:
            self._write("UPDATE tg_media SET tags = ? WHERE id = ?",
                        (json.dumps(list(tags)), media_id))
        if category is not None:
            self._write("UPDATE tg_media SET category = ? WHERE id = ?", (category, media_id))

    def add_link(self, chat_id: int, url: str, provider: str | None = None, *,
                 message_id: int | None = None) -> None:
        """Persist a link into the link index (deduped per chat+url)."""
        self._write(
            "INSERT OR IGNORE INTO tg_links (chat_id, message_id, url, provider) VALUES (?, ?, ?, ?)",
            (chat_id, message_id, url, provider),
        )

    def list_links(self, chat_id: int | None = None, *, provider: str | None = None,
                   limit: int = 200) -> list[dict[str, Any]]:
        """List indexed links, optionally filtered by chat / provider (tiktok|youtube|url)."""
        where: list[str] = []
        params: list[Any] = []
        if chat_id is not None:
            where.append("chat_id = ?"); params.append(chat_id)
        if provider:
            where.append("provider = ?"); params.append(provider)
        sql = "SELECT * FROM tg_links"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._read_all(sql, tuple(params))]

    def update_message_text(self, local_id: int, text: str) -> None:
        """Update a message's stored text (after a successful remote edit)."""
        self._write("UPDATE tg_messages SET text = ?, edited_at = ? WHERE id = ?", (text, _utcnow(), local_id))
        row = self._read_one("SELECT chat_id FROM tg_messages WHERE id = ?", (local_id,))
        if row and text:
            self._index_fts("message", local_id, int(row["chat_id"]), text)

    def mark_message_deleted(self, chat_id: int, message_id: int) -> bool:
        cur = self._write(
            "UPDATE tg_messages SET deleted = 1 WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        return cur.rowcount > 0

    def list_messages(
        self,
        chat_id: int,
        *,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 100,
        before_id: int | None = None,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["m.chat_id = ?"]
        params: list[Any] = [chat_id]
        if not include_deleted:
            clauses.append("m.deleted = 0")
        if kind:
            if kind == "media":
                clauses.append("m.media_ref IS NOT NULL")
            else:
                clauses.append("m.kind = ?")
                params.append(kind)
        if q:
            clauses.append("(m.text LIKE ? OR m.sender_name LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if before_id:
            clauses.append("m.message_id < ?")
            params.append(before_id)
        params.append(max(1, min(500, limit)))
        rows = self._read_all(
            f"""
            SELECT m.*, d.kind AS media_kind, d.mime AS media_mime, d.size AS media_size,
                   d.filename AS media_filename, d.analysis_status AS media_status
            FROM tg_messages m
            LEFT JOIN tg_media d ON d.id = m.media_ref
            WHERE {' AND '.join(clauses)}
            ORDER BY m.message_id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [_message_dict(r) for r in rows]

    # ── Media ─────────────────────────────────────────────────

    def upsert_media(
        self,
        chat_id: int,
        *,
        message_id: int | None = None,
        file_id: str | None = None,
        file_unique_id: str | None = None,
        kind: str | None = None,
        mime: str | None = None,
        size: int | None = None,
        filename: str | None = None,
    ) -> int:
        """Insert (or fetch existing by ``file_unique_id``) a media row; returns its ``id``."""
        if file_unique_id:
            existing = self._read_one(
                "SELECT id FROM tg_media WHERE file_unique_id = ?", (file_unique_id,)
            )
            if existing:
                return int(existing["id"])
        cur = self._write(
            """
            INSERT INTO tg_media
                (chat_id, message_id, file_id, file_unique_id, kind, mime, size, filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_unique_id) DO UPDATE SET
                file_id    = COALESCE(excluded.file_id, tg_media.file_id),
                message_id = COALESCE(excluded.message_id, tg_media.message_id)
            """,
            (chat_id, message_id, file_id, file_unique_id, kind, mime, size, filename),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self._read_one(
            "SELECT id FROM tg_media WHERE file_unique_id = ?", (file_unique_id,)
        )
        return int(row["id"]) if row else 0

    def get_media(self, media_id: int) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM tg_media WHERE id = ?", (media_id,))
        return _media_dict(row) if row else None

    def list_media(self, chat_id: int, *, kind: str | None = None, tag: str | None = None,
                   category: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses = ["chat_id = ?"]
        params: list[Any] = [chat_id]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if tag:
            clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        params.append(max(1, min(1000, limit)))
        rows = self._read_all(
            f"SELECT * FROM tg_media WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [_media_dict(r) for r in rows]

    def pending_media(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._read_all(
            "SELECT * FROM tg_media WHERE analysis_status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        return [_media_dict(r) for r in rows]

    def set_media_status(self, media_id: int, status: str) -> None:
        self._write("UPDATE tg_media SET analysis_status = ? WHERE id = ?", (status, media_id))

    def set_media_local_path(self, media_id: int, local_path: str) -> None:
        self._write("UPDATE tg_media SET local_path = ? WHERE id = ?", (local_path, media_id))

    def set_analysis(
        self,
        media_id: int,
        *,
        status: str = "done",
        ocr_text: str | None = None,
        transcript: str | None = None,
        ai_description: str | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> None:
        self._write(
            """
            UPDATE tg_media SET
                analysis_status = ?,
                ocr_text       = COALESCE(?, ocr_text),
                transcript     = COALESCE(?, transcript),
                ai_description = COALESCE(?, ai_description),
                analysis_json  = COALESCE(?, analysis_json),
                analyzed_at    = ?
            WHERE id = ?
            """,
            (
                status, ocr_text, transcript, ai_description,
                json.dumps(analysis) if analysis is not None else None,
                _utcnow(), media_id,
            ),
        )
        row = self._read_one("SELECT chat_id, filename FROM tg_media WHERE id = ?", (media_id,))
        if row:
            body = " ".join(p for p in (row["filename"], ocr_text, transcript, ai_description) if p)
            self._index_fts("media", media_id, int(row["chat_id"]), body)

    # ── Search ────────────────────────────────────────────────

    def search(self, q: str, *, chat_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Quick search across messages + media analysis. FTS5 when available."""
        q = (q or "").strip()
        if not q:
            return []
        limit = max(1, min(200, limit))
        if self._has_fts():
            return self._search_fts(q, chat_id, limit)
        return self._search_like(q, chat_id, limit)

    def _search_fts(self, q: str, chat_id: int | None, limit: int) -> list[dict[str, Any]]:
        clauses = ["tg_search MATCH ?"]
        params: list[Any] = [_fts_query(q)]
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        params.append(limit)
        try:
            rows = self._read_all(
                f"SELECT ref_kind, ref_id, chat_id, snippet(tg_search, 0, '[', ']', '…', 12) AS snippet "
                f"FROM tg_search WHERE {' AND '.join(clauses)} ORDER BY rank LIMIT ?",
                tuple(params),
            )
        except sqlite3.OperationalError:
            return self._search_like(q, chat_id, limit)
        return [self._hydrate_hit(r["ref_kind"], int(r["ref_id"]), r["snippet"]) for r in rows]

    def _search_like(self, q: str, chat_id: int | None, limit: int) -> list[dict[str, Any]]:
        like = f"%{q}%"
        out: list[dict[str, Any]] = []
        mc = "AND chat_id = ?" if chat_id is not None else ""
        mp: list[Any] = [like]
        if chat_id is not None:
            mp.append(chat_id)
        mp.append(limit)
        for r in self._read_all(
            f"SELECT id FROM tg_messages WHERE text LIKE ? {mc} AND deleted = 0 ORDER BY id DESC LIMIT ?",
            tuple(mp),
        ):
            out.append(self._hydrate_hit("message", int(r["id"]), None))
        dp: list[Any] = [like, like, like]
        dc = "AND chat_id = ?" if chat_id is not None else ""
        if chat_id is not None:
            dp.append(chat_id)
        dp.append(limit)
        for r in self._read_all(
            f"SELECT id FROM tg_media WHERE (ocr_text LIKE ? OR transcript LIKE ? OR ai_description LIKE ?) "
            f"{dc} ORDER BY id DESC LIMIT ?",
            tuple(dp),
        ):
            out.append(self._hydrate_hit("media", int(r["id"]), None))
        return out[:limit]

    def _hydrate_hit(self, ref_kind: str, ref_id: int, snippet: str | None) -> dict[str, Any]:
        if ref_kind == "media":
            m = self.get_media(ref_id) or {}
            return {
                "ref_kind": "media", "ref_id": ref_id, "chat_id": m.get("chat_id"),
                "snippet": snippet or (m.get("ai_description") or m.get("transcript") or m.get("ocr_text") or "")[:200],
                "media": m,
            }
        row = self._read_one("SELECT * FROM tg_messages WHERE id = ?", (ref_id,))
        msg = _message_dict(row) if row else {}
        return {
            "ref_kind": "message", "ref_id": ref_id, "chat_id": msg.get("chat_id"),
            "snippet": snippet or (msg.get("text") or "")[:200],
            "message": msg,
        }


# ── Row → dict converters ─────────────────────────────────────


def _b(v: bool | None) -> int | None:
    return None if v is None else (1 if v else 0)


def _room_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "chat_id": row["chat_id"],
        "type": row["type"],
        "title": row["title"],
        "username": row["username"],
        "member_count": row["member_count"],
        "bot_is_admin": _ib(row["bot_is_admin"]),
        "can_post": _ib(row["can_post"]),
        "can_delete": _ib(row["can_delete"]),
        "privacy_ok": _ib(row["privacy_ok"]),
        "last_message_at": row["last_message_at"],
        "last_synced": row["last_synced"],
        "message_count": row["message_count"] if "message_count" in row.keys() else None,
        "media_count": row["media_count"] if "media_count" in row.keys() else None,
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    d = {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "sender_id": row["sender_id"],
        "sender_name": row["sender_name"],
        "date": row["date"],
        "text": row["text"],
        "reply_to": row["reply_to"],
        "media_ref": row["media_ref"],
        "kind": row["kind"],
        "edited_at": row["edited_at"],
        "deleted": bool(row["deleted"]),
    }
    if "media_kind" in keys:
        d["media"] = (
            {
                "kind": row["media_kind"],
                "mime": row["media_mime"],
                "size": row["media_size"],
                "filename": row["media_filename"],
                "analysis_status": row["media_status"],
            }
            if row["media_ref"] is not None
            else None
        )
    return d


def _media_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "file_id": row["file_id"],
        "file_unique_id": row["file_unique_id"],
        "kind": row["kind"],
        "mime": row["mime"],
        "size": row["size"],
        "filename": row["filename"],
        "local_path": row["local_path"],
        "analysis_status": row["analysis_status"],
        "ocr_text": row["ocr_text"],
        "transcript": row["transcript"],
        "ai_description": row["ai_description"],
        "analysis": json.loads(row["analysis_json"]) if row["analysis_json"] else None,
        "analyzed_at": row["analyzed_at"],
        "tags": (json.loads(row["tags"]) if ("tags" in row.keys() and row["tags"]) else []),
        "category": (row["category"] if "category" in row.keys() else None),
    }


def _ib(v: Any) -> bool | None:
    return None if v is None else bool(v)


def _fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 prefix query (avoids operator injection)."""
    import re

    terms = re.findall(r"\w+", q)
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)


# ── Singleton ─────────────────────────────────────────────────

_store: TelegramCatalogStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "telegram_catalog.db"


def get_telegram_catalog() -> TelegramCatalogStore:
    """Return the global :class:`TelegramCatalogStore` singleton."""
    global _store
    if _store is None:
        _store = TelegramCatalogStore()
    return _store
