"""Tests for vault/migrate.py, vault/types.py, and comms/matrix_webhook._sign()."""
from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# vault/migrate.py — MigrationReport dataclass
# ──────────────────────────────────────────────────────────────────────────────
from navig.vault.migrate import MigrationReport, check_legacy_exists


class TestMigrationReport:
    def test_defaults(self, tmp_path):
        r = MigrationReport()
        assert r.migrated == 0
        assert r.skipped == 0
        assert r.errors == []
        assert r.dry_run is False

    def test_ok_when_no_errors(self):
        r = MigrationReport(migrated=3, skipped=1)
        assert r.ok() is True

    def test_not_ok_when_errors(self):
        r = MigrationReport(errors=["something failed"])
        assert r.ok() is False

    def test_summary_normal(self):
        r = MigrationReport(migrated=5, skipped=2, errors=["e1"])
        s = r.summary()
        assert "5" in s
        assert "2" in s
        assert "1" in s  # error count

    def test_summary_dry_run_tag(self):
        r = MigrationReport(dry_run=True)
        assert "DRY RUN" in r.summary()

    def test_summary_no_dry_run_tag(self):
        r = MigrationReport(dry_run=False)
        assert "DRY RUN" not in r.summary()

    def test_source_is_path(self):
        r = MigrationReport()
        assert isinstance(r.source, Path)


class TestCheckLegacyExists:
    def test_non_existent_path_returns_false(self, tmp_path):
        result = check_legacy_exists(tmp_path / "nope.db")
        assert result is False

    def test_existing_file_returns_true(self, tmp_path):
        db = tmp_path / "vault.db"
        db.write_bytes(b"sqlite")
        assert check_legacy_exists(db) is True

    def test_default_argument_returns_bool(self):
        # Should not raise regardless of whether db exists on this machine
        result = check_legacy_exists()
        assert isinstance(result, bool)


# ──────────────────────────────────────────────────────────────────────────────
# vault/types.py — enums, dataclasses, PROVIDER_PRESETS
# ──────────────────────────────────────────────────────────────────────────────
from navig.vault.types import (
    PROVIDER_PRESETS,
    Credential,
    CredentialInfo,
    CredentialType,
    TestResult,
    VaultItem,
    VaultItemKind,
)


class TestCredentialType:
    def test_all_values(self):
        names = {c.name for c in CredentialType}
        assert names == {"API_KEY", "OAUTH", "EMAIL", "TOKEN", "PASSWORD", "SSH_KEY", "GENERIC"}

    def test_inherits_str(self):
        assert isinstance(CredentialType.API_KEY, str)

    def test_api_key_value(self):
        assert CredentialType.API_KEY == "api_key"


class TestCredential:
    def _make(self, **kwargs):
        defaults = dict(
            id="abc12345",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="My Key",
            data={"api_key": "sk-test"},
        )
        defaults.update(kwargs)
        return Credential(**defaults)

    def test_creation(self):
        c = self._make()
        assert c.provider == "openai"
        assert c.data["api_key"] == "sk-test"

    def test_get_secret_found(self):
        c = self._make(data={"api_key": "sk-abc"})
        assert c.get_secret("api_key") == "sk-abc"

    def test_get_secret_missing(self):
        c = self._make(data={})
        assert c.get_secret("api_key") is None

    def test_generate_id_length(self):
        id_ = Credential.generate_id()
        assert len(id_) == 8

    def test_generate_id_unique(self):
        ids = {Credential.generate_id() for _ in range(50)}
        assert len(ids) > 1

    def test_enabled_default(self):
        c = self._make()
        assert c.enabled is True

    def test_created_at_is_datetime(self):
        c = self._make()
        assert isinstance(c.created_at, datetime)


class TestCredentialInfo:
    def _make(self, **kwargs):
        defaults = dict(
            id="12345678",
            provider="github",
            profile_id="work",
            credential_type=CredentialType.TOKEN,
            label="GitHub PAT",
            enabled=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
        defaults.update(kwargs)
        return CredentialInfo(**defaults)

    def test_str_contains_provider(self):
        info = self._make()
        assert "github" in str(info)

    def test_str_enabled_marker(self):
        assert "✓" in str(self._make(enabled=True))

    def test_str_disabled_marker(self):
        assert "✗" in str(self._make(enabled=False))

    def test_metadata_default(self):
        assert self._make().metadata == {}


class TestTestResult:
    def test_success_str(self):
        r = TestResult(success=True, message="OK!")
        assert "✅" in str(r)

    def test_failure_str(self):
        r = TestResult(success=False, message="invalid key")
        assert "❌" in str(r)

    def test_details_default(self):
        r = TestResult(success=True, message="ok")
        assert r.details == {}

    def test_tested_at_is_datetime(self):
        r = TestResult(success=True, message="ok")
        assert isinstance(r.tested_at, datetime)


class TestVaultItemKind:
    def test_all_kinds_present(self):
        names = {k.name for k in VaultItemKind}
        for expected in ("SECRET", "JSON", "CERT", "TOKEN", "PASSWORD", "GENERIC"):
            assert expected in names

    def test_inherits_str(self):
        assert isinstance(VaultItemKind.SECRET, str)


class TestVaultItem:
    def _make(self, **kwargs):
        defaults = dict(
            id=VaultItem.new_id(),
            kind=VaultItemKind.SECRET,
            label="test-item",
            provider="openai",
        )
        defaults.update(kwargs)
        return VaultItem(**defaults)

    def test_creation(self):
        item = self._make()
        assert item.kind == VaultItemKind.SECRET
        assert item.label == "test-item"

    def test_new_id_length(self):
        assert len(VaultItem.new_id()) == 8

    def test_enabled_default_true(self):
        item = self._make()
        assert item.enabled is True

    def test_enabled_from_metadata(self):
        item = self._make(metadata={"enabled": False})
        assert item.enabled is False

    def test_payload_default_empty(self):
        item = self._make()
        assert item.payload == b""

    def test_version_default(self):
        item = self._make()
        assert item.version == 1


class TestProviderPresets:
    def test_openai_preset(self):
        assert "openai" in PROVIDER_PRESETS
        assert PROVIDER_PRESETS["openai"]["credential_type"] == CredentialType.API_KEY

    def test_gmail_preset_type(self):
        assert PROVIDER_PRESETS["gmail"]["credential_type"] == CredentialType.EMAIL

    def test_github_preset_type(self):
        assert PROVIDER_PRESETS["github"]["credential_type"] == CredentialType.TOKEN

    def test_all_presets_have_credential_type(self):
        for name, preset in PROVIDER_PRESETS.items():
            assert "credential_type" in preset, f"Missing credential_type in {name}"

    def test_matrix_has_required_fields(self):
        meta = PROVIDER_PRESETS["matrix"]["metadata"]
        assert "required_fields" in meta


# ──────────────────────────────────────────────────────────────────────────────
# comms/matrix_webhook.py — _sign() pure helper
# ──────────────────────────────────────────────────────────────────────────────
from navig.comms.matrix_webhook import _sign


class TestSign:
    def test_returns_hex_string(self):
        result = _sign("payload", "secret")
        assert isinstance(result, str)
        # hex string is all lowercase hex digits
        int(result, 16)  # would raise ValueError if not valid hex

    def test_length_is_sha256(self):
        result = _sign("any payload", "any secret")
        assert len(result) == 64  # SHA-256 hex = 64 chars

    def test_deterministic(self):
        assert _sign("msg", "key") == _sign("msg", "key")

    def test_different_secrets_give_different_sigs(self):
        assert _sign("msg", "key1") != _sign("msg", "key2")

    def test_different_payloads_give_different_sigs(self):
        assert _sign("msg1", "key") != _sign("msg2", "key")

    def test_matches_manual_hmac(self):
        payload, secret = "test-payload", "super-secret"
        expected = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        assert _sign(payload, secret) == expected
