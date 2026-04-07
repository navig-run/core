"""NAVIG Vault Migration — upgrade legacy credentials/vault.db to the new vault.

Source : ~/.navig/credentials/vault.db  (Fernet + PBKDF2, old schema)
Target : ~/.navig/vault/vault.db         (AES-GCM + Argon2id, new schema)

The source is never modified or deleted.  Run ``navig vault migrate`` to execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["MigrationReport", "migrate_from_legacy", "check_legacy_exists"]

from navig.platform.paths import config_dir as _navig_config_dir

# Default paths
_LEGACY_DB = _navig_config_dir() / "credentials" / "vault.db"


@dataclass
class MigrationReport:
    """Summary returned by :func:`migrate_from_legacy`."""

    migrated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    source: Path = _LEGACY_DB

    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        tag = " [DRY RUN]" if self.dry_run else ""
        return (
            f"Migration{tag}: {self.migrated} migrated, "
            f"{self.skipped} skipped, {len(self.errors)} errors"
        )


def check_legacy_exists(legacy_path: Path | None = None) -> bool:
    """Return True if the legacy credentials DB exists."""
    return (legacy_path or _LEGACY_DB).exists()


def migrate_from_legacy(
    legacy_path: Path | None = None,
    dry_run: bool = False,
) -> MigrationReport:
    """Migrate credentials from the old Fernet-based vault to the new AES-GCM vault.

    Parameters
    ----------
    legacy_path : Override the legacy DB path (default: ``~/.navig/credentials/vault.db``).
    dry_run     : If True, read and report but do not write to the new vault.

    Returns
    -------
    MigrationReport
        Summary of what was migrated, skipped, or failed.

    Notes
    -----
    - The legacy vault is opened read-only.
    - Items that already exist in the new vault (same label) are skipped.
    - Decryption errors from Fernet are reported but do not abort the migration.
    - The legacy DB is *never* modified or deleted.
    """
    from navig.vault.core import get_vault
    from navig.vault.encryption import VaultEncryption
    from navig.vault.storage import VaultStorage
    from navig.vault.types import VaultItemKind

    src = legacy_path or _LEGACY_DB
    report = MigrationReport(dry_run=dry_run, source=src)

    if not src.exists():
        report.errors.append(f"Legacy DB not found: {src}")
        return report

    # Open legacy vault using VaultStorage + VaultEncryption (Fernet / PBKDF2)
    try:
        legacy_enc = VaultEncryption(src.parent)
        legacy_store = VaultStorage(src, legacy_enc)
    except Exception as exc:
        report.errors.append(f"Cannot initialize legacy vault: {exc}")
        return report

    new_vault = get_vault()

    # Read all credentials from the legacy table (metadata only first)
    try:
        all_creds_info = legacy_store.list_all(include_disabled=True)
    except Exception as exc:
        report.errors.append(f"Cannot read legacy credentials: {exc}")
        return report

    for info in all_creds_info:
        label = info.provider if info.profile_id == "default" else f"{info.provider}/{info.profile_id}"
        try:
            # Fetch with decrypted data
            cred = legacy_store.get_by_provider_profile(info.provider, info.profile_id)
            if cred is None:
                report.skipped += 1
                continue

            # Check if already in new vault
            if not dry_run:
                existing = new_vault.store().get(label)
                if existing:
                    report.skipped += 1
                    continue

            payload = json.dumps(cred.data).encode()

            if not dry_run:
                new_vault.put(
                    label=label,
                    payload=payload,
                    kind=VaultItemKind.PROVIDER,
                    provider=info.provider,
                    metadata={
                        "migrated_from": "legacy_vault",
                        "credential_type": info.credential_type.value,
                        "profile_id": info.profile_id,
                        "label": info.label,
                        "enabled": info.enabled,
                    },
                )
            report.migrated += 1

        except Exception as exc:
            report.errors.append(f"  {label}: {exc}")
    return report