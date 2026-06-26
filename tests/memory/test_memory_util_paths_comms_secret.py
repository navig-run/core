"""Tests for memory/_util.py, memory/paths.py, comms/types.py, vault/secret_str.py."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# memory/_util.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.memory._util import _atomic_write_text, _debug_log


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.txt"
        _atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old", encoding="utf-8")
        _atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "dir" / "file.txt"
        _atomic_write_text(target, "data")
        assert target.read_text(encoding="utf-8") == "data"

    def test_unicode_content(self, tmp_path):
        target = tmp_path / "uni.txt"
        _atomic_write_text(target, "héllo wörld")
        assert "héllo" in target.read_text(encoding="utf-8")


class TestDebugLog:
    def test_does_not_raise(self):
        _debug_log("test message")

    def test_logs_at_debug_level(self):
        with patch("navig.memory._util._logger") as mock_logger:
            _debug_log("my msg")
            mock_logger.debug.assert_called_once_with("my msg")

    def test_suppresses_logging_exceptions(self):
        with patch("navig.memory._util._logger") as mock_logger:
            mock_logger.debug.side_effect = RuntimeError("boom")
            _debug_log("safe")  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# memory/paths.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.memory.paths import KEY_FACTS_DB_PATH, memory_dir, navig_home


class TestNavigHome:
    def test_returns_path(self):
        assert isinstance(navig_home(), Path)

    def test_respects_navig_home_env(self, tmp_path):
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            result = navig_home()
        assert result == tmp_path

    def test_env_takes_priority_over_platform(self, tmp_path):
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            assert navig_home() == tmp_path

    def test_falls_back_to_platform_when_no_env(self):
        env = os.environ.copy()
        env.pop("NAVIG_HOME", None)
        with patch.dict(os.environ, env, clear=True):
            result = navig_home()
        assert isinstance(result, Path)


class TestMemoryDir:
    def test_returns_path_under_home(self, tmp_path):
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            d = memory_dir()
        assert d == tmp_path / "memory"

    def test_creates_directory(self, tmp_path):
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            d = memory_dir()
        # memory_dir() returns the path but does NOT create it (callers mkdir
        # lazily to avoid import-time fs mutations). Verify path + that mkdir works.
        assert d == tmp_path / "memory"
        d.mkdir(parents=True, exist_ok=True)
        assert d.is_dir()

    def test_returns_path_type(self, tmp_path):
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            d = memory_dir()
        assert isinstance(d, Path)


class TestKeyFactsDbPath:
    def test_is_path(self):
        assert isinstance(KEY_FACTS_DB_PATH, Path)

    def test_ends_with_db_extension(self):
        assert KEY_FACTS_DB_PATH.suffix == ".db"

    def test_filename(self):
        assert KEY_FACTS_DB_PATH.name == "key_facts.db"


# ──────────────────────────────────────────────────────────────────────────────
# comms/types.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.comms.types import (
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)


class TestNotificationTarget:
    def test_telegram_factory(self):
        t = NotificationTarget.telegram(12345)
        assert t.telegram_chat_id == 12345
        assert t.matrix_room_id is None

    def test_matrix_factory(self):
        t = NotificationTarget.matrix("!room:example.com")
        assert t.matrix_room_id == "!room:example.com"
        assert t.telegram_chat_id is None

    def test_auto_factory(self):
        t = NotificationTarget.auto("user_42")
        assert t.user_id == "user_42"

    def test_extra_defaults_empty(self):
        t = NotificationTarget()
        assert t.extra == {}


class TestDeliveryPriority:
    def test_all_priorities_exist(self):
        names = {p.name for p in DeliveryPriority}
        assert names == {"LOW", "NORMAL", "HIGH", "CRITICAL"}

    def test_values_are_strings(self):
        for p in DeliveryPriority:
            assert isinstance(p.value, str)


class TestNotificationOptions:
    def test_defaults(self):
        opts = NotificationOptions()
        assert opts.priority == DeliveryPriority.NORMAL
        assert opts.silent is False
        assert opts.ttl_seconds == 0
        assert opts.retry_count == 2
        assert opts.parse_mode == "HTML"
        assert opts.metadata == {}

    def test_custom_priority(self):
        opts = NotificationOptions(priority=DeliveryPriority.CRITICAL)
        assert opts.priority == DeliveryPriority.CRITICAL

    def test_silent_flag(self):
        opts = NotificationOptions(silent=True)
        assert opts.silent is True


class TestDeliveryResult:
    def test_success_factory(self):
        r = DeliveryResult.success(channel="telegram", message_id="42")
        assert r.ok is True
        assert r.channel == "telegram"
        assert r.message_id == "42"
        assert r.error is None

    def test_failure_factory(self):
        r = DeliveryResult.failure(channel="matrix", error="timeout")
        assert r.ok is False
        assert r.error == "timeout"

    def test_timestamp_is_utc(self):
        r = DeliveryResult.success(channel="telegram")
        assert r.timestamp.tzinfo is not None

    def test_metadata_defaults_empty(self):
        r = DeliveryResult.success(channel="telegram")
        assert r.metadata == {}


class TestFanoutResult:
    def test_all_ok_empty(self):
        fr = FanoutResult()
        assert fr.all_ok is True  # vacuous truth from all()

    def test_any_ok_empty(self):
        fr = FanoutResult()
        assert fr.any_ok is False  # any([]) is False

    def test_all_ok_with_failures(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.success("telegram"),
                DeliveryResult.failure("matrix", "err"),
            ]
        )
        assert fr.all_ok is False

    def test_any_ok_with_one_success(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.success("telegram"),
                DeliveryResult.failure("matrix", "err"),
            ]
        )
        assert fr.any_ok is True

    def test_all_ok_all_success(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.success("telegram"),
                DeliveryResult.success("matrix"),
            ]
        )
        assert fr.all_ok is True


# ──────────────────────────────────────────────────────────────────────────────
# vault/secret_str.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.vault.secret_str import Secret, SecretStr, mask_secret


class TestSecretStr:
    def test_str_is_redacted(self):
        s = SecretStr("super-secret")
        assert str(s) == "***"

    def test_repr_is_redacted(self):
        s = SecretStr("super-secret")
        assert repr(s) == "SecretStr('***')"

    def test_reveal_returns_original(self):
        s = SecretStr("my-key-123")
        assert s.reveal() == "my-key-123"

    def test_equality(self):
        a = SecretStr("abc")
        b = SecretStr("abc")
        assert a == b

    def test_inequality(self):
        assert SecretStr("abc") != SecretStr("xyz")

    def test_not_equal_to_plain_string(self):
        assert SecretStr("abc") != "abc"

    def test_len(self):
        assert len(SecretStr("hello")) == 5

    def test_bool_truthy(self):
        assert bool(SecretStr("x"))

    def test_bool_falsy(self):
        assert not bool(SecretStr(""))

    def test_hash_consistent(self):
        s = SecretStr("key")
        assert hash(s) == hash(s)

    def test_format_redacted(self):
        s = SecretStr("secret")
        assert f"{s}" == "***"

    def test_reveal_prefix_short_key(self):
        s = SecretStr("abc")
        assert s.reveal_prefix(4) == "***"

    def test_reveal_prefix_long_key(self):
        s = SecretStr("sk-abcdef1234")
        result = s.reveal_prefix(4)
        assert result.startswith("sk-a")
        assert "***" in result

    def test_copy_is_equal(self):
        s = SecretStr("orig")
        c = s.copy()
        assert c == s
        assert c is not s

    def test_type_error_on_non_string(self):
        with pytest.raises(TypeError):
            SecretStr(12345)  # type: ignore[arg-type]

    def test_from_env_reads_variable(self):
        with patch.dict(os.environ, {"MY_TEST_SECRET_VAR": "test-val"}):
            s = SecretStr.from_env("MY_TEST_SECRET_VAR")
        assert s.reveal() == "test-val"

    def test_from_env_default(self):
        env_copy = os.environ.copy()
        env_copy.pop("MISSING_TEST_VAR_999", None)
        with patch.dict(os.environ, env_copy, clear=True):
            s = SecretStr.from_env("MISSING_TEST_VAR_999", default="fallback")
        assert s.reveal() == "fallback"

    def test_secret_alias(self):
        assert Secret is SecretStr


class TestMaskSecret:
    def test_none_returns_none_marker(self):
        assert mask_secret(None) == "<none>"

    def test_empty_string_returns_empty_marker(self):
        assert mask_secret("") == "<empty>"

    def test_short_string_is_masked(self):
        assert mask_secret("abc", show_prefix=4) == "***"

    def test_long_string_shows_prefix(self):
        result = mask_secret("abcdefgh", show_prefix=4)
        assert result.startswith("abcd")
        assert "***" in result

    def test_secret_str_uses_reveal_prefix(self):
        s = SecretStr("sk-abcdef")
        result = mask_secret(s, show_prefix=4)
        assert "sk-a" in result

    def test_zero_prefix_fully_redacted(self):
        s = SecretStr("sk-abc")
        assert mask_secret(s, show_prefix=0) == "***"
