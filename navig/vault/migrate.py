"""NAVIG Vault Migration — upgrade legacy credentials/vault.db to the new vault.

Source : ~/.navig/credentials/vault.db  (Fernet + PBKDF2, old schema)
Target : ~/.navig/vault/vault.db         (AES-GCM + Argon2id, new schema)

The source is never modified or deleted.  Run ``navig vault migrate`` to execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MigrationReport",
    "migrate_from_legacy",
    "check_legacy_exists",
    "legacy_migration_done",
    "migration_marker_path",
]

from navig.platform.paths import config_dir as _navig_config_dir
from navig.platform.paths import vault_dir as _navig_vault_dir


def _legacy_db_path() -> Path:
    """Resolve the legacy credentials DB path at CALL time — never import time.

    This must not be a module-level constant: ``config_dir()`` honours the
    ``NAVIG_CONFIG_DIR`` environment variable, and this module can be imported
    before the environment is finalized (a daemon setting its config dir after
    startup imports, or pytest collection importing test modules before the
    session isolation fixture runs). A path frozen at import would then point
    at the REAL user home, and ``get_vault()``'s auto-migration would silently
    copy the operator's real credentials into whatever vault is active.
    """
    return _navig_config_dir() / "credentials" / "vault.db"


# Marker files ``get_vault()``'s auto-migration touches inside the vault
# directory to record that the legacy DB has been dealt with. Current installs
# write the first name; historical installs may still carry the second.
_MIGRATION_MARKERS: tuple[str, ...] = (".migrated_legacy", ".migrated_v1")


def migration_marker_path(vault_dir: Path) -> Path:
    """Return the marker file a completed migration touches in *vault_dir*."""
    return vault_dir / _MIGRATION_MARKERS[0]


def legacy_migration_done(vault_dir: Path | None = None) -> bool:
    """Return True when a completed legacy migration is recorded for *vault_dir*.

    Single source of truth for the marker semantics — both ``get_vault()``'s
    auto-migration and the ``navig doctor`` Vault row resolve "has the legacy
    DB been migrated?" through here. The default directory is resolved at CALL
    time (same reason as :func:`_legacy_db_path`): a path frozen at import
    would ignore a later ``NAVIG_CONFIG_DIR`` and report another vault's state.
    """
    if vault_dir is None:
        vault_dir = _navig_vault_dir()
    return any((vault_dir / name).exists() for name in _MIGRATION_MARKERS)


@dataclass
class MigrationReport:
    """Summary returned by :func:`migrate_from_legacy`."""

    migrated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    source: Path = field(default_factory=_legacy_db_path)

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
    return (legacy_path or _legacy_db_path()).exists()


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

    src = legacy_path or _legacy_db_path()
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
