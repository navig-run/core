"""
Batch 33 — navig/vault/secret_str.py + navig/vault/types.py

Covers:
  SecretStr: __str__, __repr__, __eq__, __ne__, __hash__, __len__, __bool__,
             __format__, reveal(), reveal_prefix(), copy(), from_env(), TypeError init
  mask_secret(): None, SecretStr, plain str, empty, show_prefix=0
  CredentialType: enum values
  Credential: defaults, generate_id(), get_secret()
  CredentialInfo: __str__ enabled/disabled
  TestResult: __str__ success/failure, defaults
  PROVIDER_PRESETS: spot-check known providers
  VaultItemKind: enum values
  VaultItem: defaults, new_id(), enabled property
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from navig.vault.secret_str import SecretStr, mask_secret
from navig.vault.types import (
    Credential,
    CredentialInfo,
    CredentialType,
    PROVIDER_PRESETS,
    TestResult,
    VaultItem,
    VaultItemKind,
)


# ---------------------------------------------------------------------------
# SecretStr
# ---------------------------------------------------------------------------

class TestSecretStrBasics:
    def test_str_returns_stars(self):
        assert str(SecretStr("hello")) == "***"

    def test_repr_returns_safe(self):
        assert repr(SecretStr("secret")) == "SecretStr('***')"

    def test_reveal_returns_actual(self):
        s = SecretStr("mykey")
        assert s.reveal() == "mykey"

    def test_len_reflects_real_len(self):
        s = SecretStr("abcde")
        assert len(s) == 5

    def test_bool_true_for_nonempty(self):
        assert bool(SecretStr("x")) is True

    def test_bool_false_for_empty(self):
        assert bool(SecretStr("")) is False

    def test_format_returns_stars(self):
        s = SecretStr("hidden")
        assert f"{s}" == "***"
        assert format(s, "") == "***"

    def test_eq_same_secret(self):
        assert SecretStr("abc") == SecretStr("abc")

    def test_eq_different_secret(self):
        assert SecretStr("abc") != SecretStr("xyz")

    def test_ne_different_secret(self):
        assert SecretStr("a") != SecretStr("b")

    def test_eq_non_secret_false(self):
        assert (SecretStr("abc") == "abc") is False

    def test_hash_consistent(self):
        s = SecretStr("key")
        assert hash(s) == hash(s)

    def test_hash_equal_secrets_equal_hash(self):
        assert hash(SecretStr("k")) == hash(SecretStr("k"))

    def test_type_error_on_non_string(self):
        with pytest.raises(TypeError):
            SecretStr(12345)  # type: ignore


class TestSecretStrRevealPrefix:
    def test_reveal_prefix_normal(self):
        s = SecretStr("sk-abcdefghij")
        result = s.reveal_prefix(4)
        assert result.startswith("sk-a")
        assert result.endswith("...***")

    def test_reveal_prefix_short_secret(self):
        # Value shorter than count → fully masked
        s = SecretStr("abc")
        assert s.reveal_prefix(10) == "***"

    def test_reveal_prefix_exact_boundary(self):
        s = SecretStr("abcd")
        # len == count → fully masked
        assert s.reveal_prefix(4) == "***"


class TestSecretStrCopyAndFromEnv:
    def test_copy_is_equal(self):
        s = SecretStr("original")
        c = s.copy()
        assert c == s

    def test_copy_is_independent(self):
        s = SecretStr("original")
        c = s.copy()
        assert c is not s

    def test_from_env_reads_variable(self, monkeypatch):
        monkeypatch.setenv("TEST_NAVIG_SECRET", "env_value_xyz")
        s = SecretStr.from_env("TEST_NAVIG_SECRET")
        assert s.reveal() == "env_value_xyz"

    def test_from_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        s = SecretStr.from_env("NONEXISTENT_VAR_XYZ", default="fallback")
        assert s.reveal() == "fallback"

    def test_from_env_empty_if_unset_no_default(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        s = SecretStr.from_env("NONEXISTENT_VAR_XYZ")
        assert s.reveal() == ""


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------

class TestMaskSecret:
    def test_none_returns_none_label(self):
        assert mask_secret(None) == "<none>"

    def test_secret_str_with_prefix(self):
        s = SecretStr("sk-abcdefghij")
        result = mask_secret(s, show_prefix=4)
        assert result.startswith("sk-a")
        assert "***" in result

    def test_secret_str_no_prefix(self):
        s = SecretStr("anyvalue")
        assert mask_secret(s, show_prefix=0) == "***"

    def test_empty_string_returns_empty_label(self):
        assert mask_secret("") == "<empty>"

    def test_plain_str_with_prefix(self):
        result = mask_secret("abcdefgh", show_prefix=3)
        assert result.startswith("abc")
        assert "***" in result

    def test_plain_short_str_masked(self):
        # Short string that doesn't exceed prefix → "***"
        result = mask_secret("ab", show_prefix=4)
        assert result == "***"


# ---------------------------------------------------------------------------
# CredentialType
# ---------------------------------------------------------------------------

class TestCredentialType:
    def test_api_key_value(self):
        assert CredentialType.API_KEY == "api_key"

    def test_email_value(self):
        assert CredentialType.EMAIL == "email"

    def test_ssh_key_value(self):
        assert CredentialType.SSH_KEY == "ssh_key"

    def test_generic_value(self):
        assert CredentialType.GENERIC == "generic"


# ---------------------------------------------------------------------------
# Credential dataclass
# ---------------------------------------------------------------------------

class TestCredential:
    def _make(self, **kwargs):
        defaults = dict(
            id="cred0001",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="My OpenAI key",
            data={"api_key": "sk-abc123"},
        )
        defaults.update(kwargs)
        return Credential(**defaults)

    def test_defaults_enabled_true(self):
        c = self._make()
        assert c.enabled is True

    def test_defaults_metadata_empty(self):
        c = self._make()
        assert c.metadata == {}

    def test_get_secret_default_key(self):
        c = self._make(data={"api_key": "sk-abc123"})
        assert c.get_secret() == "sk-abc123"

    def test_get_secret_custom_key(self):
        c = self._make(data={"access_token": "tok_xyz"})
        assert c.get_secret("access_token") == "tok_xyz"

    def test_get_secret_missing_returns_none(self):
        c = self._make(data={})
        assert c.get_secret("api_key") is None

    def test_generate_id_is_8_chars(self):
        id_ = Credential.generate_id()
        assert len(id_) == 8

    def test_generate_id_is_unique(self):
        ids = {Credential.generate_id() for _ in range(20)}
        assert len(ids) == 20

    def test_created_at_is_datetime(self):
        c = self._make()
        assert isinstance(c.created_at, datetime)


# ---------------------------------------------------------------------------
# CredentialInfo
# ---------------------------------------------------------------------------

class TestCredentialInfo:
    def _make(self, enabled=True):
        return CredentialInfo(
            id="ci001",
            provider="github",
            profile_id="work",
            credential_type=CredentialType.TOKEN,
            label="Work GitHub",
            enabled=enabled,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )

    def test_str_enabled(self):
        info = self._make(enabled=True)
        s = str(info)
        assert "✓" in s
        assert "github" in s

    def test_str_disabled(self):
        info = self._make(enabled=False)
        s = str(info)
        assert "✗" in s


# ---------------------------------------------------------------------------
# TestResult
# ---------------------------------------------------------------------------

class TestTestResult:
    def test_str_success(self):
        r = TestResult(success=True, message="OK")
        s = str(r)
        assert "✅" in s
        assert "OK" in s

    def test_str_failure(self):
        r = TestResult(success=False, message="Unauthorized")
        s = str(r)
        assert "❌" in s
        assert "Unauthorized" in s

    def test_details_default_empty(self):
        r = TestResult(success=True, message="fine")
        assert r.details == {}

    def test_tested_at_is_datetime(self):
        r = TestResult(success=True, message="ok")
        assert isinstance(r.tested_at, datetime)


# ---------------------------------------------------------------------------
# PROVIDER_PRESETS
# ---------------------------------------------------------------------------

class TestProviderPresets:
    def test_openai_present(self):
        assert "openai" in PROVIDER_PRESETS

    def test_openai_credential_type(self):
        assert PROVIDER_PRESETS["openai"]["credential_type"] == CredentialType.API_KEY

    def test_gmail_credential_type(self):
        assert PROVIDER_PRESETS["gmail"]["credential_type"] == CredentialType.EMAIL

    def test_gmail_has_imap_host(self):
        assert "imap_host" in PROVIDER_PRESETS["gmail"]["metadata"]

    def test_github_credential_type(self):
        assert PROVIDER_PRESETS["github"]["credential_type"] == CredentialType.TOKEN


# ---------------------------------------------------------------------------
# VaultItemKind
# ---------------------------------------------------------------------------

class TestVaultItemKind:
    def test_secret_value(self):
        assert VaultItemKind.SECRET == "secret"

    def test_token_value(self):
        assert VaultItemKind.TOKEN == "token"

    def test_password_value(self):
        assert VaultItemKind.PASSWORD == "password"


# ---------------------------------------------------------------------------
# VaultItem
# ---------------------------------------------------------------------------

class TestVaultItem:
    def _make(self, **kwargs):
        defaults = dict(
            id="vi001234",
            kind=VaultItemKind.SECRET,
            label="My API key",
            provider="openai",
        )
        defaults.update(kwargs)
        return VaultItem(**defaults)

    def test_default_payload_empty_bytes(self):
        vi = self._make()
        assert vi.payload == b""

    def test_default_enabled_true(self):
        vi = self._make()
        assert vi.enabled is True

    def test_enabled_false_from_metadata(self):
        vi = self._make(metadata={"enabled": False})
        assert vi.enabled is False

    def test_new_id_is_8_chars(self):
        assert len(VaultItem.new_id()) == 8

    def test_new_id_unique(self):
        ids = {VaultItem.new_id() for _ in range(20)}
        assert len(ids) == 20

    def test_created_at_is_datetime(self):
        vi = self._make()
        assert isinstance(vi.created_at, datetime)

    def test_version_defaults_1(self):
        vi = self._make()
        assert vi.version == 1
