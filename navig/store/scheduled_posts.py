"""
ScheduledPostStore — persistence for Studio composer drafts and scheduled posts.

Backs the Studio deck API (``navig_social.deck_routes.studio``) and the always-on
:class:`navig_social.social.scheduler_service.ScheduledPostService`, which every
~20s pulls due posts via :meth:`due`, publishes them, and reschedules ``recurring``
ones. (The Studio surface lives in the optional navig-social plugin; this store
stays in core because it owns persistence.)

A *post* is a row with opaque JSON ``content`` (markdown body + media refs),
a list of publish ``targets``, a ``status`` lifecycle, and scheduling fields:

    status          draft → scheduled → publishing → published|partial|failed
                    (recurring posts return to ``scheduled`` with a fresh run_at)
    schedule_kind   "now" | "once" | "recurring"
    run_at          ISO-8601 UTC (e.g. "2026-06-27T15:30:00Z"). Compared
                    LEXICOGRAPHICALLY in :meth:`due`, so callers must store a
                    zero-padded, Z-suffixed UTC timestamp — the same format the
                    scheduler's CronParser path emits.

Backed by :class:`BaseStore` for WAL, schema versioning, and write serialisation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.core.json_io import safe_json_loads
from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# Columns that store JSON-encoded structures; (de)serialised transparently.
_JSON_FIELDS = {"content": "content_json", "targets": "targets_json", "receipts": "receipts_json"}
# Scalar columns a caller may set via create()/update().
# (expires_at/campaign only among the new ones: identity and approval columns
# stay on their dedicated paths — create/set_content/approve — so a loose
# UPDATE can never move a join key or an approval.)
_SCALAR_FIELDS = {"body", "status", "schedule_kind", "run_at", "cron_expr", "last_error",
                  "content_id", "content_hash", "version", "approval_state",
                  "approved_hash", "approved_at", "idempotency_key",
                  "campaign", "expires_at"}
# Statuses a post has already been (or is being) published as: content edits are
# refused here (routes map False → 409 immutable). Schedule fields stay editable.
_TERMINAL_STATUSES = ("publishing", "published", "partial", "failed")


def _canonical_run_at(value: Any) -> Any:
    """Normalise a caller-supplied ``run_at`` to canonical UTC ``%Y-%m-%dT%H:%M:%SZ``.

    ``due()`` compares ``run_at`` LEXICOGRAPHICALLY, so every stored value must
    share one exact format or a post fires early / late / never.  Callers (the
    deck route, the API, an agent) may send anything ISO-ish — JS
    ``toISOString()`` → ``…:00.000Z`` (milliseconds), ``datetime.isoformat()`` →
    ``…+00:00`` (offset), a ``datetime-local`` value → ``…T18:00`` (no seconds,
    naive), or a non-UTC offset like ``…+05:30`` (which sorts by its *local*
    wall-clock, not the real UTC instant).  Parse robustly, convert to UTC, drop
    sub-seconds.  A non-ISO value is returned unchanged so it fails loudly
    downstream rather than being silently zeroed.
    """
    if not value or not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return value  # not ISO-8601 — leave as-is
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # naive input is assumed UTC
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScheduledPostStore(BaseStore):
    """SQLite store for Studio posts. See module docstring for the lifecycle."""

    SCHEMA_VERSION = 3
    PRAGMAS = {"cache_size": -2000}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                body           TEXT NOT NULL DEFAULT '',
                content_json   TEXT NOT NULL DEFAULT '{}',
                targets_json   TEXT NOT NULL DEFAULT '[]',
                status         TEXT NOT NULL DEFAULT 'draft',
                schedule_kind  TEXT NOT NULL DEFAULT 'now',
                run_at         TEXT,
                cron_expr      TEXT,
                receipts_json  TEXT NOT NULL DEFAULT '[]',
                last_error     TEXT,
                content_id     TEXT,
                content_hash   TEXT,
                version        INTEGER NOT NULL DEFAULT 1,
                approval_state TEXT NOT NULL DEFAULT 'none',
                approved_hash  TEXT,
                approved_at    TEXT,
                idempotency_key TEXT,
                campaign       TEXT,
                expires_at     TEXT,
                created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            -- The scheduler's hot path: due() filters on (status, run_at).
            CREATE INDEX IF NOT EXISTS idx_sched_due
                ON scheduled_posts (status, run_at);

            -- Approval audit: one row per decision, append-only.
            CREATE TABLE IF NOT EXISTS post_approvals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id      INTEGER NOT NULL,
                version      INTEGER,
                content_hash TEXT,
                state        TEXT NOT NULL,
                decided_by   TEXT NOT NULL DEFAULT '',
                decided_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                reason       TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_post
                ON post_approvals (post_id, decided_at);

            -- Version history: the exact content of every version (append-only).
            CREATE TABLE IF NOT EXISTS post_versions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id      INTEGER NOT NULL,
                version      INTEGER NOT NULL,
                content_json TEXT NOT NULL DEFAULT '{}',
                targets_json TEXT NOT NULL DEFAULT '[]',
                content_hash TEXT,
                created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_versions_post
                ON post_versions (post_id, version);
        """)
        # ⚠ BaseStore._init_schema runs this BEFORE it reads the stored version.
        # On an existing v1 table the CREATE TABLE above is a no-op and
        # content_id does not exist yet — an index over it inside the script
        # raised "no such column", store init died, and every Studio route plus
        # the scheduler tick failed on a live install (2026-10-15). Created
        # guarded here; _migrate creates it right after the columns land.
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_content "
                         "ON scheduled_posts (content_id)")
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        if from_version < 2:
            # v2: content identity + approval state (additive; approval defaults
            # to 'none' so every existing post keeps its current behaviour —
            # nothing is gated until content carries approval_policy="required").
            for ddl in (
                "ALTER TABLE scheduled_posts ADD COLUMN content_id TEXT",
                "ALTER TABLE scheduled_posts ADD COLUMN content_hash TEXT",
                "ALTER TABLE scheduled_posts ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE scheduled_posts ADD COLUMN approval_state TEXT NOT NULL DEFAULT 'none'",
                "ALTER TABLE scheduled_posts ADD COLUMN approved_hash TEXT",
                "ALTER TABLE scheduled_posts ADD COLUMN approved_at TEXT",
                "ALTER TABLE scheduled_posts ADD COLUMN idempotency_key TEXT",
                "CREATE INDEX IF NOT EXISTS idx_sched_content "
                "ON scheduled_posts (content_id)",
                "CREATE TABLE IF NOT EXISTS post_approvals ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " post_id INTEGER NOT NULL, version INTEGER, content_hash TEXT,"
                " state TEXT NOT NULL, decided_by TEXT NOT NULL DEFAULT '',"
                " decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
                " reason TEXT NOT NULL DEFAULT '')",
                "CREATE INDEX IF NOT EXISTS idx_approvals_post "
                "ON post_approvals (post_id, decided_at)",
                "CREATE TABLE IF NOT EXISTS post_versions ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " post_id INTEGER NOT NULL, version INTEGER NOT NULL,"
                " content_json TEXT NOT NULL DEFAULT '{}',"
                " targets_json TEXT NOT NULL DEFAULT '[]', content_hash TEXT,"
                " created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
                "CREATE INDEX IF NOT EXISTS idx_versions_post "
                "ON post_versions (post_id, version)",
            ):
                # guarded: a migration interrupted before the version stamp
                # re-runs on the next open and must not die on duplicate column
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        if from_version < 3:
            # v3: campaign grouping + publish deadline (additive; both nullable).
            # Guarded per statement: a migration interrupted before the version
            # stamp re-runs on the next open and must not die on duplicate
            # column name (same shape as the v2 block above).
            for ddl in (
                "ALTER TABLE scheduled_posts ADD COLUMN campaign TEXT",
                "ALTER TABLE scheduled_posts ADD COLUMN expires_at TEXT",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise

    # ── Row conversion ────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "body": row["body"] or "",
            # safe_json_loads, not json.loads: these mappers run inside
            # `[_row_to_dict(r) for r in rows]`, so one corrupt blob would take out the
            # whole queue listing rather than degrade the single post.
            "content": safe_json_loads(row["content_json"], {}),
            "targets": safe_json_loads(row["targets_json"], []),
            "status": row["status"],
            "schedule_kind": row["schedule_kind"],
            "run_at": row["run_at"],
            "cron_expr": row["cron_expr"],
            "receipts": safe_json_loads(row["receipts_json"], []),
            "last_error": row["last_error"],
            "content_id": row["content_id"],
            "content_hash": row["content_hash"],
            "version": row["version"] or 1,
            "approval_state": row["approval_state"] or "none",
            "approved_hash": row["approved_hash"],
            "approved_at": row["approved_at"],
            "idempotency_key": row["idempotency_key"],
            "campaign": row["campaign"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }

    # ── CRUD ──────────────────────────────────────────────────

    def create(
        self,
        *,
        body: str = "",
        content: dict[str, Any] | None = None,
        targets: list[Any] | None = None,
        status: str = "draft",
        schedule_kind: str = "now",
        run_at: str | None = None,
        cron_expr: str | None = None,
        campaign: str | None = None,
        expires_at: str | None = None,
    ) -> int:
        """Insert a post and return its new id.

        Content identity is computed here (never trusted from the caller unless
        the content carries an explicit ``content_hash`` — a Stage-C contract
        passthrough, so SCHEMA manifests keep their pinned hash).
        """
        run_at = _canonical_run_at(run_at)
        expires_at = _canonical_run_at(expires_at)
        now = _utcnow()
        content = content or {}
        targets = targets or []
        try:
            from navig_social.social.content_hash import of_post

            content_hash = str(content.get("content_hash") or of_post(content, targets))
        except ImportError:  # navig-social absent: identity unavailable, nothing gated
            content_hash = str(content.get("content_hash") or "") or None
        content_id = content.get("content_id")
        cursor = self._write(
            "INSERT INTO scheduled_posts "
            "(body, content_json, targets_json, status, schedule_kind, run_at, "
            " cron_expr, content_id, content_hash, version, approval_state, "
            " campaign, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'none', ?, ?, ?, ?)",
            (
                body or "",
                json.dumps(content),
                json.dumps(targets),
                status,
                schedule_kind,
                run_at,
                cron_expr,
                str(content_id) if content_id else None,
                content_hash,
                campaign,
                expires_at,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid or 0)

    def get(self, post_id: int) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
        return self._row_to_dict(row) if row else None

    def list(self, *, status: str | None = None, campaign: str | None = None,
             content_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """List posts, newest first, filtered by status/campaign/content id."""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if campaign:
            clauses.append("campaign = ?")
            params.append(campaign)
        if content_id:
            clauses.append("content_id = ?")
            params.append(content_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._read_all(
            "SELECT * FROM scheduled_posts" + where +
            " ORDER BY COALESCE(run_at, created_at) DESC, id DESC LIMIT ?",
            (*params, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    def update(self, post_id: int, **fields: Any) -> bool:
        """Update an allowlisted set of fields. Unknown keys are ignored.

        JSON fields (``content``/``targets``/``receipts``) accept Python
        structures and are serialised; scalar fields pass through. ``updated_at``
        is always bumped. Returns True if a row was changed.

        Immutability: once a post is (being) published — ``publishing``,
        ``published``, ``partial``, ``failed`` — content edits (``body``/
        ``content``/``targets``) are refused (returns False; routes map that to
        409 ``immutable``). Schedule/status/error fields stay editable so a
        failed post can be rescheduled and a published one annotated. Content
        edits on live posts belong to :meth:`set_content`, never here.
        """
        content_keys = {"body", "content", "targets"} & set(fields)
        if content_keys:
            row = self._read_one("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,))
            if row is None:
                return False
            if row["status"] in _TERMINAL_STATUSES:
                logger.info("update refused: post %s is %s (content immutable)",
                            post_id, row["status"])
                return False

        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key in _JSON_FIELDS:
                sets.append(f"{_JSON_FIELDS[key]} = ?")
                params.append(json.dumps(value if value is not None else ([] if key != "content" else {})))
            elif key in _SCALAR_FIELDS:
                sets.append(f"{key} = ?")
                params.append(_canonical_run_at(value) if key == "run_at" else value)
            # silently ignore unknown / non-updatable keys (e.g. id, created_at)

        if not sets:
            return False

        sets.append("updated_at = ?")
        params.append(_utcnow())
        params.append(post_id)
        cursor = self._write(
            f"UPDATE scheduled_posts SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return cursor.rowcount > 0

    def claim_publish(self, post_id: int, idempotency_key: str | None = None) -> bool:
        """Move a post to ``publishing`` in ONE conditional UPDATE. True = this caller owns it.

        Refused when the post is already ``publishing`` (another request or tick holds it)
        or was already published/partial under the same ``idempotency_key`` (a replay).
        A read-then-update cannot give this: two concurrent publishes would both pass
        the read and both send.
        """
        cursor = self._write(
            "UPDATE scheduled_posts SET status = 'publishing', updated_at = ? "
            "WHERE id = ? AND status != 'publishing' "
            "AND NOT (? IS NOT NULL AND idempotency_key IS ? "
            "         AND status IN ('published', 'partial'))",
            (_utcnow(), post_id, idempotency_key, idempotency_key),
        )
        return cursor.rowcount > 0

    def delete(self, post_id: int) -> bool:
        cursor = self._write("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))
        return cursor.rowcount > 0

    # ── Content identity + approval (v2) ──────────────────────

    def get_by_content_id(self, content_id: str) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT * FROM scheduled_posts WHERE content_id = ? "
            "ORDER BY COALESCE(run_at, created_at) DESC, id DESC LIMIT 1",
            (content_id,),
        )
        return self._row_to_dict(row) if row else None

    def set_content(self, post_id: int, content: dict[str, Any],
                    targets: list[Any] | None = None) -> dict[str, Any]:
        """Replace content/targets with rehash + version bump + approval invalidation.

        This is the invalidation rule of SOCIAL-CONTRACT §4 step 7: an edit to
        material content (text, media, destinations) can never keep an old
        approval. Rescheduling (``update(run_at=…)``) does NOT come through here
        and never bumps a version — schedule is excluded from the hash.
        """
        from navig_social.social.content_hash import of_post

        post = self.get(post_id)
        if post is None:
            raise ValueError(f"no post {post_id}")
        if post.get("status") in _TERMINAL_STATUSES:
            raise ValueError(
                f"immutable: post is {post.get('status')} — content cannot change after publish")
        new_targets = targets if targets is not None else post["targets"]
        new_hash = str((content or {}).get("content_hash") or of_post(content, new_targets))
        if new_hash != post.get("content_hash"):
            new_version = int(post.get("version") or 1) + 1
            self._write(
                "INSERT INTO post_versions (post_id, version, content_json, "
                "targets_json, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (post_id, new_version, json.dumps(content or {}),
                 json.dumps(new_targets), new_hash, _utcnow()),
            )
            approval_state = post.get("approval_state") or "none"
            invalidated = approval_state == "approved" and \
                post.get("approved_hash") not in (None, new_hash)
            fields: dict[str, Any] = {
                "content": content, "targets": new_targets,
                "content_hash": new_hash, "version": new_version,
            }
            if invalidated:
                fields["approval_state"] = "invalidated"
            self.update(post_id, **fields)
            if invalidated:
                # the audit trail must explain WHY the state moved (the hash
                # comparison is what gates; this row is the human answer)
                try:
                    self._write(
                        "INSERT INTO post_approvals (post_id, version, content_hash, "
                        " state, decided_by, decided_at, reason) "
                        "VALUES (?, ?, ?, 'invalidated', 'system', ?, 'content changed')",
                        (post_id, new_version, new_hash, _utcnow()),
                    )
                except sqlite3.Error:  # noqa: BLE001 — audit append must not break the edit
                    logger.warning("post %s: invalidation audit row not written", post_id)
        else:
            # same hash: still store the (possibly reordered) structures
            self.update(post_id, content=content, targets=new_targets)
        return self.get(post_id) or {}

    def approve(self, post_id: int, *, decided_by: str = "",
                content_hash: str | None = None) -> dict[str, Any]:
        """Bind an approval to the CURRENT content hash (or refuse a stale one)."""
        post = self.get(post_id)
        if post is None:
            raise ValueError(f"no post {post_id}")
        current = post.get("content_hash")
        if not current:
            raise ValueError(f"post {post_id} has no content_hash to approve")
        if content_hash is not None and content_hash != current:
            raise ValueError(
                f"stale approval: provided hash {content_hash[:19]}… != "
                f"current {current[:19]}… (content changed — review the new version)")
        ok = self.update(post_id, approval_state="approved", approved_hash=current,
                         approved_at=_utcnow())
        if not ok:
            raise ValueError(f"no post {post_id}")
        self._write(
            "INSERT INTO post_approvals (post_id, version, content_hash, state, "
            "decided_by, decided_at, reason) VALUES (?, ?, ?, 'approved', ?, ?, '')",
            (post_id, post.get("version") or 1, current, decided_by or "", _utcnow()),
        )
        return self.get(post_id) or {}

    def reject(self, post_id: int, *, reason: str = "", decided_by: str = "") -> dict[str, Any]:
        post = self.get(post_id)
        if post is None:
            raise ValueError(f"no post {post_id}")
        self.update(post_id, approval_state="rejected")
        self._write(
            "INSERT INTO post_approvals (post_id, version, content_hash, state, "
            "decided_by, decided_at, reason) VALUES (?, ?, ?, 'rejected', ?, ?, ?)",
            (post_id, post.get("version") or 1, post.get("content_hash"),
             decided_by or "", _utcnow(), reason or ""),
        )
        return self.get(post_id) or {}

    def approval_ok(self, post: dict[str, Any]) -> bool:
        """True when ``content`` does NOT demand approval, or the bound hash still matches."""
        content = post.get("content") or {}
        if content.get("approval_policy", "auto") != "required":
            return True
        return (post.get("approval_state") == "approved"
                and bool(post.get("approved_hash"))
                and post.get("approved_hash") == post.get("content_hash"))

    # ── Scheduler hot path ────────────────────────────────────

    def due(self, now_iso: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return scheduled posts whose ``run_at`` has arrived.

        Only ``status = 'scheduled'`` rows are eligible; ``run_at`` is compared
        lexicographically (ISO-8601 UTC). Drafts, terminal posts, and rows
        mid-publish (``status = 'publishing'``) are excluded, so a slow publish
        cannot double-fire on the next tick. Posts past ``expires_at`` are
        excluded too (belt to :meth:`expire_due`'s braces: a caller that never
        ran expiry still cannot fire an expired item).
        """
        rows = self._read_all(
            "SELECT * FROM scheduled_posts "
            "WHERE status = 'scheduled' AND run_at IS NOT NULL AND run_at <= ? "
            "  AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY run_at ASC LIMIT ?",
            (now_iso, now_iso, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    # ── v3: expiry + review-pending (SOCIAL-CONTRACT) ──────────────────────

    def expire_due(self, now_iso: str) -> int:
        """Park scheduled posts whose ``expires_at`` has passed.

        Expiry never publishes: ``status`` moves to ``expired`` (out of
        ``due()``'s selection) and a pending approval is expired too. An
        already-made decision (approved/rejected) keeps its state. Returns the
        number of rows parked.
        """
        cursor = self._write(
            "UPDATE scheduled_posts SET status = 'expired', "
            "  approval_state = CASE WHEN approval_state IN ('none', 'pending') "
            "                        THEN 'expired' ELSE approval_state END, "
            "  last_error = COALESCE(last_error, 'expired before publish'), "
            "  updated_at = ? "
            "WHERE status = 'scheduled' AND expires_at IS NOT NULL AND expires_at <= ?",
            (_utcnow(), now_iso),
        )
        return int(cursor.rowcount or 0)

    def pend_for_review(self, post_id: int, *, decided_by: str = "submit") -> bool:
        """Mark a required-policy post as awaiting a decision (+ audit row).

        The scheduler's approval gate reads ``approval_state`` via
        :meth:`approval_ok` — pending keeps the post parked until approve/
        reject. Returns False when the row is missing.
        """
        post = self.get(post_id)
        if post is None:
            return False
        if post.get("approval_state") == "pending":
            return True  # already waiting — never double-pend
        now = _utcnow()
        self.update(post_id, approval_state="pending")
        self._write(
            "INSERT INTO post_approvals (post_id, version, content_hash, state, "
            " decided_by, decided_at, reason) VALUES (?, ?, ?, 'pending', ?, ?, '')",
            (post_id, post.get("version") or 1, post.get("content_hash"),
             decided_by or "", now),
        )
        return True

    def count(self, *, status: str | None = None) -> int:
        if status:
            row = self._read_one(
                "SELECT COUNT(*) AS cnt FROM scheduled_posts WHERE status = ?", (status,)
            )
        else:
            row = self._read_one("SELECT COUNT(*) AS cnt FROM scheduled_posts")
        return row["cnt"] if row else 0


# ── Singleton ─────────────────────────────────────────────────

_store: ScheduledPostStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "scheduled_posts.db"


def get_scheduled_posts() -> ScheduledPostStore:
    """Return the global :class:`ScheduledPostStore` singleton."""
    global _store
    if _store is None:
        _store = ScheduledPostStore()
    return _store
