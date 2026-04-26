"""Hermetic unit tests for navig.vault.types — pure data models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from navig.vault.types import (
    PROVIDER_PRESETS,
    Credential,
    CredentialInfo,
    CredentialType,
    TestResult,
    VaultItem,
    VaultItemKind,
)

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# CredentialType enum
# ---------------------------------------------------------------------------


class TestCredentialType:
    def test_values(self):
        assert CredentialType.API_KEY == "api_key"
        assert CredentialType.OAUTH == "oauth"
        assert CredentialType.TOKEN == "token"
        assert CredentialType.PASSWORD == "password"
        assert CredentialType.SSH_KEY == "ssh_key"
        assert CredentialType.GENERIC == "generic"

    def test_is_str_subclass(self):
        assert isinstance(CredentialType.API_KEY, str)


# ---------------------------------------------------------------------------
# Credential.generate_id / get_secret
# ---------------------------------------------------------------------------


class TestCredential:
    def _make(self, data: dict | None = None) -> Credential:
        return Credential(
            id="abc12345",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="OpenAI Key",
            data=data if data is not None else {"api_key": "sk-test"},
        )

    def test_generate_id_short(self):
        cid = Credential.generate_id()
        assert len(cid) == 8

    def test_generate_id_unique(self):
        ids = {Credential.generate_id() for _ in range(50)}
        assert len(ids) == 50

    def test_get_secret_default_key(self):
        cred = self._make({"api_key": "sk-abc"})
        assert cred.get_secret() == "sk-abc"

    def test_get_secret_custom_key(self):
        cred = self._make({"token": "tok_xyz"})
        assert cred.get_secret("token") == "tok_xyz"

    def test_get_secret_missing_returns_none(self):
        cred = self._make({})
        assert cred.get_secret("api_key") is None


# ---------------------------------------------------------------------------
# CredentialInfo.__str__
# ---------------------------------------------------------------------------


class TestCredentialInfo:
    def _make(self, enabled: bool = True) -> CredentialInfo:
        return CredentialInfo(
            id="abc12345",
            provider="github",
            profile_id="work",
            credential_type=CredentialType.TOKEN,
            label="GitHub Token",
            enabled=enabled,
            created_at=_TS,
            last_used_at=None,
        )

    def test_str_enabled(self):
        s = str(self._make(enabled=True))
        assert "✓" in s
        assert "github" in s

    def test_str_disabled(self):
        s = str(self._make(enabled=False))
        assert "✗" in s

    def test_str_contains_provider_and_profile(self):
        s = str(self._make())
        assert "github/work" in s


# ---------------------------------------------------------------------------
# TestResult.__str__
# ---------------------------------------------------------------------------


class TestTestResult:
    def test_success_str(self):
        r = TestResult(success=True, message="All good")
        assert "✅" in str(r)
        assert "All good" in str(r)

    def test_failure_str(self):
        r = TestResult(success=False, message="Bad key")
        assert "❌" in str(r)

    def test_details_default_empty(self):
        r = TestResult(success=True, message="ok")
        assert r.details == {}


# ---------------------------------------------------------------------------
# VaultItemKind enum
# ---------------------------------------------------------------------------


class TestVaultItemKind:
    def test_values(self):
        assert VaultItemKind.SECRET == "secret"
        assert VaultItemKind.JSON == "json"
        assert VaultItemKind.TOKEN == "token"
        assert VaultItemKind.NOTE == "note"

    def test_is_str_subclass(self):
        assert isinstance(VaultItemKind.SECRET, str)


# ---------------------------------------------------------------------------
# VaultItem.enabled (reads from metadata)
# ---------------------------------------------------------------------------


class TestVaultItem:
    def _make(self, metadata: dict | None = None) -> VaultItem:
        return VaultItem(
            id="v001",
            kind=VaultItemKind.SECRET,
            label="My Secret",
            provider="openai",
            metadata=metadata or {},
        )

    def test_enabled_default_true(self):
        assert self._make().enabled is True

    def test_enabled_true_explicit(self):
        assert self._make({"enabled": True}).enabled is True

    def test_enabled_false_explicit(self):
        assert self._make({"enabled": False}).enabled is False

    def test_new_id_length(self):
        assert len(VaultItem.new_id()) == 8


# ---------------------------------------------------------------------------
# PROVIDER_PRESETS spot-checks
# ---------------------------------------------------------------------------


class TestProviderPresets:
    def test_openai_is_api_key(self):
        assert PROVIDER_PRESETS["openai"]["credential_type"] == CredentialType.API_KEY

    def test_gmail_is_email(self):
        assert PROVIDER_PRESETS["gmail"]["credential_type"] == CredentialType.EMAIL

    def test_github_is_token(self):
        assert PROVIDER_PRESETS["github"]["credential_type"] == CredentialType.TOKEN

    def test_gmail_has_imap_host(self):
        assert PROVIDER_PRESETS["gmail"]["metadata"]["imap_host"] == "imap.gmail.com"

    def test_anthopic_present(self):
        assert "anthropic" in PROVIDER_PRESETS
