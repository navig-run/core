"""NAVIG Vault v2 — High-level API gluing CryptoEngine + VaultStore + SessionStore.

This is the primary entry point for all new vault operations.
The legacy CredentialsVault (core.py) remains untouched for backward compat.

Usage
-----
from navig.vault.core_v2 import VaultV2, get_vault_v2

v = get_vault_v2()
v.unlock()                          # machine-fingerprint unlock (no passphrase)
v.unlock(passphrase=b"mypass")      # passphrase unlock

item_id = v.put("openai/api_key", b"sk-...")
secret  = v.get_secret("openai/api_key")  # → "sk-..."
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .crypto import CryptoEngine, CryptoError
from .session import SessionStore, VaultSession
from .store import VaultStore
from .types import VaultItem, VaultItemKind

__all__ = ["VaultV2", "get_vault_v2"]

_DEFAULT_TTL = 1800  # 30 minutes


class VaultV2:
    """High-level vault operations on top of CryptoEngine + VaultStore.

    Parameters
    ----------
    vault_dir : Path
        Directory for vault.db and vault.salt.
    """

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir  = vault_dir
        self._engine    = CryptoEngine(vault_dir)
        self._store     = VaultStore(vault_dir)

    # ── Unlock / Lock ────────────────────────────────────────────────────────

    def unlock(
        self,
        passphrase: Optional[bytes] = None,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> VaultSession:
        """Derive master key and store an active session.

        Parameters
        ----------
        passphrase   : Raw passphrase bytes.  None → machine-fingerprint mode.
        ttl_seconds  : Session TTL in seconds (default 30m).
        """
        master_key = self._engine.derive_key(passphrase)
        session = VaultSession(
            master_key=master_key,
            unlocked_at=datetime.now(timezone.utc),
            ttl_seconds=ttl_seconds,
        )
        SessionStore.set(session)
        return session

    def lock(self) -> None:
        """Clear active session (zero out key from memory)."""
        SessionStore.clear()

    def _master_key(self) -> bytes:
        """Return the master key from session or machine fingerprint."""
        session = SessionStore.get()
        if session is not None:
            return session.master_key
        # Non-interactive fallback: derive from machine fingerprint
        return self._engine.derive_key(None)

    # ── Core operations ───────────────────────────────────────────────────────

    def put(
        self,
        label: str,
        payload: bytes,
        *,
        kind: VaultItemKind = VaultItemKind.SECRET,
        provider: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Encrypt and store *payload* under *label*.

        Returns the item UUID.
        """
        master_key = self._master_key()

        # Generate per-item DEK and encrypt payload
        dek            = CryptoEngine.generate_dek()
        encrypted_blob = CryptoEngine.seal(dek, payload)

        # Encrypt DEK with master key
        encrypted_dek  = CryptoEngine.seal(master_key, dek)

        now = datetime.now(timezone.utc)

        # Check if item already exists (update path)
        existing = self._store.get(label)
        if existing:
            item = VaultItem(
                id=existing.id,
                kind=kind,
                label=label,
                provider=provider,
                encrypted_dek=encrypted_dek,
                encrypted_blob=encrypted_blob,
                metadata=metadata or {},
                created_at=existing.created_at,
                updated_at=now,
                version=existing.version + 1,
            )
            self._store.upsert(item)
            self._store.audit(item.id, "update")
            return item.id
        else:
            item_id = str(uuid.uuid4())
            item = VaultItem(
                id=item_id,
                kind=kind,
                label=label,
                provider=provider,
                encrypted_dek=encrypted_dek,
                encrypted_blob=encrypted_blob,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
            self._store.upsert(item)
            self._store.audit(item_id, "create")
            return item_id

    def get_bytes(self, label: str) -> bytes:
        """Decrypt and return the raw payload bytes for *label*.

        Raises
        ------
        KeyError    : Item not found.
        CryptoError : Decryption failure (wrong key / tampered data).
        """
        item = self._store.get(label)
        if item is None:
            raise KeyError(f"Vault item not found: {label!r}")
        master_key = self._master_key()
        dek     = CryptoEngine.open(master_key, item.encrypted_dek)
        payload = CryptoEngine.open(dek, item.encrypted_blob)
        self._store.audit(item.id, "read")
        return payload

    def get_secret(self, label: str) -> str:
        """Decrypt and return the primary secret value for *label*.

        For SECRET/PROVIDER items: the JSON "value" or "api_key" field.
        For NOTE items: the "text" field.
        For FILE/IDENTITY/CONFIG items: raw bytes decoded as UTF-8.
        """
        item = self._store.get(label)
        if item is None:
            raise KeyError(f"Vault item not found: {label!r}")

        payload = self.get_bytes(label)

        if item.kind in (VaultItemKind.SECRET, VaultItemKind.PROVIDER):
            data = json.loads(payload)
            # Try "value", then the key_field from metadata, then first value
            if "value" in data:
                return data["value"]
            if item.provider:
                from .provider import get_provider
                meta = get_provider(item.provider) or {}
                key_field = meta.get("key_field", "api_key")
                if key_field in data:
                    return data[key_field]
            return next(iter(data.values()), "")
        elif item.kind == VaultItemKind.NOTE:
            data = json.loads(payload)
            return data.get("text", payload.decode("utf-8", errors="replace"))
        else:
            return payload.decode("utf-8", errors="replace")

    def delete(self, label: str) -> bool:
        """Delete item by label.  Returns True if deleted."""
        item = self._store.get(label)
        if item:
            self._store.audit(item.id, "delete")
        return self._store.delete(label)

    # ── Rotate master key ────────────────────────────────────────────────────

    def rotate(
        self,
        new_passphrase: Optional[bytes] = None,
    ) -> int:
        """Re-encrypt all DEKs with a new master key.

        This does NOT re-encrypt the payloads — only the DEK wrappers.
        Payload security is unchanged; rotation updates the master key layer.

        Parameters
        ----------
        new_passphrase : New passphrase (None = new machine-fingerprint key).

        Returns
        -------
        int : Number of items rotated.
        """
        old_master = self._master_key()

        # Force-create a new salt so the new KDF output differs
        salt_path = self.vault_dir / CryptoEngine.SALT_FILE
        import os
        new_salt = os.urandom(32)
        self._engine._salt = None  # clear cache
        salt_path.write_bytes(new_salt)
        try:
            salt_path.chmod(0o600)
        except OSError:
            pass

        new_master = self._engine.derive_key(new_passphrase)

        items   = self._store.list()
        rotated = 0
        for item in items:
            try:
                dek         = CryptoEngine.open(old_master, item.encrypted_dek)
                new_enc_dek = CryptoEngine.seal(new_master, dek)
                rotated_item = VaultItem(
                    id=item.id,
                    kind=item.kind,
                    label=item.label,
                    provider=item.provider,
                    encrypted_dek=new_enc_dek,
                    encrypted_blob=item.encrypted_blob,
                    metadata=item.metadata,
                    created_at=item.created_at,
                    updated_at=datetime.now(timezone.utc),
                    version=item.version + 1,
                )
                self._store.upsert(rotated_item)
                self._store.audit(item.id, "rotate")
                rotated += 1
            except CryptoError:
                pass  # skip items we can't decrypt with old key

        # Update session with new master key
        session = SessionStore.get()
        if session is not None:
            from dataclasses import replace  # type: ignore[attr-defined]
            SessionStore.set(VaultSession(
                master_key=new_master,
                unlocked_at=session.unlocked_at,
                ttl_seconds=session.ttl_seconds,
            ))

        return rotated

    # ── JSON key files (service accounts, etc.) ─────────────────────────────

    def put_json_file(
        self,
        label: str,
        source: "str | Path",
        *,
        provider: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Encrypt and store a JSON key file (e.g. Google service account).

        Parameters
        ----------
        label    : Vault label, e.g. ``"google/tts-service-account"``.
        source   : A filesystem path to a ``.json`` file **or** the raw JSON
                   string itself.
        provider : Optional provider tag (e.g. ``"google"``).

        Returns the vault item UUID.
        """
        import json as _json

        src_path = Path(source).expanduser() if isinstance(source, (str, Path)) else None
        if src_path is not None and src_path.exists():
            raw = src_path.read_text(encoding="utf-8")
            original_name = src_path.name
        else:
            raw = str(source)  # treat as raw JSON string
            original_name = None

        # Validate that it is actually JSON
        try:
            _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON provided to put_json_file: {exc}") from exc

        meta = dict(metadata or {})
        meta["content_type"] = "application/json"
        if original_name:
            meta["original_file"] = original_name

        return self.put(
            label,
            raw.encode("utf-8"),
            kind=VaultItemKind.FILE,
            provider=provider,
            metadata=meta,
        )

    def get_json_file(self, label: str) -> dict:
        """Decrypt and return a stored JSON key file as a Python dict."""
        import json as _json
        return _json.loads(self.get_bytes(label))

    def get_json_str(self, label: str) -> str:
        """Decrypt and return a stored JSON key file as a raw JSON string."""
        return self.get_bytes(label).decode("utf-8")

    # ── Convenience ──────────────────────────────────────────────────────────

    def list(
        self,
        kind: Optional[VaultItemKind] = None,
        provider: Optional[str] = None,
    ) -> list[VaultItem]:
        return self._store.list(kind=kind, provider=provider)

    def search(self, query: str) -> list[VaultItem]:
        return self._store.search(query)

    def count(self) -> int:
        return self._store.count()

    def store(self) -> VaultStore:
        """Expose the underlying store (for advanced use)."""
        return self._store

    def engine(self) -> CryptoEngine:
        """Expose the crypto engine (for advanced use)."""
        return self._engine


# ── Module-level singleton ────────────────────────────────────────────────────

_vault_v2: Optional["VaultV2"] = None


def get_vault_v2(vault_dir: Optional[Path] = None) -> VaultV2:
    """Return (or create) the global VaultV2 singleton.

    Parameters
    ----------
    vault_dir : Override the storage directory.  Uses ``vault_dir()`` from
                navig.platform.paths by default.
    """
    global _vault_v2
    if _vault_v2 is None:
        if vault_dir is None:
            from navig.platform.paths import vault_dir as _vault_dir_fn
            vault_dir = _vault_dir_fn()
        _vault_v2 = VaultV2(vault_dir)
    return _vault_v2
