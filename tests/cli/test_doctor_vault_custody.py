"""`navig doctor` Vault rows: a keyslot vault probes healthy; ✓ only when it is recoverable.

Regression: the encryption probe opened a DEK with ``derive_key(None)`` — the
machine-fingerprint key. A keyslot vault's master key is random, so that probe
reported every healthy keyslot vault as "cannot open vault".
"""

from __future__ import annotations

import json

import pytest
import yaml

from navig.commands import doctor


@pytest.fixture
def vdir(tmp_path, monkeypatch):
    from navig_vault import keyslots
    from navig_vault.session import SessionStore

    d = tmp_path / "vault"
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(d))
    keyslots.forget_cached_vmks()
    SessionStore.clear()
    yield d
    keyslots.forget_cached_vmks()


def _rows(label: str):
    return [r for r in doctor.check_vault() if getattr(r, "label", "") == label]


def test_a_keyslot_vault_probes_healthy(vdir):
    from navig_vault.core import Vault

    v = Vault(vdir)
    v.put("a", json.dumps({"value": "x"}).encode())
    assert not v.is_legacy()
    v._store.close()

    (row,) = _rows("Vault")
    assert row[1] and "encryption OK" in row.detail


def test_custody_is_a_warning_until_a_recovery_key_exists(vdir):
    from navig_vault.core import Vault

    v = Vault(vdir)
    v.put("a", b"x")
    (row,) = _rows("Vault keys")
    assert not row[1] and "recovery" in row.detail

    v.add_recovery()
    v._store.close()
    (row,) = _rows("Vault keys")
    assert row[1] and "recovery" in row.detail


def test_plaintext_host_passwords_are_counted_never_shown(tmp_path, monkeypatch):
    from navig.config import reset_config_manager

    cfg = tmp_path / "cfg"
    (cfg / "hosts").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(tmp_path / "vault"))
    reset_config_manager()
    (cfg / "hosts" / "box.yaml").write_text(
        yaml.safe_dump({"host": "203.0.113.5", "ssh_password": "TOPSECRET-1"}), encoding="utf-8"
    )

    (row,) = _rows("Host credentials")
    assert not row[1] and "1 plaintext password(s) in 1 host file(s)" in row.detail
    assert "TOPSECRET" not in row.detail
