"""NAVIG Vault — unified high-level API (AES-GCM + Argon2id per-item DEK).

This is the **single canonical vault** for all NAVIG credential storage.
The legacy ``CredentialsVault`` (core.py) is now a thin redirect shim to
this class; all historical call sites should converge here.

Usage
-----
from navig.vault import get_vault

v = get_vault()
v.unlock()                          # machine-fingerprint unlock (no passphrase)
v.unlock(passphrase=b"mypass")      # passphrase unlock

item_id = v.put("openai/api_key", b"sk-...")
secret  = v.get_secret("openai/api_key")  # → "sk-..."

# Provider/profile compatibility API still works:
cred_id = v.add("openai", "api_key", {"api_key": "sk-..."}, profile_id="default")
cred    = v.get("openai")           # → Credential | None
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from navig.core.file_permissions import set_owner_only_file_permissions
from navig.core.yaml_io import atomic_write_text

from .crypto import CryptoEngine, CryptoError
from .session import SessionStore, VaultSession
from .store import VaultStore
from .types import (
    Credential,
    CredentialInfo,
    CredentialType,
    TestResult,
    VaultItem,
    VaultItemKind,
)

if TYPE_CHECKING:
    pass

__all__ = ["Vault", "get_vault"]

from navig.vault._constants import _DEFAULT_TTL  # noqa: E402 — leaf module, no circular risk

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _cred_label(provider: str, profile_id: str | None) -> str:
    """Return the canonical vault label for a provider/profile credential."""
    profile = (profile_id or "default").strip()
    if profile == "default":
        return provider.lower().strip()
    return f"{provider.lower().strip()}/{profile}"


def _item_to_credential(item: VaultItem, data: dict | None = None) -> Credential:
    """Reconstruct a ``Credential`` from a ``VaultItem`` and its decrypted data."""
    meta = item.metadata or {}
    ct_raw = meta.get("credential_type", "generic")
    try:
        cred_type = CredentialType(ct_raw)
    except ValueError:
        cred_type = CredentialType.GENERIC

    return Credential(
        id=item.id,
        provider=item.provider or item.label.split("/")[0],
        profile_id=meta.get("profile_id", "default"),
        credential_type=cred_type,
        label=meta.get("label", item.label),
        data=data or {},
        metadata={
            k: v
            for k, v in meta.items()
            if k not in ("credential_type", "profile_id", "label", "enabled")
        },
        enabled=bool(meta.get("enabled", True)),
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_used_at=item.last_used_at,
    )


def _item_to_cred_info(item: VaultItem) -> CredentialInfo:
    """Return a metadata-only ``CredentialInfo`` from a ``VaultItem``."""
    meta = item.metadata or {}
    ct_raw = meta.get("credential_type", "generic")
    try:
        cred_type = CredentialType(ct_raw)
    except ValueError:
        cred_type = CredentialType.GENERIC
    return CredentialInfo(
        id=item.id,
        provider=item.provider or item.label.split("/")[0],
        profile_id=meta.get("profile_id", "default"),
        credential_type=cred_type,
        label=meta.get("label", item.label),
        enabled=bool(meta.get("enabled", True)),
        created_at=item.created_at,
        last_used_at=item.last_used_at,
        metadata={
            k: v
            for k, v in meta.items()
            if k not in ("credential_type", "profile_id", "label", "enabled")
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Vault
# ─────────────────────────────────────────────────────────────────────────────


class Vault:
    """Unified vault — AES-GCM + Argon2id per-item DEK, with compatibility adapter API.

    Parameters
    ----------
    vault_dir : Path
        Directory for vault.db and vault.salt.
    """

    def __init__(
        self,
        vault_dir: Path | None = None,
        *,
        vault_path: Path | None = None,
        auto_migrate: bool = False,  # accepted for compat; migration is handled by get_vault()
    ) -> None:
        # Accept vault_path as backward-compat alias for vault_dir (old CredentialsVault API)
        if vault_dir is None and vault_path is not None:
            # vault_path may be the .db file itself; we need the parent directory
            _vp = Path(vault_path)
            vault_dir = _vp.parent if _vp.suffix == ".db" else _vp
        if vault_dir is None:
            from navig.platform.paths import vault_dir as _vault_dir_fn  # noqa: PLC0415

            vault_dir = _vault_dir_fn()
        self.vault_dir = vault_dir
        # Store original vault_path kwarg for backward compat (callers may read .vault_path)
        self._vault_path_arg: Path | None = Path(vault_path) if vault_path is not None else None
        self._engine = CryptoEngine(vault_dir)
        self._store = VaultStore(vault_dir)
        if auto_migrate:
            _migrate_auth_profiles(self)

    # ── Unlock / Lock ─────────────────────────────────────────────────────────

    @property
    def vault_path(self) -> Path:
        """Return the vault .db file path (backward-compat with CredentialsVault API)."""
        if self._vault_path_arg is not None:
            return self._vault_path_arg
        return self.vault_dir / "vault.db"

    def unlock(
        self,
        passphrase: bytes | None = None,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> VaultSession:
        """Derive master key and store an active session."""
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
        return self._engine.derive_key(None)

    # ── Core operations ───────────────────────────────────────────────────────

    def put(
        self,
        label: str,
        payload: bytes,
        *,
        kind: VaultItemKind = VaultItemKind.SECRET,
        provider: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Encrypt and store *payload* under *label*.  Returns the item UUID."""
        master_key = self._master_key()

        dek = CryptoEngine.generate_dek()
        encrypted_blob = CryptoEngine.seal(dek, payload)
        encrypted_dek = CryptoEngine.seal(master_key, dek)

        now = datetime.now(timezone.utc)
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
                last_used_at=existing.last_used_at,
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
            self._store.audit(item_id, "created")
            return item_id

    def get_bytes(self, label: str) -> bytes:
        """Decrypt and return raw payload bytes for *label*.

        Raises
        ------
        KeyError    : Item not found.
        CryptoError : Decryption failure.
        """
        item = self._store.get(label)
        if item is None:
            raise KeyError(f"Vault item not found: {label!r}")
        master_key = self._master_key()
        dek = CryptoEngine.open(master_key, item.encrypted_dek)
        payload = CryptoEngine.open(dek, item.encrypted_blob)
        self._store.audit(item.id, "accessed")
        return payload

    def get_secret(self, label: str) -> "SecretStr":
        """Decrypt and return the primary secret string for *label* wrapped in SecretStr.

        For SECRET/PROVIDER items: "value", then "api_key", then first value.
        For NOTE items: "text" field.
        For FILE/CERT/other items: raw bytes decoded as UTF-8.

        Raises
        ------
        KeyError : Item not found.
        """
        from navig.vault.secret_str import SecretStr  # noqa: PLC0415

        item = self._store.get(label)
        if item is None:
            raise KeyError(f"Vault item not found: {label!r}")

        payload = self.get_bytes(label)

        if item.kind in (VaultItemKind.SECRET, VaultItemKind.PROVIDER, VaultItemKind.CREDENTIAL):
            data = json.loads(payload)
            if "value" in data:
                return SecretStr(data["value"])
            if item.provider:
                from .provider import get_provider  # noqa: PLC0415

                meta = get_provider(item.provider) or {}
                key_field = meta.get("key_field", "api_key")
                if key_field in data:
                    return SecretStr(data[key_field])
            if "api_key" in data:
                return SecretStr(data["api_key"])
            if "token" in data:
                return SecretStr(data["token"])
            first = next(iter(data.values()), "")
            return SecretStr(str(first) if first is not None else "")
        elif item.kind == VaultItemKind.NOTE:
            try:
                data = json.loads(payload)
                return SecretStr(data.get("text", payload.decode("utf-8", errors="replace")))
            except (json.JSONDecodeError, ValueError):
                return SecretStr(payload.decode("utf-8", errors="replace"))
        else:
            return SecretStr(payload.decode("utf-8", errors="replace"))

    def delete(self, label_or_id: str) -> bool:
        """Delete item by label or UUID.  Returns True if deleted.

        If *label_or_id* looks like a UUID (8-char hex prefix or full UUID),
        the item is looked up by ID first so that callers using credential
        IDs work without knowing the label.
        """
        # Try direct label delete first (most common for label-based callers)
        if self._store.delete(label_or_id):
            return True
        # Fall back: look up by ID (legacy callers may pass IDs)
        item = self._store.get_by_id(label_or_id)
        if item is not None:
            self._store.audit(item.id, "delete")
            return self._store.delete(item.label)
        return False

    # ── Rotate master key ─────────────────────────────────────────────────────

    def rotate(self, new_passphrase: bytes | None = None) -> int:
        """Re-encrypt all DEK wrappers with a new master key.  Returns item count."""
        old_master = self._master_key()

        import os  # noqa: PLC0415

        salt_path = self.vault_dir / CryptoEngine.SALT_FILE
        new_salt = os.urandom(32)
        self._engine._salt = None
        salt_path.write_bytes(new_salt)
        set_owner_only_file_permissions(salt_path)

        new_master = self._engine.derive_key(new_passphrase)
        items = self._store.list()
        rotated = 0
        for item in items:
            try:
                dek = CryptoEngine.open(old_master, item.encrypted_dek)
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
                    last_used_at=item.last_used_at,
                    version=item.version + 1,
                )
                self._store.upsert(rotated_item)
                self._store.audit(item.id, "rotate")
                rotated += 1
            except CryptoError:
                pass

        session = SessionStore.get()
        if session is not None:
            SessionStore.set(
                VaultSession(
                    master_key=new_master,
                    unlocked_at=session.unlocked_at,
                    ttl_seconds=session.ttl_seconds,
                )
            )
        return rotated

    # ── Fingerprint-change detection ──────────────────────────────────────────

    def _fingerprint_path(self) -> Path:
        """Path to the file that caches the current machine fingerprint."""
        return self.vault_dir / ".vault_fp"

    def _read_stored_fingerprint(self) -> bytes | None:
        """Return the fingerprint stored on disk, or ``None`` if absent."""
        fp = self._fingerprint_path()
        try:
            data = fp.read_bytes()
            return data if data else None
        except OSError:
            return None

    def _write_stored_fingerprint(self, fp_bytes: bytes) -> None:
        """Persist the fingerprint to disk (best-effort)."""
        fp = self._fingerprint_path()
        try:
            fp.write_bytes(fp_bytes)
            set_owner_only_file_permissions(fp)
        except Exception:
            pass

    def _rekey_from_old_fingerprint(self, old_fp_bytes: bytes) -> int:
        """Re-wrap all DEK envelopes: old fingerprint-derived key → current key.

        This is the vault-side analogue of ``rotate()`` but instead of
        generating a new salt it keeps the existing salt and simply swaps the
        key-encryption key.  Returns the number of items successfully re-keyed.
        """
        salt = self._engine._get_or_create_salt()
        old_master = self._engine._kdf(old_fp_bytes, salt)
        new_master = self._master_key()
        items = self._store.list()
        rekeyed = 0
        for item in items:
            if not item.encrypted_dek or not item.encrypted_blob:
                continue
            try:
                dek = CryptoEngine.open(old_master, item.encrypted_dek)
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
                    last_used_at=item.last_used_at,
                    version=item.version + 1,
                )
                self._store.upsert(rotated_item)
                rekeyed += 1
            except CryptoError:
                pass
        return rekeyed

    # ── JSON key files ────────────────────────────────────────────────────────

    def put_json_file(
        self,
        label: str,
        source: str | Path,
        *,
        provider: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Encrypt and store a JSON key file (e.g. Google service account)."""
        import json as _json  # noqa: PLC0415

        src_path = Path(source).expanduser() if isinstance(source, (str, Path)) else None
        if src_path is not None and src_path.exists():
            raw = src_path.read_text(encoding="utf-8")
            original_name = src_path.name
        else:
            raw = str(source)
            original_name = None

        try:
            _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON provided to put_json_file: {exc}") from exc

        meta = dict(metadata or {})
        meta["content_type"] = "application/json"
        if original_name:
            meta["original_file"] = original_name

        return self.put(
            label, raw.encode("utf-8"), kind=VaultItemKind.FILE, provider=provider, metadata=meta
        )

    def get_json_file(self, label: str) -> dict:
        """Decrypt and return a stored JSON key file as a Python dict."""
        import json as _json  # noqa: PLC0415

        return _json.loads(self.get_bytes(label))

    def get_json_str(self, label: str) -> str:
        """Decrypt and return a stored JSON key file as a raw JSON string."""
        return self.get_bytes(label).decode("utf-8")

    # ── Native label-based list / search / count ─────────────────────────────

    def list(
        self,
        kind: VaultItemKind | None = None,
        provider: str | None = None,
        profile_id: str | None = None,
    ) -> list[VaultItem]:
        """Return vault items, optionally filtered.

        When *profile_id* is supplied the results are further narrowed by
        ``item.metadata["profile_id"]``.
        """
        items = self._store.list(kind=kind, provider=provider)
        if profile_id is not None:
            items = [i for i in items if i.metadata.get("profile_id", "default") == profile_id]
        return items

    def search(self, query: str) -> list[VaultItem]:
        return self._store.search(query)

    def count(self, provider: str | None = None) -> int:
        return self._store.count(provider=provider) if provider else self._store.count()

    def store(self) -> VaultStore:
        """Expose the underlying store (for advanced use)."""
        return self._store

    def engine(self) -> CryptoEngine:
        """Expose the crypto engine (for advanced use)."""
        return self._engine

    # ─────────────────────────────────────────────────────────────────────────
    # Provider/profile compatibility adapter API
    # These methods bridge the old CredentialsVault (provider/profile model)
    # to the new label-based storage.  Zero changes required in call sites.
    # ─────────────────────────────────────────────────────────────────────────

    def add(
        self,
        provider: str,
        credential_type: str = "generic",
        data: dict | None = None,
        profile_id: str = "default",
        label: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Store a provider/profile credential and return its ID.

        Compatible with ``CredentialsVault.add()``.
        """
        v_label = _cred_label(provider, profile_id)
        display_label = label or f"{provider.title()} ({profile_id})"
        meta = dict(metadata or {})
        meta.update(
            {
                "credential_type": credential_type,
                "profile_id": profile_id,
                "label": display_label,
                "enabled": meta.get("enabled", True),
            }
        )
        return self.put(
            v_label,
            json.dumps(data or {}).encode(),
            kind=VaultItemKind.PROVIDER,
            provider=provider,
            metadata=meta,
        )

    def get(
        self,
        provider: str,
        profile_id: str | None = None,
        caller: str = "unknown",
    ) -> Credential | None:
        """Return a ``Credential`` for *provider* / *profile_id*, or None if not found."""
        v_label = _cred_label(provider, profile_id or "default")
        item = self._store.get(v_label)
        if item is None:
            # Fallback: search by provider tag (picks first match)
            matches = self._store.list(provider=provider)
            prof = profile_id or "default"
            matches = [m for m in matches if m.metadata.get("profile_id", "default") == prof]
            if not matches:
                return None
            # Priority: active=True → most-recently-used → created_at desc
            active_m = [m for m in matches if m.metadata.get("active", False)]
            item = (
                active_m[0]
                if active_m
                else sorted(
                    matches,
                    key=lambda m: m.last_used_at or m.created_at,
                    reverse=True,
                )[0]
            )
        # Return None for disabled credentials
        if not item.metadata.get("enabled", True):
            return None
        try:
            payload = self.get_bytes(item.label)
            decoded_data = json.loads(payload)
        except (KeyError, json.JSONDecodeError, Exception):
            decoded_data = {}
        return _item_to_credential(item, decoded_data)

    def get_api_key(
        self,
        provider: str,
        profile_id: str | None = None,
        caller: str = "unknown",
    ) -> str | None:
        """Return the primary API key for *provider*, or None if not stored.

        Resolution order:
        1. Vault lookup (active profile when profile_id is None)
        2. Environment variable: ``{PROVIDER_UPPER}_API_KEY``
        """
        import os as _os  # noqa: PLC0415

        resolved_profile = profile_id or self.get_active_profile()
        try:
            v_label = _cred_label(provider, resolved_profile)
            # Try label-direct first
            try:
                secret = self.get_secret(v_label)
                if secret is not None:
                    return secret.reveal() if hasattr(secret, "reveal") else str(secret)
            except KeyError:
                pass
            # Compatibility fallback – also tries active profile
            cred = self.get(provider, resolved_profile, caller=caller)
            if cred is not None:
                val = cred.data.get("api_key") or cred.data.get("token") or cred.data.get("value")
                if val:
                    return str(val)
        except Exception:  # noqa: BLE001
            pass

        # Environment variable fallback: PROVIDER_API_KEY (e.g. GROQ_API_KEY)
        env_key = provider.upper().replace(".", "_").replace("-", "_") + "_API_KEY"
        env_val = _os.environ.get(env_key)
        if env_val:
            return env_val

        return None

    def get_by_id(
        self,
        credential_id: str,
        caller: str = "unknown",
    ) -> Credential | None:
        """Return a full ``Credential`` by its UUID, or None if not found."""
        item = self._store.get_by_id(credential_id)
        if item is None:
            return None
        try:
            payload = self.get_bytes(item.label)
            decoded_data = json.loads(payload)
        except (KeyError, json.JSONDecodeError, Exception):
            decoded_data = {}
        return _item_to_credential(item, decoded_data)

    def update(
        self,
        credential_id: str,
        data: dict | None = None,
        label: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Update credential data and/or label.  Returns True if found and updated."""
        item = self._store.get_by_id(credential_id)
        if item is None:
            return False

        # Decrypt existing data and merge
        decrypt_failed = False
        try:
            existing_payload = self.get_bytes(item.label)
            existing_data = json.loads(existing_payload)
        except (KeyError, json.JSONDecodeError):
            existing_data = {}
        except Exception:  # noqa: BLE001  — e.g. CryptoError, OSError
            # Decryption failed.  If the caller is only updating metadata/label
            # (data=None), preserve the encrypted blob unchanged to prevent
            # silently wiping the credential's secret.
            decrypt_failed = True
            existing_data = {}

        if decrypt_failed and data is None:
            # Metadata / label-only update — patch the item metadata in-place
            # without touching the encrypted DEK or blob.
            new_meta = dict(item.metadata)
            if metadata is not None:
                new_meta.update(metadata)
            if label is not None:
                new_meta["label"] = label
            patched = VaultItem(
                id=item.id,
                kind=item.kind,
                label=item.label,
                provider=item.provider,
                encrypted_dek=item.encrypted_dek,
                encrypted_blob=item.encrypted_blob,
                metadata=new_meta,
                created_at=item.created_at,
                updated_at=datetime.now(timezone.utc),
                last_used_at=item.last_used_at,
                version=item.version + 1,
            )
            self._store.upsert(patched)
            return True

        if data is not None:
            existing_data.update(data)

        new_meta = dict(item.metadata)
        if metadata is not None:
            new_meta.update(metadata)
        if label is not None:
            new_meta["label"] = label

        self.put(
            item.label,
            json.dumps(existing_data).encode(),
            kind=item.kind,
            provider=item.provider,
            metadata=new_meta,
        )
        return True

    def activate(self, credential_id: str) -> bool:
        """Mark a credential as the active (preferred) one for its provider.

        Clears the ``active`` flag from every other credential for the same
        provider, then sets it on this one. Also syncs the active-profile file
        so ``get_api_key()`` resolves it automatically.

        Returns True if found and updated.
        """
        target = self._store.get_by_id(credential_id)
        if target is None:
            return False

        # Clear active flag on all siblings (same provider, different id)
        for sib in self._store.list(provider=target.provider):
            if sib.id == target.id:
                continue
            if sib.metadata.get("active", False):
                sib_meta = dict(sib.metadata)
                sib_meta["active"] = False
                self._store.upsert(
                    VaultItem(
                        id=sib.id,
                        kind=sib.kind,
                        label=sib.label,
                        provider=sib.provider,
                        encrypted_dek=sib.encrypted_dek,
                        encrypted_blob=sib.encrypted_blob,
                        metadata=sib_meta,
                        created_at=sib.created_at,
                        updated_at=datetime.now(timezone.utc),
                        last_used_at=sib.last_used_at,
                        version=sib.version + 1,
                    )
                )

        # Set active flag on target
        tgt_meta = dict(target.metadata)
        tgt_meta["active"] = True
        self._store.upsert(
            VaultItem(
                id=target.id,
                kind=target.kind,
                label=target.label,
                provider=target.provider,
                encrypted_dek=target.encrypted_dek,
                encrypted_blob=target.encrypted_blob,
                metadata=tgt_meta,
                created_at=target.created_at,
                updated_at=datetime.now(timezone.utc),
                last_used_at=target.last_used_at,
                version=target.version + 1,
            )
        )
        self._store.audit(target.id, "activated")
        self.set_active_profile(target.metadata.get("profile_id", "default"))
        return True

    def enable(self, credential_id: str) -> bool:
        """Mark a credential as enabled.  Returns True if found."""
        item = self._store.get_by_id(credential_id)
        if item is None:
            return False
        new_meta = dict(item.metadata)
        new_meta["enabled"] = True
        # Re-put preserving encrypted blob (no-decrypt shortcut via upsert)
        updated = VaultItem(
            id=item.id,
            kind=item.kind,
            label=item.label,
            provider=item.provider,
            encrypted_dek=item.encrypted_dek,
            encrypted_blob=item.encrypted_blob,
            metadata=new_meta,
            created_at=item.created_at,
            updated_at=datetime.now(timezone.utc),
            last_used_at=item.last_used_at,
            version=item.version + 1,
        )
        self._store.upsert(updated)
        self._store.audit(item.id, "enabled")
        return True

    def disable(self, credential_id: str) -> bool:
        """Mark a credential as disabled.  Returns True if found."""
        item = self._store.get_by_id(credential_id)
        if item is None:
            return False
        new_meta = dict(item.metadata)
        new_meta["enabled"] = False
        updated = VaultItem(
            id=item.id,
            kind=item.kind,
            label=item.label,
            provider=item.provider,
            encrypted_dek=item.encrypted_dek,
            encrypted_blob=item.encrypted_blob,
            metadata=new_meta,
            created_at=item.created_at,
            updated_at=datetime.now(timezone.utc),
            last_used_at=item.last_used_at,
            version=item.version + 1,
        )
        self._store.upsert(updated)
        self._store.audit(item.id, "disabled")
        return True

    def clone(
        self,
        credential_id: str,
        profile: str,
        label: str | None = None,
    ) -> str | None:
        """Clone a credential to a different profile.  Returns new ID, or None."""
        src_item = self._store.get_by_id(credential_id)
        if src_item is None:
            return None
        try:
            payload = self.get_bytes(src_item.label)
        except Exception:  # noqa: BLE001
            return None
        src_meta = dict(src_item.metadata)
        provider = src_item.provider or src_item.label.split("/")[0]
        new_meta = dict(src_meta)
        new_meta["profile_id"] = profile
        if label:
            new_meta["label"] = label
        new_label = _cred_label(provider, profile)
        return self.put(
            new_label, payload, kind=src_item.kind, provider=provider, metadata=new_meta
        )

    def list_profiles(self) -> list[str]:
        """Return all unique profile IDs across stored credentials."""
        items = self._store.list()
        profiles = {item.metadata.get("profile_id", "default") for item in items}
        profiles.add("default")
        return sorted(profiles)

    def get_active_profile(self) -> str:
        """Return the currently active profile (default: 'default')."""
        profile_file = self.vault_dir / "active_profile.txt"
        try:
            if profile_file.exists():
                return profile_file.read_text(encoding="utf-8").strip() or "default"
        except OSError:
            pass
        return "default"

    def set_active_profile(self, profile_id: str) -> None:
        """Persist the active profile selection."""
        profile_file = self.vault_dir / "active_profile.txt"
        try:
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(profile_file, profile_id or "default")
        except OSError:
            pass

    def get_audit_log(
        self,
        credential_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return audit log entries, newest first.

        If *credential_id* is given, filters to that item only.
        """
        if credential_id:
            rows = self._store.get_audit(credential_id)
        else:
            try:
                conn = self._store._connect()
                rows_raw = conn.execute(
                    "SELECT * FROM vault_audit ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
                rows = [dict(r) for r in rows_raw]
            except Exception:  # noqa: BLE001
                rows = []
        return rows[:limit]

    def list_creds(
        self,
        provider: str | None = None,
        profile_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[CredentialInfo]:
        """List stored credentials as ``CredentialInfo`` objects.

        This is the compatibility listing method used by ``navig cred list``.
        """
        items = self.list(provider=provider, profile_id=profile_id)
        result = [_item_to_cred_info(i) for i in items]
        if enabled_only:
            result = [c for c in result if c.enabled]
        return result

    def test(self, credential_id: str) -> TestResult:
        """Validate a credential by its UUID against the provider's API."""
        cred = self.get_by_id(credential_id)
        if cred is None:
            return TestResult(success=False, message=f"Credential {credential_id} not found.")
        return self._run_validator(cred)

    def test_provider(
        self,
        provider: str,
        profile_id: str | None = None,
    ) -> TestResult:
        """Validate the credential for *provider* against the provider's API."""
        cred = self.get(provider, profile_id)
        if cred is None:
            return TestResult(
                success=False, message=f"No credential found for provider '{provider}'."
            )
        return self._run_validator(cred)

    def _run_validator(self, cred: Credential) -> TestResult:
        try:
            from navig.vault import validators as _validators_mod  # noqa: PLC0415

            validator = _validators_mod.get_validator(cred.provider)
            return validator.validate(cred)
        except Exception as exc:  # noqa: BLE001
            return TestResult(success=False, message=f"Validation error: {exc}")

    # ─────────────────────────────────────────────────────────────────────────
    # Legacy list() overload — preserves compatibility with call sites that
    # call vault.list(provider=..., profile_id=...) and iterate CredentialInfo.
    # ─────────────────────────────────────────────────────────────────────────

    # NOTE: list() already accepts profile_id kwarg and returns list[VaultItem].
    # Commands that need CredentialInfo (cred list / cred check-all) should call
    # list_creds() instead  — but we keep a dual-mode shim below so that old
    # code using `for c in vault.list(...): c.provider` still works because
    # VaultItem also has .provider.


# ── Backward-compat aliases ───────────────────────────────────────────────────


# ── Module-level singletons ───────────────────────────────────────────────────

_vault: Vault | None = None


def get_vault(vault_dir: Path | None = None) -> Vault:
    """Return (or create) the global :class:`Vault` singleton.

    On first call, if a legacy database exists and has not yet been
    migrated, a silent migration is triggered automatically.

    Parameters
    ----------
    vault_dir : Override the storage directory.  Uses ``vault_dir()`` from
                ``navig.platform.paths`` by default.
    """
    global _vault
    if _vault is None:
        if vault_dir is None:
            from navig.platform.paths import vault_dir as _vault_dir_fn  # noqa: PLC0415

            vault_dir = _vault_dir_fn()
        _vault = Vault(vault_dir)
        _auto_migrate(_vault)
    return _vault


def _migrate_auth_profiles(vault: "Vault") -> None:
    """Migrate credentials from ``auth-profiles.json`` in *vault.vault_dir* into the vault.

    Called automatically when ``Vault(auto_migrate=True)`` is used.
    Renames the source file to ``auth-profiles.json.migrated`` on success.
    Never raises — migration failures are silently swallowed.
    """
    import json as _json  # noqa: PLC0415

    legacy = vault.vault_dir / "auth-profiles.json"
    if not legacy.exists():
        return
    try:
        data = _json.loads(legacy.read_text(encoding="utf-8"))
        for profile_id, cred in data.get("profiles", {}).items():
            provider = cred.get("provider", profile_id.split(":")[0])
            key = cred.get("key") or cred.get("api_key") or cred.get("token")
            if not key:
                continue
            path = _cred_label(provider, profile_id)
            if vault._store.get(path) is None:
                cred_data: dict = {"api_key": key}
                if cred.get("email"):
                    cred_data["email"] = cred["email"]
                vault.add(
                    provider=provider,
                    credential_type=cred.get("type", "api_key"),
                    data=cred_data,
                    profile_id=profile_id,
                    label="Migrated from auth-profiles.json",
                )
        legacy.rename(legacy.with_suffix(".json.migrated"))
    except Exception:  # noqa: BLE001
        pass  # Never crash on migration


def _maybe_upgrade_fingerprint(vault: Vault) -> None:
    """Silently re-key vault items when the machine fingerprint changed.

    This handles two upgrade scenarios transparently:

    1. **First run after the stable-fingerprint feature landed** (no
       ``.vault_fp`` file yet).  We probe whether the current master key can
       decrypt any stored item.  If it cannot, we try the legacy fingerprint
       (``CryptoEngine._legacy_fingerprint()``) and re-key if that works.

    2. **Subsequent runs where the fingerprint changed** (e.g. the operator
       switched from 32-bit to 64-bit Python on macOS or Linux).  The stored
       ``.vault_fp`` no longer matches; we re-key from the old fingerprint to
       the new one and update the file.

    All errors are silently swallowed — the vault does not raise on startup.
    """
    try:
        current_fp = CryptoEngine._machine_fingerprint()
        stored_fp = vault._read_stored_fingerprint()

        if stored_fp is None:
            # First run: check whether current key actually works.
            items = vault._store.list()
            decryptable = 0
            for item in items[:3]:  # sample at most 3 items
                if not item.encrypted_dek:
                    continue
                try:
                    CryptoEngine.open(vault._master_key(), item.encrypted_dek)
                    decryptable += 1
                except CryptoError:
                    break
            if decryptable == 0 and any(i.encrypted_dek for i in items):
                # Current key cannot decrypt — attempt legacy fingerprint
                legacy_fp = CryptoEngine._legacy_fingerprint()
                if legacy_fp != current_fp:
                    n = vault._rekey_from_old_fingerprint(legacy_fp)
                    if n > 0:
                        vault._write_stored_fingerprint(current_fp)
                        return
            vault._write_stored_fingerprint(current_fp)

        elif stored_fp == current_fp:
            return  # fast path — fingerprint unchanged

        else:
            # Fingerprint changed between runs — re-key from stored → current.
            vault._rekey_from_old_fingerprint(stored_fp)
            vault._write_stored_fingerprint(current_fp)

    except Exception:  # noqa: BLE001
        pass  # never crash on startup due to fingerprint upgrade


def _auto_migrate(vault: Vault) -> None:
    """Silently migrate legacy credentials to the unified vault if not done yet."""
    _maybe_upgrade_fingerprint(vault)
    sentinel = vault.vault_dir / ".migrated_legacy"
    legacy_sentinel = vault.vault_dir / ".migrated_v1"
    if sentinel.exists() or legacy_sentinel.exists():
        return
    try:
        from navig.vault.migrate import check_legacy_exists, migrate_from_legacy  # noqa: PLC0415

        if not check_legacy_exists():
            sentinel.touch()
            return
        report = migrate_from_legacy()
        if report.ok() or report.migrated > 0:
            sentinel.touch()
    except Exception:  # noqa: BLE001
        pass  # Never crash on auto-migration; user can run navig vault migrate manually
