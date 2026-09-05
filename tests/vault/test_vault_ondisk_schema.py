"""The vault's on-disk schema is a data contract with every existing user's ``vault.db``.

Why a snapshot test rather than a fixture database
--------------------------------------------------
The obvious way to prove "the format did not change" is to commit a vault written by the old
code and read it with the new. That cannot work here, and the reason is worth writing down so
nobody spends the afternoon rediscovering it: the master key is derived from the OS keyring
or, failing that, machine-specific data -- hostname, CPU architecture, OS user, and on Windows
the registry MachineGuid (``navig/vault/encryption.py``). A vault created on one machine is
undecryptable on any other, so a committed fixture would fail everywhere except the machine
that produced it, and it would commit machine-identifying material into the repo besides.

What IS portable is the schema. Every existing user's vault.db has these tables, columns and
indexes; a change to any of them is a migration, not an edit. So this asserts the shape
directly, and it is machine-independent because it inspects a vault the test itself creates.

This exists in particular to sit under the vault extraction: crypto/storage/store are moving
into the navig-vault package, and the one thing that move must NOT do is alter the format.
The suite's ~669 behavioural tests would catch a change that breaks reading, but a change
that is merely *different* -- a renamed column, a dropped index, a new table -- can pass them
all and still strand data written by an older navig.

Adding a column or index is normal evolution: update the expectation here in the SAME change,
so the diff shows a human decided the format moved. Removing or renaming one needs a
migration in ``navig/vault/migrate.py`` too.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Exactly what a vault.db carries today. Sorted, so the comparison is order-independent.
EXPECTED_TABLES = {
    "vault_items": [
        "id",
        "kind",
        "label",
        "provider",
        "encrypted_dek",
        "encrypted_blob",
        "metadata_json",
        "created_at",
        "updated_at",
        "last_used_at",
        "version",
    ],
    "vault_audit": ["id", "item_id", "action", "actor", "ts", "detail"],
}

EXPECTED_INDEXES = {"idx_vault_kind", "idx_vault_provider", "uidx_vault_label"}


@pytest.fixture
def written_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real vault created by the real code path, with one credential in it."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    from navig.vault import CredentialsVault

    db = tmp_path / "vault.db"
    vault = CredentialsVault(vault_path=db, auto_migrate=False)
    vault.add(
        provider="schema-probe",
        credential_type="api_key",
        data={"api_key": "not-a-real-key"},
        metadata={"note": "schema snapshot"},
    )
    del vault
    return db


def _schema(db: Path) -> tuple[dict[str, list[str]], set[str]]:
    con = sqlite3.connect(db)
    try:
        objects = con.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {
            name: [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
            for kind, name in objects
            if kind == "table"
        }
        indexes = {name for kind, name in objects if kind == "index"}
        return tables, indexes
    finally:
        con.close()


def test_tables_and_columns_are_unchanged(written_vault: Path) -> None:
    tables, _ = _schema(written_vault)
    assert set(tables) == set(EXPECTED_TABLES), (
        f"vault.db table set changed: {sorted(set(tables) ^ set(EXPECTED_TABLES))}. "
        f"That is a data-contract change for every existing vault -- it needs a migration "
        f"in navig/vault/migrate.py, not just an edit to storage.py."
    )
    for name, expected in EXPECTED_TABLES.items():
        assert tables[name] == expected, (
            f"vault.db column layout for {name!r} changed.\n"
            f"  expected: {expected}\n"
            f"  actual  : {tables[name]}\n"
            f"Adding a column is fine -- update the expectation in the SAME change so the "
            f"diff records the decision. Renaming or removing one strands existing data."
        )


def test_indexes_are_unchanged(written_vault: Path) -> None:
    _, indexes = _schema(written_vault)
    assert indexes == EXPECTED_INDEXES, (
        f"vault.db index set changed: {sorted(indexes ^ EXPECTED_INDEXES)}. "
        f"uidx_vault_label in particular is a UNIQUENESS constraint -- dropping it lets "
        f"duplicate labels in, and re-adding it later fails against data that already has them."
    )


def test_the_probe_actually_wrote_something(written_vault: Path) -> None:
    """Guards the guard: an empty database would satisfy nothing above by accident.

    If the fixture silently failed to store a credential, the schema assertions could still
    pass against a freshly-initialised file while proving nothing about a vault in use.
    """
    con = sqlite3.connect(written_vault)
    try:
        rows = con.execute("SELECT COUNT(*) FROM vault_items").fetchone()[0]
    finally:
        con.close()
    assert rows >= 1, "the fixture stored no credential -- the schema check above is vacuous"


def test_secrets_are_not_stored_in_the_clear(written_vault: Path) -> None:
    """The blob columns must not contain the plaintext that was just written.

    Cheap, and it is the property the whole package exists for -- worth asserting on the
    real file rather than trusting the layer above it.
    """
    raw = written_vault.read_bytes()
    assert b"not-a-real-key" not in raw, (
        "the plaintext secret is present in vault.db on disk -- encryption did not happen"
    )
