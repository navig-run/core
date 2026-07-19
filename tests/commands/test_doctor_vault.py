"""Tests for the ``navig doctor`` Vault check (check_vault).

Purely LOCAL checks — no daemon involved, and no secret may ever reach the
output (only counts):
- no vault DB yet → informational green ("created on first `navig vault set`");
- healthy vault → green with item COUNT + a decrypt probe ("encryption OK");
- legacy credentials DB present and unmigrated → warn (auto-migrates on use);
- legacy present but the migration marker exists → green ("legacy DB retained");
- store open / decrypt failure → hard failure carrying the exception CLASS
  name only (messages could embed paths or item labels).

Everything resolves through call-time paths — ``NAVIG_CONFIG_DIR`` is honoured
at the moment the check runs, the regression class fixed in
``navig/vault/migrate.py`` (``_legacy_db_path``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from navig.commands.doctor import check_vault


@pytest.fixture()
def navig_home(tmp_path, monkeypatch) -> Path:
    """Point every vault path at a throwaway NAVIG_CONFIG_DIR (call-time resolved)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # A passphrase session leaked from another test would poison key derivation
    # for the vaults built here via the public API.
    from navig.vault.session import SessionStore

    SessionStore.clear()
    yield tmp_path
    SessionStore.clear()


def _row(results: list[tuple[str, bool, str]], label: str) -> tuple[str, bool, str]:
    for icon, ok, line in results:
        if f" {label}:" in line:
            return icon, ok, line
    raise AssertionError(f"no {label!r} row in {results!r}")


def _fabricate_legacy_db(cfg_dir: Path) -> Path:
    """The minimal legacy DB migrate.py detects: check_legacy_exists() is existence-only."""
    legacy = cfg_dir / "credentials" / "vault.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"")
    return legacy


def _ensure_salt(vdir: Path) -> None:
    """Make sure vault.salt exists, as it always does for a production vault.

    Real ``derive_key`` creates the salt file on the first ``put``; the
    suite-wide conftest replaces ``CryptoEngine.derive_key`` with a fast,
    salt-less test KDF (the argon2 DLL hangs on this platform), so a vault
    built under pytest never grows one. check_vault treats items-without-salt
    as a decrypt failure, so restore the production invariant here. The
    content is irrelevant under the patched KDF; under real crypto this
    branch never runs.
    """
    from navig.vault.crypto import CryptoEngine

    salt = vdir / CryptoEngine.SALT_FILE
    if not salt.exists():
        salt.write_bytes(os.urandom(32))


def test_no_vault_yet_is_informational_green(navig_home):
    results = check_vault()
    icon, ok, line = _row(results, "Vault")
    assert ok is True  # missing vault must never fail doctor
    assert icon == "✓"
    assert "no vault yet" in line
    assert "navig vault set" in line
    assert len(results) == 1  # no legacy DB → no legacy row


def test_healthy_vault_counts_items_and_probes_encryption(navig_home):
    from navig.vault.core import Vault

    vdir = navig_home / "vault"
    vault = Vault(vdir)
    vault.put("doctor-probe-alpha", b'{"value": "s3cr3t-alpha"}')
    vault.put("doctor-probe-beta", b'{"value": "s3cr3t-beta"}')
    _ensure_salt(vdir)

    results = check_vault()
    icon, ok, line = _row(results, "Vault")
    assert ok is True
    assert icon == "✓"
    assert "2 item(s)" in line
    assert "encryption OK" in line
    # Only counts may surface — never labels, never payloads.
    joined = " ".join(r[2] for r in results)
    assert "doctor-probe" not in joined
    assert "s3cr3t" not in joined


def test_unmigrated_legacy_db_warns(navig_home):
    _fabricate_legacy_db(navig_home)

    icon, ok, line = _row(check_vault(), "Legacy credentials")
    # House style: a ⚠ row is ok=False + warn=True — it flips doctor's exit
    # code (there is something to act on) but renders as a warning, not ✗.
    assert ok is False
    assert icon == "⚠"
    assert "legacy credentials DB present" in line
    assert "auto-migrate on next vault use" in line


def test_migrated_legacy_db_is_green(navig_home):
    from navig.vault.migrate import migration_marker_path

    _fabricate_legacy_db(navig_home)
    vdir = navig_home / "vault"
    vdir.mkdir(parents=True)
    migration_marker_path(vdir).touch()  # how _auto_migrate records completion

    icon, ok, line = _row(check_vault(), "Legacy credentials")
    assert ok is True
    assert icon == "✓"
    assert "legacy DB retained (migrated)" in line


def test_corrupted_db_reports_exception_class_only(navig_home):
    vdir = navig_home / "vault"
    vdir.mkdir(parents=True)
    (vdir / "vault.db").write_bytes(b"this is not a sqlite database " * 32)

    icon, ok, line = _row(check_vault(), "Vault")
    assert ok is False
    assert icon == "✗"
    assert "DatabaseError" in line
    # Class name ONLY — the exception message (which embeds the DB path on
    # some sqlite builds) must not leak into doctor output.
    assert str(navig_home) not in line


def test_undecryptable_item_reports_class_only(navig_home):
    import dataclasses

    from navig.vault.core import Vault

    vdir = navig_home / "vault"
    vault = Vault(vdir)
    vault.put("doctor-probe-gamma", b'{"value": "s3cr3t-gamma"}')
    _ensure_salt(vdir)
    # Corrupt the stored item's DEK wrapper: whatever master key derivation is
    # in effect (real or the suite's patched test KDF), the probe's
    # CryptoEngine.open() must now raise CryptoError.
    store = vault.store()
    item = store.list()[0]
    store.upsert(dataclasses.replace(item, encrypted_dek=b"xx"))

    icon, ok, line = _row(check_vault(), "Vault")
    assert ok is False
    assert icon == "✗"
    assert "CryptoError" in line
    assert "doctor-probe-gamma" not in line
    assert "s3cr3t" not in line
