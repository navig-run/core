"""
NAVIG Vault Storage Backend

SQLite-based encrypted storage for credentials.
All credential data is encrypted before writing to disk.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .encryption import VaultEncryption
from .types import Credential, CredentialInfo, CredentialType


class VaultStorageError(Exception):
    """Raised when storage operations fail."""

    pass


class VaultStorage:
    """
    SQLite storage backend with encryption.

    All credential data (secrets) is encrypted using Fernet before
    being stored in the database. Metadata is stored in plaintext
    for searchability.

    Database schema:
    - credentials: Main credential storage
    - vault_meta: Key-value metadata (version, settings)
    - audit_log: Access audit trail

    File permissions are set to 0600 to restrict access.
    """

    SCHEMA_VERSION = 1

    def __init__(self, vault_path: Path, encryption: VaultEncryption):
        """
        Initialize storage backend.

        Args:
            vault_path: Path to the SQLite database file
            encryption: VaultEncryption instance for data encryption
        """
        self.vault_path = vault_path
        self.encryption = encryption
        self._ensure_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """
        Context manager for database connections.

        Ensures connections are properly closed after use.
        """
        conn = sqlite3.connect(self.vault_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create or migrate database schema."""
        # Ensure parent directory exists
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            conn.executescript(
                """
                -- Main credentials table
                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    data_encrypted BLOB NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_provider
                    ON credentials(provider);
                CREATE INDEX IF NOT EXISTS idx_profile
                    ON credentials(profile_id);
                CREATE INDEX IF NOT EXISTS idx_provider_profile
                    ON credentials(provider, profile_id);
                CREATE INDEX IF NOT EXISTS idx_enabled
                    ON credentials(enabled);

                -- Vault metadata
                CREATE TABLE IF NOT EXISTS vault_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- Audit log for credential access tracking
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    credential_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    accessed_by TEXT,
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_credential
                    ON audit_log(credential_id);
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                    ON audit_log(timestamp);
            """
            )

            # Set schema version
            conn.execute(
                "INSERT OR REPLACE INTO vault_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(self.SCHEMA_VERSION)),
            )
            conn.commit()

        # Set restrictive file permissions (Unix only)
        try:
            import os

            try:
                os.chmod(self.vault_path, 0o600)
            except (OSError, PermissionError):
                pass
        except OSError:
            pass  # best-effort cleanup

    def save(self, credential: Credential) -> None:
        """
        Save credential to storage.

        Inserts or updates the credential. Data is encrypted before storage.

        Args:
            credential: Credential object to save
        """
        # Encrypt the secret data
        data_json = json.dumps(credential.data)
        encrypted_data = self.encryption.encrypt(data_json)

        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO credentials
                (id, provider, profile_id, credential_type, label,
                 data_encrypted, metadata_json, enabled,
                 created_at, updated_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    credential.id,
                    credential.provider,
                    credential.profile_id,
                    credential.credential_type.value,
                    credential.label,
                    encrypted_data,
                    json.dumps(credential.metadata),
                    1 if credential.enabled else 0,
                    credential.created_at.isoformat(),
                    credential.updated_at.isoformat(),
                    (credential.last_used_at.isoformat() if credential.last_used_at else None),
                ),
            )
            conn.commit()

    def get(self, credential_id: str) -> Credential | None:
        """
        Get credential by ID.

        Args:
            credential_id: Unique credential identifier

        Returns:
            Credential if found, None otherwise
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM credentials WHERE id = ?", (credential_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_credential(row)

    def get_by_provider_profile(
        self, provider: str, profile_id: str = "default"
    ) -> Credential | None:
        """
        Get credential by provider and profile.

        Returns the most recently created enabled credential matching
        the provider and profile.

        Args:
            provider: Provider name
            profile_id: Profile namespace

        Returns:
            Matching credential or None
        """
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM credentials
                WHERE provider = ? AND profile_id = ? AND enabled = 1
                ORDER BY created_at DESC LIMIT 1
            """,
                (provider, profile_id),
            ).fetchone()

            if not row:
                return None

            return self._row_to_credential(row)

    def list_all(
        self,
        provider: str | None = None,
        profile_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[CredentialInfo]:
        """
        List credentials (metadata only, no secrets).

        Args:
            provider: Optional filter by provider
            profile_id: Optional filter by profile
            include_disabled: Include disabled credentials

        Returns:
            List of CredentialInfo objects (no secret data)
        """
        query = "SELECT * FROM credentials WHERE 1=1"
        params: list[str] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if profile_id:
            query += " AND profile_id = ?"
            params.append(profile_id)
        if not include_disabled:
            query += " AND enabled = 1"

        query += " ORDER BY provider, profile_id, label"

        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_info(row) for row in rows]

    def delete(self, credential_id: str) -> bool:
        """
        Delete credential by ID.

        Args:
            credential_id: Credential to delete

        Returns:
            True if deleted, False if not found
        """
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
            conn.commit()
            return cursor.rowcount > 0

    def set_enabled(self, credential_id: str, enabled: bool) -> bool:
        """
        Enable or disable a credential.

        Args:
            credential_id: Credential to update
            enabled: New enabled state

        Returns:
            True if updated, False if not found
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE credentials SET enabled = ?, updated_at = ? WHERE id = ?",
                (
                    1 if enabled else 0,
                    datetime.now(timezone.utc).isoformat(),
                    credential_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_last_used(self, credential_id: str) -> None:
        """
        Update the last_used_at timestamp.

        Args:
            credential_id: Credential that was accessed
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE credentials SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), credential_id),
            )
            conn.commit()

    def log_access(self, credential_id: str, action: str, accessed_by: str = "unknown") -> None:
        """
        Log credential access for auditing.

        Note: This only logs the credential ID and action, never the secret values!

        Args:
            credential_id: Credential that was accessed
            action: Action performed (created, accessed, updated, deleted, tested)
            accessed_by: Module/function that accessed the credential
        """
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_log
                (credential_id, action, accessed_by, timestamp)
                VALUES (?, ?, ?, ?)
            """,
                (
                    credential_id,
                    action,
                    accessed_by,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def get_audit_log(self, credential_id: str | None = None, limit: int = 100) -> list[dict]:
        """
        Get audit log entries.

        Args:
            credential_id: Optional filter by credential
            limit: Maximum entries to return

        Returns:
            List of audit log entries as dicts
        """
        with self._connection() as conn:
            if credential_id:
                rows = conn.execute(
                    """
                    SELECT * FROM audit_log
                    WHERE credential_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (credential_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM audit_log
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (limit,),
                ).fetchall()

            return [dict(row) for row in rows]

    def count(self, provider: str | None = None, enabled_only: bool = False) -> int:
        """
        Count credentials in vault.

        Args:
            provider: Optional filter by provider
            enabled_only: Only count enabled credentials

        Returns:
            Number of matching credentials
        """
        query = "SELECT COUNT(*) FROM credentials WHERE 1=1"
        params: list[str] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if enabled_only:
            query += " AND enabled = 1"

        with self._connection() as conn:
            result = conn.execute(query, params).fetchone()
            return result[0] if result else 0

    def _row_to_credential(self, row: sqlite3.Row) -> Credential:
        """
        Convert database row to Credential.

        Decrypts the data field.
        """
        decrypted_data = json.loads(self.encryption.decrypt(row["data_encrypted"]))

        return Credential(
            id=row["id"],
            provider=row["provider"],
            profile_id=row["profile_id"],
            credential_type=CredentialType(row["credential_type"]),
            label=row["label"],
            data=decrypted_data,
            metadata=json.loads(row["metadata_json"]),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_used_at=(
                datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None
            ),
        )

    def _row_to_info(self, row: sqlite3.Row) -> CredentialInfo:
        """
        Convert database row to CredentialInfo.

        No decryption needed - this is metadata only.
        """
        return CredentialInfo(
            id=row["id"],
            provider=row["provider"],
            profile_id=row["profile_id"],
            credential_type=CredentialType(row["credential_type"]),
            label=row["label"],
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used_at=(
                datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None
            ),
            metadata=json.loads(row["metadata_json"]),
        )
