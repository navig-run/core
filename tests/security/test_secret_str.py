"""Tests for navig.vault.secret_str — SecretStr and mask_secret."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from navig.vault.secret_str import Secret, SecretStr, mask_secret


# ──────────────────────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────────────────────


class TestSecretStrConstruction:
    def test_accepts_string(self):
        s = SecretStr("hello")
        assert s.reveal() == "hello"

    def test_rejects_non_string(self):
        with pytest.raises(TypeError):
            SecretStr(12345)  # type: ignore[arg-type]

    def test_rejects_none(self):
        with pytest.raises(TypeError):
            SecretStr(None)  # type: ignore[arg-type]

    def test_empty_string_accepted(self):
        s = SecretStr("")
        assert s.reveal() == ""

    def test_secret_alias(self):
        assert Secret is SecretStr


# ──────────────────────────────────────────────────────────────
# Redaction
# ──────────────────────────────────────────────────────────────


class TestSecretStrRedaction:
    def test_str_returns_stars(self):
        assert str(SecretStr("my-api-key")) == "***"

    def test_repr_hides_secret(self):
        assert repr(SecretStr("my-api-key")) == "SecretStr('***')"

    def test_format_returns_stars(self):
        s = SecretStr("secret")
        assert f"{s}" == "***"

    def test_not_in_format_string(self):
        s = SecretStr("supersecret")
        formatted = f"Key is {s}"
        assert "supersecret" not in formatted
        assert "***" in formatted


# ──────────────────────────────────────────────────────────────
# reveal / reveal_prefix
# ──────────────────────────────────────────────────────────────


class TestReveal:
    def test_reveal_returns_actual_value(self):
        s = SecretStr("actual-value-123")
        assert s.reveal() == "actual-value-123"

    def test_reveal_prefix_shows_prefix(self):
        s = SecretStr("sk-abcdef123456")
        result = s.reveal_prefix(5)
        assert result.startswith("sk-ab")
        assert "***" in result

    def test_reveal_prefix_short_string_is_redacted(self):
        s = SecretStr("abc")
        assert s.reveal_prefix(10) == "***"

    def test_reveal_prefix_default_count(self):
        s = SecretStr("1234567890")
        result = s.reveal_prefix()
        assert result.startswith("1234")
        assert "***" in result

    def test_reveal_prefix_exact_length(self):
        s = SecretStr("abcd")
        assert s.reveal_prefix(4) == "***"


# ──────────────────────────────────────────────────────────────
# Comparison and hashing
# ──────────────────────────────────────────────────────────────


class TestSecretStrComparison:
    def test_equal_secrets(self):
        assert SecretStr("abc") == SecretStr("abc")

    def test_unequal_secrets(self):
        assert SecretStr("abc") != SecretStr("xyz")

    def test_not_equal_to_plain_string(self):
        assert SecretStr("abc") != "abc"

    def test_hash_equal_for_same_value(self):
        assert hash(SecretStr("key")) == hash(SecretStr("key"))

    def test_hash_differs_for_different_values(self):
        assert hash(SecretStr("key1")) != hash(SecretStr("key2"))

    def test_usable_as_dict_key(self):
        d = {SecretStr("k"): "value"}
        assert d[SecretStr("k")] == "value"


# ──────────────────────────────────────────────────────────────
# len / bool
# ──────────────────────────────────────────────────────────────


class TestSecretStrLenBool:
    def test_len_reflects_actual_length(self):
        assert len(SecretStr("hello")) == 5

    def test_empty_is_falsy(self):
        assert not SecretStr("")

    def test_nonempty_is_truthy(self):
        assert SecretStr("x")


# ──────────────────────────────────────────────────────────────
# copy
# ──────────────────────────────────────────────────────────────


class TestSecretStrCopy:
    def test_copy_reveals_same_value(self):
        s = SecretStr("original")
        copy = s.copy()
        assert copy.reveal() == "original"

    def test_copy_is_new_instance(self):
        s = SecretStr("key")
        copy = s.copy()
        assert s is not copy


# ──────────────────────────────────────────────────────────────
# from_env
# ──────────────────────────────────────────────────────────────


class TestFromEnv:
    def test_reads_set_env_var(self):
        with patch.dict(os.environ, {"TEST_SECRET_KEY": "my-secret-value"}):
            s = SecretStr.from_env("TEST_SECRET_KEY")
        assert s.reveal() == "my-secret-value"

    def test_uses_default_when_unset(self):
        env_key = "NAVIG_TEST_DEFINITELY_NOT_SET_XYZ"
        os.environ.pop(env_key, None)
        s = SecretStr.from_env(env_key, default="fallback")
        assert s.reveal() == "fallback"

    def test_empty_default(self):
        env_key = "NAVIG_TEST_DEFINITELY_NOT_SET_ABC"
        os.environ.pop(env_key, None)
        s = SecretStr.from_env(env_key)
        assert s.reveal() == ""


# ──────────────────────────────────────────────────────────────
# mask_secret
# ──────────────────────────────────────────────────────────────


class TestMaskSecret:
    def test_none_returns_none_label(self):
        assert mask_secret(None) == "<none>"

    def test_secretstr_shows_prefix(self):
        result = mask_secret(SecretStr("sk-abcdef"), show_prefix=4)
        assert result.startswith("sk-a")
        assert "***" in result

    def test_secretstr_no_prefix(self):
        result = mask_secret(SecretStr("sk-abcdef"), show_prefix=0)
        assert result == "***"

    def test_plain_string_masked(self):
        result = mask_secret("plain-secret-value", show_prefix=4)
        assert result.startswith("plai")
        assert "***" in result

    def test_empty_string_returns_empty_label(self):
        assert mask_secret("") == "<empty>"

    def test_short_string_fully_masked(self):
        result = mask_secret("ab", show_prefix=4)
        assert result == "***"
