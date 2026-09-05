"""VaultStore must lock its SQLite files owner-only so the credential inventory (labels,
provider names, metadata: domain/username/url) and the access-audit log can't be read by
other local users on a multi-user host. WAL is on, so the -wal/-shm siblings — which hold
pending rows — are locked alongside the main DB. This mirrors the salt (crypto.py) and the
legacy DB (storage.py); the new canonical store was the one missing it.
"""

from __future__ import annotations

import stat
import sys

import pytest

import navig.core.file_permissions as fp
from navig.vault.store import VaultStore


def test_connect_locks_the_store_files_owner_only(tmp_path, monkeypatch):
    """Spy the permission helper (platform-independent): opening the store must lock the DB
    file — pre-fix it was never called, so other users could read vault.db directly."""
    locked: list[str] = []
    monkeypatch.setattr(
        fp, "set_owner_only_file_permissions", lambda p: locked.append(str(p))
    )

    vault_dir = tmp_path / "vault"
    store = VaultStore(vault_dir)
    store._connect()  # creates vault.db (+ -wal/-shm under WAL)

    db = str(vault_dir / VaultStore.DB_FILE)
    assert db in locked  # the main DB was locked owner-only
    # any WAL sibling that exists is locked too (never left world-readable)
    for suffix in ("-wal", "-shm"):
        if (vault_dir / (VaultStore.DB_FILE + suffix)).exists():
            assert db + suffix in locked


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="mode bits are POSIX-only; Windows enforces the same intent through ACLs",
)
def test_store_files_are_actually_0600_on_posix(tmp_path):
    """On POSIX, verify the real mode bits.

    This used to `return` on Windows, which reports **PASS** — a green tick over a check
    that never ran, on the platform this repo is developed on. The docstring already said
    "skipped on Windows"; only the mechanism was wrong. `skipif` makes the summary say what
    actually happened.
    """
    vault_dir = tmp_path / "vault"
    store = VaultStore(vault_dir)
    store._connect()

    assert stat.S_IMODE(vault_dir.stat().st_mode) == 0o700  # dir owner-only, still traversable
    db_path = vault_dir / VaultStore.DB_FILE
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600  # DB not world/group readable
