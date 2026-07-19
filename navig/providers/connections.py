"""
NAVIG Connection service (Phase 1) — instance-based connection storage.

Owns the control-plane state for AI connections: versioned SQLite storage with
migrations, optimistic concurrency (``revision``), global + per-workspace default
resolution, health state, and **safe-remove** (deletes local config + vault
material only; remote revocation is a separate, explicit action).

Secrets never live in this DB — only a ``secret_ref`` (vault label). The vault
(:mod:`navig.vault`) holds the encrypted material; deleting a connection deletes
its vault item too.

Storage is isolated by ``NAVIG_DATA_DIR`` (via :func:`navig.platform.paths.data_dir`),
so tests/scripts can point it at a temp dir without touching real state.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from navig.platform.paths import data_dir
from navig.providers.connection_types import (
    AuthState,
    Capability,
    Connection,
    ConnectionNotFound,
    Driver,
    HealthState,
    RevisionConflict,
    VaultUnavailable,
    new_connection_id,
)
from navig.store.base import BaseStore, _utcnow  # noqa: F401  (_utcnow kept for parity)

logger = logging.getLogger(__name__)

# Vault item prefix for connection secrets — keeps connection material grouped
# and auditable separately from other vault entries.
SECRET_LABEL_PREFIX = "connection"


def _ms() -> int:
    import time

    return int(time.time() * 1000)


# ─────────────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────────────


class ConnectionStore(BaseStore):
    """SQLite-backed, versioned store of :class:`Connection` instances."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | None = None, *, engine=None, vault=None):
        self._vault = vault  # injectable; lazily resolved when None
        resolved = Path(db_path) if db_path else (data_dir() / "connections.db")
        super().__init__(resolved, engine=engine)

    # ── schema ──────────────────────────────────────────────────────────────
    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS connections (
                connection_id  TEXT PRIMARY KEY,
                template_id    TEXT NOT NULL,
                name           TEXT NOT NULL,
                driver         TEXT NOT NULL,
                secret_ref     TEXT,
                capabilities   TEXT NOT NULL DEFAULT '[]',
                auth_state     TEXT NOT NULL,
                health_state   TEXT NOT NULL,
                revision       INTEGER NOT NULL DEFAULT 1,
                models         TEXT NOT NULL DEFAULT '[]',
                default_model  TEXT,
                is_default     INTEGER NOT NULL DEFAULT 0,
                metadata       TEXT NOT NULL DEFAULT '{}',
                created_at     INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_connections_template
                ON connections(template_id);
            CREATE INDEX IF NOT EXISTS idx_connections_default
                ON connections(is_default);

            CREATE TABLE IF NOT EXISTS workspace_defaults (
                workspace_id   TEXT PRIMARY KEY,
                connection_id  TEXT NOT NULL
            );

            -- small key/value store; holds the global default connection id,
            -- which may point at a VIRTUAL (configured-elsewhere) connection.
            CREATE TABLE IF NOT EXISTS connection_settings (
                key    TEXT PRIMARY KEY,
                value  TEXT
            );

            -- In-flight OAuth logins (the server-held PKCE state for one login).
            -- Persisted ONLY so a login survives a daemon restart between "open the
            -- browser" and "paste the code" — the daemon is restarted often enough
            -- (config changes, `navig gateway start` after a Lighthouse deploy) that
            -- an in-memory map silently killed logins mid-flow.
            --
            -- Rows are deliberately short-lived: single-use (deleted the moment they
            -- are read) and pruned past their 10-minute expiry. A code_verifier is
            -- useless without the matching single-use authorization code, which only
            -- ever reaches the user's browser.
            CREATE TABLE IF NOT EXISTS pending_oauth (
                handle       TEXT PRIMARY KEY,
                kind         TEXT NOT NULL,
                template_id  TEXT NOT NULL,
                state        TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                created_at   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pending_oauth_created
                ON pending_oauth(created_at);
            """
        )

    # ── settings (kv) ─────────────────────────────────────────────────────────
    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self._write("DELETE FROM connection_settings WHERE key = ?", (key,))
        else:
            self._write(
                "INSERT OR REPLACE INTO connection_settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        row = self._read_one("SELECT value FROM connection_settings WHERE key = ?", (key,))
        return row["value"] if row else None

    # ── in-flight OAuth logins ────────────────────────────────────────────────
    #
    # The PKCE state for one login, held between "open the browser" and "paste the
    # code". It lives here rather than in a module-level dict so a login survives a
    # daemon restart — the daemon is restarted often enough mid-setup (a config
    # change, `navig gateway start` after a Lighthouse deploy) that an in-memory map
    # silently killed logins and left the user with "expired or already used".

    def put_pending_oauth(
        self,
        handle: str,
        *,
        kind: str,
        template_id: str,
        state: str,
        code_verifier: str,
        created_at: float,
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO pending_oauth "
            "(handle, kind, template_id, state, code_verifier, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (handle, kind, template_id, state, code_verifier, created_at),
        )

    def take_pending_oauth(self, handle: str) -> dict[str, Any] | None:
        """Read a pending login and consume it — single-use, exactly like the
        in-memory ``dict.pop`` it replaces. A handle can never be redeemed twice."""
        row = self._read_one("SELECT * FROM pending_oauth WHERE handle = ?", (handle,))
        if row is None:
            return None
        self._write("DELETE FROM pending_oauth WHERE handle = ?", (handle,))
        return dict(row)

    def count_pending_oauth(self) -> int:
        """How many logins are mid-flight. A count only — diagnostics must never
        need the handle, the state, or the verifier."""
        row = self._read_one("SELECT COUNT(*) AS n FROM pending_oauth")
        return int(row["n"]) if row else 0

    def prune_pending_oauth(self, older_than: float) -> int:
        """Drop logins that were never completed. Without this, an abandoned login
        kept its PKCE verifier forever — well past the 10-minute expiry the flow
        itself enforces — and the table grew without bound."""
        cur = self._write("DELETE FROM pending_oauth WHERE created_at < ?", (older_than,))
        return int(getattr(cur, "rowcount", 0) or 0)

    def get_workspace_default_id(self, workspace_id: str) -> str | None:
        row = self._read_one(
            "SELECT connection_id FROM workspace_defaults WHERE workspace_id = ?",
            (workspace_id,),
        )
        return row["connection_id"] if row else None

    # ── (de)serialization ───────────────────────────────────────────────────
    @staticmethod
    def _row_to_connection(row: sqlite3.Row) -> Connection:
        return Connection(
            connection_id=row["connection_id"],
            template_id=row["template_id"],
            name=row["name"],
            driver=Driver(row["driver"]),
            secret_ref=row["secret_ref"],
            capabilities={Capability(c) for c in json.loads(row["capabilities"])},
            auth_state=AuthState(row["auth_state"]),
            health_state=HealthState(row["health_state"]),
            revision=row["revision"],
            models=json.loads(row["models"]),
            default_model=row["default_model"],
            is_default=bool(row["is_default"]),
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, connection_id: str) -> Connection:
        row = self._read_one(
            "SELECT * FROM connections WHERE connection_id = ?", (connection_id,)
        )
        if row is None:
            raise ConnectionNotFound(f"No connection: {connection_id}")
        return self._row_to_connection(row)

    def list(self) -> list[Connection]:
        rows = self._read_all("SELECT * FROM connections ORDER BY created_at ASC")
        return [self._row_to_connection(r) for r in rows]

    def get_default(self) -> Connection | None:
        row = self._read_one(
            "SELECT * FROM connections WHERE is_default = 1 LIMIT 1"
        )
        return self._row_to_connection(row) if row else None

    def resolve_default(self, workspace_id: str | None = None) -> Connection | None:
        """Per-workspace override wins over the global default."""
        if workspace_id:
            row = self._read_one(
                "SELECT connection_id FROM workspace_defaults WHERE workspace_id = ?",
                (workspace_id,),
            )
            if row is not None:
                try:
                    return self.get(row["connection_id"])
                except ConnectionNotFound:
                    # stale override → fall through to global default
                    self._write(
                        "DELETE FROM workspace_defaults WHERE workspace_id = ?",
                        (workspace_id,),
                    )
        return self.get_default()

    # ── writes ──────────────────────────────────────────────────────────────
    def create(self, conn: Connection) -> Connection:
        now = _ms()
        conn.created_at = conn.created_at or now
        conn.updated_at = now
        self._write(
            """
            INSERT INTO connections (
                connection_id, template_id, name, driver, secret_ref,
                capabilities, auth_state, health_state, revision, models,
                default_model, is_default, metadata, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                conn.connection_id,
                conn.template_id,
                conn.name,
                conn.driver.value,
                conn.secret_ref,
                json.dumps(sorted(c.value for c in conn.capabilities)),
                conn.auth_state.value,
                conn.health_state.value,
                conn.revision,
                json.dumps(conn.models),
                conn.default_model,
                1 if conn.is_default else 0,
                json.dumps(conn.metadata),
                conn.created_at,
                conn.updated_at,
            ),
        )
        # First connection becomes the global default automatically.
        if self.get_default() is None:
            self.set_default(conn.connection_id)
            conn.is_default = True
        return self.get(conn.connection_id)

    def update(self, conn: Connection, *, expected_revision: int | None = None) -> Connection:
        """Persist mutations with optimistic concurrency.

        Pass ``expected_revision`` (typically ``conn.revision`` as last read) to
        reject lost-update races with :class:`RevisionConflict`.
        """
        expected = expected_revision if expected_revision is not None else conn.revision
        new_rev = expected + 1
        cur = self._write(
            """
            UPDATE connections SET
                name = ?, secret_ref = ?, capabilities = ?, auth_state = ?,
                health_state = ?, revision = ?, models = ?, default_model = ?,
                metadata = ?, updated_at = ?
            WHERE connection_id = ? AND revision = ?
            """,
            (
                conn.name,
                conn.secret_ref,
                json.dumps(sorted(c.value for c in conn.capabilities)),
                conn.auth_state.value,
                conn.health_state.value,
                new_rev,
                json.dumps(conn.models),
                conn.default_model,
                json.dumps(conn.metadata),
                _ms(),
                conn.connection_id,
                expected,
            ),
        )
        if cur.rowcount == 0:
            # Either the row is gone or the revision moved under us.
            if self._read_one(
                "SELECT 1 FROM connections WHERE connection_id = ?",
                (conn.connection_id,),
            ) is None:
                raise ConnectionNotFound(f"No connection: {conn.connection_id}")
            raise RevisionConflict(
                f"Connection {conn.connection_id} changed (expected rev {expected})"
            )
        return self.get(conn.connection_id)

    def set_default(self, connection_id: str) -> None:
        # ensure it exists first (raises if not)
        self.get(connection_id)
        with self._lock:
            db = self._get_conn()
            old_iso = db.isolation_level
            try:
                db.isolation_level = None
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE connections SET is_default = 0 WHERE is_default = 1")
                db.execute(
                    "UPDATE connections SET is_default = 1 WHERE connection_id = ?",
                    (connection_id,),
                )
                db.execute("COMMIT")
            except Exception:
                try:
                    db.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise
            finally:
                db.isolation_level = old_iso

    def set_workspace_default(self, workspace_id: str, connection_id: str) -> None:
        self.get(connection_id)  # validate
        self._write(
            "INSERT OR REPLACE INTO workspace_defaults (workspace_id, connection_id) VALUES (?, ?)",
            (workspace_id, connection_id),
        )

    def clear_workspace_default(self, workspace_id: str) -> None:
        self._write(
            "DELETE FROM workspace_defaults WHERE workspace_id = ?", (workspace_id,)
        )

    def delete(self, connection_id: str) -> bool:
        """Safe-remove: delete the record + its vault material + any workspace
        overrides pointing at it. Does NOT perform remote revocation."""
        conn = self.get(connection_id)
        # Remove vault secret first (best-effort; never block local removal).
        if conn.secret_ref:
            try:
                self._vault_delete(conn.secret_ref)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vault delete failed for %s: %s", connection_id, exc)
        self._write("DELETE FROM connections WHERE connection_id = ?", (connection_id,))
        self._write(
            "DELETE FROM workspace_defaults WHERE connection_id = ?", (connection_id,)
        )
        # Keep the kv default in sync — routing reads `default_connection_id`
        # (connect._default_id) which would otherwise dangle at the deleted id.
        if self.get_setting("default_connection_id") == connection_id:
            _rest = self.list()
            self.set_setting(
                "default_connection_id",
                _rest[0].connection_id if _rest else None,
            )
        # If we removed the default, promote the oldest remaining connection.
        if conn.is_default:
            remaining = self.list()
            if remaining:
                self.set_default(remaining[0].connection_id)
        return True

    # ── vault helpers (lazy, fail-safe) ─────────────────────────────────────
    def _get_vault(self):
        if self._vault is not None:
            return self._vault
        try:
            from navig.vault import get_vault

            self._vault = get_vault()
            return self._vault
        except Exception as exc:  # noqa: BLE001
            raise VaultUnavailable(str(exc))

    def store_secret(self, template_id: str, payload: str | bytes, *, provider: str | None = None) -> str:
        """Encrypt+store a connection secret in the vault; return the secret_ref label."""
        label = f"{SECRET_LABEL_PREFIX}/{template_id}/{new_connection_id()[:8]}"
        data = payload.encode() if isinstance(payload, str) else payload
        self._get_vault().put(label, data, provider=provider)
        return label

    def update_secret(self, label: str | None, payload: str | bytes) -> None:
        """Re-store a secret under an existing label (e.g. a refreshed OAuth token
        bundle). The vault versions the item; the label/secret_ref is unchanged."""
        if not label:
            return
        data = payload.encode() if isinstance(payload, str) else payload
        self._get_vault().put(label, data)

    def read_secret(self, label: str | None) -> str | None:
        """Resolve a ``secret_ref`` to its EXACT stored plaintext (a raw API key
        OR a JSON OAuth token bundle). Returns ``None`` if absent. Callers must
        never persist or log the result.

        Uses ``vault.get_bytes`` (the exact stored payload) — NOT ``get_secret``,
        which for SECRET-kind items ``json.loads`` the payload and returns a
        SINGLE field. Since ``store_secret`` writes the raw payload, ``get_secret``
        (a) raises ``JSONDecodeError`` on a raw key (crashing openai-compat /
        chatgpt-OAuth connect + revalidate) and (b) silently drops the
        ``refresh_token``/``expires_at`` from a claude-max bundle (returning only
        ``access_token``), which kills OAuth auto-refresh. ``get_bytes`` round-trips
        both shapes intact.
        """
        if not label:
            return None
        vault = self._get_vault()
        get_bytes = getattr(vault, "get_bytes", None)
        if callable(get_bytes):
            try:
                raw = get_bytes(label)
            except KeyError:
                return None
            if raw is None:
                return None
            if isinstance(raw, (bytes, bytearray)):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
        # Fallback for vault doubles without get_bytes (tests): SecretStr reveal.
        # navig SecretStr exposes plaintext via .reveal(); pydantic via
        # .get_secret_value(); both mask under str()/repr() ('***'), so never str().
        try:
            secret = vault.get_secret(label)
        except KeyError:
            return None
        if secret is None:
            return None
        for accessor in ("reveal", "get_secret_value"):
            fn = getattr(secret, accessor, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:  # noqa: BLE001
                    return None
        if isinstance(secret, str):
            return secret
        text = str(secret)
        return None if text == "***" else text

    def _vault_delete(self, label: str) -> None:
        self._get_vault().delete(label)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton accessor (NAVIG_DATA_DIR-aware)
# ─────────────────────────────────────────────────────────────────────────────

_STORE: ConnectionStore | None = None
_STORE_PATH: Path | None = None
_STORE_LOCK = threading.Lock()


def get_connection_store() -> ConnectionStore:
    """Return the process-wide connection store, rebuilt if the data dir changed
    (so ``NAVIG_DATA_DIR`` overrides in tests take effect). Thread-safe: a
    parallel first use (e.g. a council fan-out) must not construct two stores."""
    global _STORE, _STORE_PATH
    path = data_dir() / "connections.db"
    if _STORE is None or _STORE_PATH != path:
        with _STORE_LOCK:
            if _STORE is None or _STORE_PATH != path:
                _STORE = ConnectionStore(path)
                _STORE_PATH = path
    return _STORE
