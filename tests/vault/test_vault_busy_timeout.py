"""Both vault SQLite stores must WAIT out an inter-process lock rather than erroring
instantly with "database is locked" (the CLI writing while the daemon writes; a backup /
AV agent holding the file). On the SECRETS read path that transient lock surfaced a live
key as ABSENT (the phantom-empty class, #687).

These tests hold a real lock from a second connection and release it from a timer, then
assert the vault operation waited and succeeded. Asserting the ``busy_timeout`` PRAGMA
value proved nothing: Python's ``sqlite3.connect`` already defaults ``timeout`` to 5s, so
that check passed with the stores' explicit PRAGMA removed. Each test also opens a probe
with ``timeout=0`` under the same lock and asserts it fails, so the contention is real and
the wait is what made the operation succeed.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from navig.vault.encryption import VaultEncryption
from navig.vault.storage import VaultStorage
from navig.vault.store import VaultStore
from navig.vault.types import Credential, CredentialType, VaultItem, VaultItemKind

_HOLD_S = 0.5


def _hold_lock(db_path, begin: str) -> tuple[sqlite3.Connection, threading.Timer]:
    """Take ``begin`` on a second connection and release it after ``_HOLD_S``."""
    holder = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    holder.execute(begin)
    timer = threading.Timer(_HOLD_S, holder.commit)
    timer.start()
    return holder, timer


def _assert_contended(db_path, sql: str, params: tuple = ()) -> None:
    probe = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            probe.execute(sql, params).fetchall()
    finally:
        probe.close()


def test_vault_store_write_waits_out_another_process_lock(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store._connect()  # create the schema before another connection takes the lock
    item = VaultItem(id="id-1", kind=VaultItemKind.SECRET, label="openai", provider="openai")

    holder, timer = _hold_lock(store.db_path(), "BEGIN IMMEDIATE")
    try:
        _assert_contended(store.db_path(), "BEGIN IMMEDIATE")
        started = time.monotonic()
        store.upsert(item)
        waited = time.monotonic() - started
    finally:
        timer.join()
        holder.close()

    assert waited >= _HOLD_S * 0.8, "the write did not actually contend with the lock"
    got = store.get("openai")
    assert got is not None and got.id == "id-1"
    store.close()


def test_legacy_vault_storage_read_waits_out_another_process_lock(tmp_path):
    storage = VaultStorage(tmp_path / "legacy.db", VaultEncryption(tmp_path))
    storage.save(
        Credential(
            id="cred1234",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="OpenAI",
            data={"api_key": "sk-test"},
        )
    )

    # Rollback-journal mode: an EXCLUSIVE lock blocks readers too, which is the
    # phantom-empty shape — a read that errors or comes back empty while a key exists.
    holder, timer = _hold_lock(storage.vault_path, "BEGIN EXCLUSIVE")
    try:
        _assert_contended(storage.vault_path, "SELECT COUNT(*) FROM credentials")
        started = time.monotonic()
        got = storage.get("cred1234")
        waited = time.monotonic() - started
    finally:
        timer.join()
        holder.close()

    assert waited >= _HOLD_S * 0.8, "the read did not actually contend with the lock"
    assert got is not None, "a live credential read as absent under a transient lock"
    assert got.data["api_key"] == "sk-test"
