"""Both vault SQLite stores must set ``busy_timeout`` so an INTER-process lock (the CLI
writing while the daemon writes; a backup / AV agent holding the file) waits briefly
instead of erroring instantly with "database is locked". On the SECRETS read path that
transient lock surfaced a live key as ABSENT (the phantom-empty class, #687). 5000ms
matches the canonical store default (store/base.py · storage/pragma_profiles.py ·
memory/key_facts). The in-process RLock only serialises threads, not other processes.
"""

from __future__ import annotations

from navig.vault.encryption import VaultEncryption
from navig.vault.storage import VaultStorage
from navig.vault.store import VaultStore


def test_vault_store_sets_busy_timeout(tmp_path):
    store = VaultStore(tmp_path / "vault")
    conn = store._connect()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_legacy_vault_storage_sets_busy_timeout(tmp_path):
    enc = VaultEncryption(tmp_path)
    storage = VaultStorage(tmp_path / "legacy.db", enc)
    with storage._connection() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
