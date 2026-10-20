"""
PromptLibraryStore — the operator's prompt library (the Prompt Manager).

One row per prompt. ``id`` is a slug path (``dev/coding/debug-root-cause``) so
imports from a folder tree are stable and re-runnable. After the first import
this database is the source of truth: re-importing adds new files and refreshes
untouched records, but never overwrites a prompt the operator edited
(``user_edited = 1``).

Surfaces: ``navig prompts …`` · ``/api/deck/prompts`` (NAVIG OS) · Telegram
``/prompt``. Search uses FTS5 when compiled in, LIKE otherwise.

Schema v2 adds the **preset** tables (``navig.presets``): user-saved and imported
presets, plus user overrides of file presets, live here beside the prompts so one
database holds everything the operator saved. Builtin, plugin, project and space
presets stay as files and are layered under these rows by
``navig.presets.catalog``. The v1 → v2 migration is additive (two new tables;
existing prompt rows are untouched) and writes a one-time backup first.

Schema v3 adds **capture provenance** for prompts saved from the browser (the Dock's
ChatGPT Lens): ``project``, ``source_url`` and ``content_hash`` — the sha256 of the body
with whitespace normalised (:func:`content_hash`), indexed, so a batch import dedupes by
content instead of by exact bytes. The v2 → v3 migration only adds columns, backfills the
hash of every existing row, and takes a one-time backup first.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from navig.core.json_io import safe_json_loads
from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9/_-]+")
SORTS = ("recent", "used", "title", "updated")


@dataclass
class PromptRecord:
    id: str
    title: str
    body: str
    use_when: str = ""
    domain: str = ""
    category: str = ""
    collection: str = ""
    step: int | None = None
    tags: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    source: str = "user"  # import | user
    source_path: str = ""
    source_hash: str = ""
    user_edited: bool = False
    favorite: bool = False
    use_count: int = 0
    last_used_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    project: str = ""
    source_url: str = ""
    content_hash: str = ""

    def to_dict(self, *, with_body: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not with_body:
            d.pop("body", None)
            d["size"] = len(self.body)
        return d


def slugify(text: str) -> str:
    """Lowercase kebab slug; keeps ``/`` so ids can carry a path."""
    s = text.strip().lower().replace("\\", "/").replace(" ", "-")
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"/{2,}", "/", s)
    return s.strip("-/")


class PromptLibraryStore(BaseStore):
    SCHEMA_VERSION = 3
    PRAGMAS = {"cache_size": -2000}

    _COLS = (
        "id, title, body, use_when, domain, category, collection, step, tags_json, "
        "variables_json, source, source_path, source_hash, user_edited, favorite, "
        "use_count, last_used_at, created_at, updated_at, archived, project, source_url, content_hash"
    )

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS prompts (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                body            TEXT NOT NULL DEFAULT '',
                use_when        TEXT NOT NULL DEFAULT '',
                domain          TEXT NOT NULL DEFAULT '',
                category        TEXT NOT NULL DEFAULT '',
                collection      TEXT NOT NULL DEFAULT '',
                step            INTEGER,
                tags_json       TEXT NOT NULL DEFAULT '[]',
                variables_json  TEXT NOT NULL DEFAULT '[]',
                source          TEXT NOT NULL DEFAULT 'user',
                source_path     TEXT NOT NULL DEFAULT '',
                source_hash     TEXT NOT NULL DEFAULT '',
                user_edited     INTEGER NOT NULL DEFAULT 0,
                favorite        INTEGER NOT NULL DEFAULT 0,
                use_count       INTEGER NOT NULL DEFAULT 0,
                last_used_at    TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                archived        INTEGER NOT NULL DEFAULT 0,
                project         TEXT NOT NULL DEFAULT '',
                source_url      TEXT NOT NULL DEFAULT '',
                content_hash    TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_prompts_domain ON prompts (domain, category);
            CREATE INDEX IF NOT EXISTS idx_prompts_collection ON prompts (collection, step);
            CREATE INDEX IF NOT EXISTS idx_prompts_fav ON prompts (favorite) WHERE favorite = 1;

            -- v2: presets (navig.presets). The full preset is ``data_json`` (its own
            -- ``format_version`` inside); the columns are what lists filter on.
            CREATE TABLE IF NOT EXISTS presets (
                id          TEXT PRIMARY KEY,
                tool        TEXT NOT NULL,
                layer       TEXT NOT NULL,
                name        TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'user',
                favorite    INTEGER NOT NULL DEFAULT 0,
                archived    INTEGER NOT NULL DEFAULT 0,
                use_count   INTEGER NOT NULL DEFAULT 0,
                data_json   TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_presets_tool ON presets (tool, layer);
            -- Per-tool default preset, and favorites of FILE presets (which have no row).
            CREATE TABLE IF NOT EXISTS preset_prefs (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            """
        )
        if _has_column(conn, "prompts", "content_hash"):  # an older library gets it in _migrate
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts (content_hash)")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5("
                "title, use_when, body, tags, pid UNINDEXED)"
            )
        except sqlite3.OperationalError as exc:  # FTS5 not compiled in
            logger.info("FTS5 unavailable (%s); prompt search falls back to LIKE.", exc)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        # Take a one-time copy of the library before any migration: it is the
        # operator's data, and a copy is cheap.
        # v1 → v2 only ADDS tables (created above by _create_schema, IF NOT EXISTS),
        # so there is nothing to rewrite for it.
        if from_version < 3:
            self._backup_once(conn, f"v{from_version}")
            # v2 → v3: capture provenance. Only ADD columns (old code ignores them), then
            # backfill the content hash so dedupe sees prompts saved before the upgrade.
            for col in ("project", "source_url", "content_hash"):
                if not _has_column(conn, "prompts", col):
                    conn.execute(f"ALTER TABLE prompts ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts (content_hash)")
            rows = conn.execute("SELECT id, body FROM prompts WHERE content_hash = ''").fetchall()
            conn.executemany(
                "UPDATE prompts SET content_hash = ? WHERE id = ?",
                [(content_hash(r[1] or ""), r[0]) for r in rows],
            )

    def _backup_once(self, conn: sqlite3.Connection, tag: str) -> Path | None:
        dest = Path(self.db_path).with_name(f"{Path(self.db_path).stem}.{tag}-backup.db")
        if dest.exists() or str(self.db_path) == ":memory:":
            return None
        try:
            # `with sqlite3.connect()` only commits; it does not close — and an open
            # handle keeps the backup file locked on Windows. Close explicitly.
            out = sqlite3.connect(str(dest), timeout=5.0)  # NAVIG's busy-timeout default
            try:
                conn.backup(out)
            finally:
                out.close()
            logger.info("prompt library: backed up %s before migrating", dest.name)
            return dest
        except (sqlite3.Error, OSError) as exc:
            # A failed copy must not block the (additive) migration — log it loudly.
            logger.warning("prompt library: could not back up before migrating: %s", exc)
            return None

    # ── FTS ───────────────────────────────────────────────────

    def _has_fts(self) -> bool:
        return (
            self._read_one(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompts_fts'"
            )
            is not None
        )

    def _reindex(self, rec: PromptRecord) -> None:
        if not self._has_fts():
            return
        try:
            self._write("DELETE FROM prompts_fts WHERE pid = ?", (rec.id,))
            self._write(
                "INSERT INTO prompts_fts (title, use_when, body, tags, pid) VALUES (?, ?, ?, ?, ?)",
                (rec.title, rec.use_when, rec.body, " ".join(rec.tags), rec.id),
            )
        except sqlite3.OperationalError as exc:
            logger.debug("prompt FTS index write failed: %s", exc)

    def _unindex(self, pid: str) -> None:
        if not self._has_fts():
            return
        try:
            self._write("DELETE FROM prompts_fts WHERE pid = ?", (pid,))
        except sqlite3.OperationalError as exc:
            logger.debug("prompt FTS delete failed: %s", exc)

    # ── Row conversion ────────────────────────────────────────

    @staticmethod
    def _row(row: sqlite3.Row) -> PromptRecord:
        return PromptRecord(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            use_when=row["use_when"],
            domain=row["domain"],
            category=row["category"],
            collection=row["collection"],
            step=row["step"],
            tags=safe_json_loads(row["tags_json"], []),
            variables=safe_json_loads(row["variables_json"], []),
            source=row["source"],
            source_path=row["source_path"],
            source_hash=row["source_hash"],
            user_edited=bool(row["user_edited"]),
            favorite=bool(row["favorite"]),
            use_count=row["use_count"],
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived=bool(row["archived"]),
            project=row["project"],
            source_url=row["source_url"],
            content_hash=row["content_hash"],
        )

    # ── Read ──────────────────────────────────────────────────

    def get(self, pid: str) -> PromptRecord | None:
        row = self._read_one(f"SELECT {self._COLS} FROM prompts WHERE id = ?", (pid,))
        return self._row(row) if row else None

    def find_by_body(self, body: str) -> PromptRecord | None:
        """The live prompt whose body equals ``body`` (outer whitespace ignored), if any."""
        text = body.strip()
        if not text:
            return None
        row = self._read_one(
            f"SELECT {self._COLS} FROM prompts WHERE archived = 0 AND TRIM(body) = ? LIMIT 1", (text,)
        )
        return self._row(row) if row else None

    def find_by_hash(self, digest: str) -> PromptRecord | None:
        """The live prompt whose :func:`content_hash` is *digest*, if any."""
        if not digest:
            return None
        row = self._read_one(
            f"SELECT {self._COLS} FROM prompts WHERE archived = 0 AND content_hash = ? LIMIT 1", (digest,)
        )
        return self._row(row) if row else None

    def all_ids(self, *, include_archived: bool = False) -> list[str]:
        sql = "SELECT id FROM prompts" + ("" if include_archived else " WHERE archived = 0")
        return [r["id"] for r in self._read_all(sql)]

    def count(self, *, include_archived: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM prompts"
        if not include_archived:
            sql += " WHERE archived = 0"
        row = self._read_one(sql)
        return int(row["n"]) if row else 0

    def list(
        self,
        *,
        q: str = "",
        domain: str = "",
        category: str = "",
        collection: str = "",
        tag: str = "",
        favorite: bool = False,
        include_archived: bool = False,
        project: str = "",
        sort: str = "title",
        limit: int = 500,
        offset: int = 0,
    ) -> list[PromptRecord]:
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("p.archived = 0")
        if domain:
            where.append("p.domain = ?")
            params.append(domain)
        if category:
            where.append("p.category = ?")
            params.append(category)
        if collection:
            where.append("p.collection = ?")
            params.append(collection)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(p.tags_json) WHERE value = ?)")
            params.append(tag)
        if favorite:
            where.append("p.favorite = 1")
        if project:
            where.append("p.project = ?")
            params.append(project)

        cols = ", ".join(f"p.{c.strip()}" for c in self._COLS.split(","))
        q = (q or "").strip()
        rank_order = ""
        join = ""
        if q:
            fts_q = _fts_query(q)
            if self._has_fts() and fts_q:
                join = "JOIN prompts_fts f ON f.pid = p.id"
                where.append("prompts_fts MATCH ?")
                params.append(fts_q)
                rank_order = "bm25(prompts_fts, 10.0, 4.0, 1.0, 3.0)"
            else:
                like = f"%{q}%"
                where.append("(p.title LIKE ? OR p.use_when LIKE ? OR p.body LIKE ? OR p.id LIKE ?)")
                params.extend([like, like, like, like])

        order = {
            "recent": "COALESCE(p.last_used_at, '') DESC, p.title COLLATE NOCASE",
            "used": "p.use_count DESC, p.title COLLATE NOCASE",
            "updated": "p.updated_at DESC",
        }.get(sort, "p.domain, p.category, p.collection, COALESCE(p.step, 0), p.title COLLATE NOCASE")
        if rank_order and sort == "title":
            order = rank_order
        sql = f"SELECT {cols} FROM prompts p {join}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 5000)), max(0, int(offset))])
        try:
            rows = self._read_all(sql, tuple(params))
        except sqlite3.OperationalError as exc:
            # A malformed FTS expression must degrade to LIKE, not to an error page.
            if not join:
                raise
            logger.debug("FTS query failed (%s); retrying with LIKE", exc)
            return self._list_like(q, limit)
        return [self._row(r) for r in rows]

    def _list_like(self, q: str, limit: int) -> list[PromptRecord]:
        like = f"%{q}%"
        rows = self._read_all(
            f"SELECT {self._COLS} FROM prompts WHERE archived = 0 AND "
            "(title LIKE ? OR use_when LIKE ? OR body LIKE ? OR id LIKE ?) "
            "ORDER BY title COLLATE NOCASE LIMIT ?",
            (like, like, like, like, int(limit)),
        )
        return [self._row(r) for r in rows]

    def facets(self, *, include_archived: bool = False) -> dict[str, Any]:
        """Counts for filter chips: domains → categories, collections, tags."""
        arch = "" if include_archived else "WHERE archived = 0"
        domains: dict[str, dict[str, Any]] = {}
        for r in self._read_all(
            f"SELECT domain, category, COUNT(*) AS n FROM prompts {arch} GROUP BY domain, category"
        ):
            d = domains.setdefault(r["domain"], {"count": 0, "categories": {}})
            d["count"] += r["n"]
            if r["category"]:
                d["categories"][r["category"]] = r["n"]
        collections = [
            {"name": r["collection"], "domain": r["domain"], "count": r["n"]}
            for r in self._read_all(
                f"SELECT collection, MIN(domain) AS domain, COUNT(*) AS n FROM prompts "
                f"{arch + (' AND' if arch else 'WHERE')} collection != '' "
                "GROUP BY collection ORDER BY collection"
            )
        ]
        tags: dict[str, int] = {}
        for r in self._read_all(
            f"SELECT j.value AS tag, COUNT(*) AS n FROM prompts, json_each(prompts.tags_json) j "
            f"{arch} GROUP BY j.value"
        ):
            tags[r["tag"]] = r["n"]
        fav = self._read_one(
            f"SELECT COUNT(*) AS n FROM prompts {arch + (' AND' if arch else 'WHERE')} favorite = 1"
        )
        return {
            "total": self.count(include_archived=include_archived),
            "favorites": int(fav["n"]) if fav else 0,
            "domains": domains,
            "collections": collections,
            "tags": dict(sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

    def collection_steps(self, collection: str) -> list[PromptRecord]:
        return self.list(collection=collection, sort="title", limit=200)

    def resolve(self, query: str, *, limit: int = 8) -> list[PromptRecord]:
        """Best matches for a free-form name: exact id → slug tail → title → search."""
        q = (query or "").strip()
        if not q:
            return []
        exact = self.get(q) or self.get(slugify(q))
        if exact and not exact.archived:
            return [exact]
        slug = slugify(q)
        tail = self._read_all(
            f"SELECT {self._COLS} FROM prompts WHERE archived = 0 AND "
            "(id LIKE ? OR lower(title) = lower(?)) ORDER BY length(id) LIMIT ?",
            (f"%/{slug}", q, limit),
        )
        if tail:
            return [self._row(r) for r in tail]
        return self.list(q=q, limit=limit)

    # ── Write ─────────────────────────────────────────────────

    def upsert(self, rec: PromptRecord) -> PromptRecord:
        """Insert or fully replace a record (keeps created_at / usage on replace)."""
        now = _utcnow()
        existing = self.get(rec.id)
        rec.created_at = existing.created_at if existing else (rec.created_at or now)
        rec.updated_at = now
        rec.content_hash = content_hash(rec.body)
        if existing:
            rec.use_count = existing.use_count
            rec.last_used_at = existing.last_used_at
            rec.favorite = rec.favorite or existing.favorite
        self._write(
            f"INSERT OR REPLACE INTO prompts ({self._COLS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.id, rec.title, rec.body, rec.use_when, rec.domain, rec.category,
                rec.collection, rec.step, json.dumps(rec.tags), json.dumps(rec.variables),
                rec.source, rec.source_path, rec.source_hash, int(rec.user_edited),
                int(rec.favorite), rec.use_count, rec.last_used_at, rec.created_at,
                rec.updated_at, int(rec.archived), rec.project, rec.source_url, rec.content_hash,
            ),
        )
        self._reindex(rec)
        return rec

    def create(
        self,
        *,
        title: str,
        body: str,
        domain: str = "",
        category: str = "",
        use_when: str = "",
        tags: list[str] | None = None,
        collection: str = "",
        pid: str = "",
        project: str = "",
        source_url: str = "",
        source: str = "user",
    ) -> PromptRecord:
        from navig.prompts.importer import detect_variables

        title = title.strip()
        if not title:
            raise ValueError("title is required")
        base = slugify(pid) if pid else slugify("/".join(x for x in (domain, category, title) if x))
        if not base:
            raise ValueError("could not derive an id from the title")
        new_id = base
        n = 2
        while self.get(new_id) is not None:
            new_id = f"{base}-{n}"
            n += 1
        rec = PromptRecord(
            id=new_id,
            title=title,
            body=body,
            use_when=use_when.strip(),
            domain=slugify(domain),
            category=slugify(category),
            collection=slugify(collection),
            tags=_clean_tags(tags),
            variables=detect_variables(body),
            source=source,
            user_edited=True,
            project=project.strip(),
            source_url=source_url.strip(),
        )
        return self.upsert(rec)

    _EDITABLE = ("title", "body", "use_when", "domain", "category", "collection", "tags", "step", "project")

    def update(self, pid: str, changes: dict[str, Any]) -> PromptRecord:
        from navig.prompts.importer import detect_variables

        rec = self.get(pid)
        if rec is None:
            raise KeyError(pid)
        unknown = set(changes) - set(self._EDITABLE) - {"favorite", "archived"}
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
        content_changed = False
        for key in self._EDITABLE:
            if key not in changes:
                continue
            val = changes[key]
            if key == "tags":
                val = _clean_tags(val)
            elif key in ("domain", "category", "collection"):
                val = slugify(str(val or ""))
            elif key == "step":
                val = int(val) if val not in (None, "") else None
            else:
                val = str(val or "")
            if key == "title" and not val.strip():
                raise ValueError("title cannot be empty")
            if getattr(rec, key) != val:
                setattr(rec, key, val)
                content_changed = True
        if "favorite" in changes:
            rec.favorite = bool(changes["favorite"])
        if "archived" in changes:
            rec.archived = bool(changes["archived"])
        if content_changed:
            rec.user_edited = True
            rec.variables = detect_variables(rec.body)
        return self.upsert(rec)

    def set_favorite(self, pid: str, value: bool | None = None) -> PromptRecord:
        rec = self.get(pid)
        if rec is None:
            raise KeyError(pid)
        rec.favorite = (not rec.favorite) if value is None else bool(value)
        self._write(
            "UPDATE prompts SET favorite = ?, updated_at = ? WHERE id = ?",
            (int(rec.favorite), _utcnow(), pid),
        )
        return rec

    def record_use(self, pid: str) -> PromptRecord | None:
        now = _utcnow()
        cur = self._write(
            "UPDATE prompts SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            (now, pid),
        )
        return self.get(pid) if cur.rowcount else None

    def delete(self, pid: str, *, purge: bool = False) -> bool:
        """Archive (default — recoverable) or purge a prompt."""
        if purge:
            cur = self._write("DELETE FROM prompts WHERE id = ?", (pid,))
            self._unindex(pid)
        else:
            cur = self._write(
                "UPDATE prompts SET archived = 1, updated_at = ? WHERE id = ?", (_utcnow(), pid)
            )
        return cur.rowcount > 0


    # ── Presets (v2) ──────────────────────────────────────────

    def preset_rows(self, *, tool: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
        """Every stored preset as its data dict (plus favorite/archived/use_count)."""
        where, params = [], []
        if tool:
            where.append("tool = ?")
            params.append(tool)
        if not include_archived:
            where.append("archived = 0")
        sql = "SELECT * FROM presets" + (" WHERE " + " AND ".join(where) if where else "")
        return [self._preset_row(r) for r in self._read_all(sql + " ORDER BY name COLLATE NOCASE", tuple(params))]

    def preset_row(self, pid: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM presets WHERE id = ?", (pid,))
        return self._preset_row(row) if row else None

    @staticmethod
    def _preset_row(row: sqlite3.Row) -> dict[str, Any]:
        data = safe_json_loads(row["data_json"], {})
        if not isinstance(data, dict):
            data = {}
        data.update(
            id=row["id"], favorite=bool(row["favorite"]), archived=bool(row["archived"]),
            use_count=int(row["use_count"]), created_at=row["created_at"], updated_at=row["updated_at"],
            source=row["source"],
        )
        return data

    def preset_put(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace one preset (keeps created_at and usage on replace)."""
        now = _utcnow()
        existing = self.preset_row(str(data["id"]))
        body = {k: v for k, v in data.items() if k not in ("favorite", "archived", "use_count", "created_at", "updated_at")}
        self._write(
            "INSERT OR REPLACE INTO presets (id, tool, layer, name, source, favorite, archived, "
            "use_count, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["id"], data["tool"], data["layer"], data["name"], data.get("source", "user"),
                int(bool(data.get("favorite", existing["favorite"] if existing else False))),
                int(bool(data.get("archived", False))),
                existing["use_count"] if existing else 0,
                json.dumps(body, ensure_ascii=False, sort_keys=True),
                existing["created_at"] if existing else now, now,
            ),
        )
        stored = self.preset_row(str(data["id"]))
        if stored is None:  # the read-back of the row just written
            raise RuntimeError(f"preset {data['id']!r} was not stored")
        return stored

    def preset_delete(self, pid: str) -> bool:
        cur = self._write("DELETE FROM presets WHERE id = ?", (pid,))
        return cur.rowcount > 0

    def preset_record_use(self, pid: str) -> None:
        self._write("UPDATE presets SET use_count = use_count + 1 WHERE id = ?", (pid,))

    def pref_get(self, key: str) -> str | None:
        row = self._read_one("SELECT value FROM preset_prefs WHERE key = ?", (key,))
        return row["value"] if row else None

    def pref_all(self, prefix: str) -> dict[str, str]:
        rows = self._read_all("SELECT key, value FROM preset_prefs WHERE key LIKE ?", (prefix + "%",))
        return {r["key"]: r["value"] for r in rows}

    def pref_set(self, key: str, value: str | None) -> None:
        if value is None:
            self._write("DELETE FROM preset_prefs WHERE key = ?", (key,))
        else:
            self._write(
                "INSERT OR REPLACE INTO preset_prefs (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, _utcnow()),
            )


def content_hash(body: str) -> str:
    """sha256 of *body* with all whitespace runs collapsed — the prompt's content identity."""
    return hashlib.sha256(" ".join((body or "").split()).encode("utf-8")).hexdigest()


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _clean_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    out: list[str] = []
    for t in tags or []:
        s = str(t).strip().lower().lstrip("#")
        if s and s not in out:
            out.append(s)
    return out


def _fts_query(q: str) -> str:
    """User text → a safe FTS5 expression (each word a quoted prefix term)."""
    words = re.findall(r"[\w\-]+", q, flags=re.UNICODE)
    terms = []
    for w in words[:12]:
        w = w.replace('"', "")
        if w:
            terms.append(f'"{w}"*')
    return " ".join(terms)


# ── Singleton ─────────────────────────────────────────────────

_store: PromptLibraryStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "prompts.db"


def get_prompt_store() -> PromptLibraryStore:
    """Return the process-wide :class:`PromptLibraryStore`."""
    global _store
    # Re-resolve the path on every call: a cached store pinned to a previous
    # NAVIG_DATA_DIR would write into the wrong library (tests, `--data-dir`).
    path = _default_db_path()
    if _store is None or Path(_store.db_path) != path:
        reset_prompt_store()
        _store = PromptLibraryStore(path)
    return _store


def reset_prompt_store() -> None:
    """Drop the singleton (tests / data-dir changes)."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:  # noqa: BLE001
            logger.debug("prompt store close failed", exc_info=True)
    _store = None
