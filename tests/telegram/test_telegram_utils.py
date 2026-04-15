"""Tests for navig.gateway.channels.telegram_utils.

Covers:
  - escape_mdv2 / _mdv2_escape alias
  - sanitize_user_error
  - classify_tg_error / TgErrorKind
  - jittered_backoff
  - Verify the 4 importer modules expose _mdv2_escape via the new import
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_utils import (
    TgErrorKind,
    _mdv2_escape,
    classify_tg_error,
    escape_mdv2,
    jittered_backoff,
    sanitize_user_error,
)


# ── escape_mdv2 ───────────────────────────────────────────────────────────────


class TestEscapeMdv2:
    def test_escapes_special_chars(self):
        assert escape_mdv2("hello_world") == r"hello\_world"
        assert escape_mdv2("2*3=6") == r"2\*3\=6"

    def test_escapes_brackets_and_parens(self):
        assert escape_mdv2("[link](url)") == r"\[link\]\(url\)"

    def test_escapes_backtick(self):
        assert escape_mdv2("`code`") == r"\`code\`"

    def test_plain_alphanum_unchanged(self):
        assert escape_mdv2("hello123") == "hello123"

    def test_empty_string(self):
        assert escape_mdv2("") == ""

    def test_non_string_auto_cast(self):
        assert escape_mdv2(42) == "42"

    def test_exclamation_mark(self):
        result = escape_mdv2("Hello!")
        assert result == r"Hello\!"

    def test_alias_matches_canonical(self):
        assert _mdv2_escape("test!") == escape_mdv2("test!")


# ── sanitize_user_error ───────────────────────────────────────────────────────


class TestSanitizeUserError:
    def test_redacts_query_param_token(self):
        url = "https://api.example.com/v1/data?access_token=supersecret123"
        result = sanitize_user_error(url)
        assert "supersecret123" not in result
        assert "access_token=***" in result

    def test_redacts_api_key_in_qs(self):
        url = "https://example.com/api?api_key=abc123xyz"
        result = sanitize_user_error(url)
        assert "abc123xyz" not in result
        assert "***" in result

    def test_redacts_generic_key_assignment(self):
        text = "Error: api_key=my_secret_value was rejected"
        result = sanitize_user_error(text)
        assert "my_secret_value" not in result

    def test_plain_error_unchanged(self):
        text = "Connection refused"
        result = sanitize_user_error(text)
        assert result == text

    def test_exception_object(self):
        exc = ValueError("bad value: access_token=tok123")
        result = sanitize_user_error(exc)
        assert "tok123" not in result

    def test_never_raises(self):
        # Even if input is bizarre, must not raise
        result = sanitize_user_error(None)
        assert isinstance(result, str)

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
        result = sanitize_user_error(text)
        assert "eyJ" not in result


# ── classify_tg_error ─────────────────────────────────────────────────────────


class TestClassifyTgError:
    def test_429_is_rate_limit(self):
        exc = Exception("429")
        assert classify_tg_error(exc, status_code=429) == TgErrorKind.RATE_LIMIT

    def test_401_is_auth(self):
        exc = Exception("401")
        assert classify_tg_error(exc, status_code=401) == TgErrorKind.AUTH

    def test_404_is_not_found(self):
        exc = Exception("404")
        assert classify_tg_error(exc, status_code=404) == TgErrorKind.NOT_FOUND

    def test_400_is_payload(self):
        exc = Exception("400 bad request")
        assert classify_tg_error(exc, status_code=400) == TgErrorKind.PAYLOAD

    def test_500_is_transient(self):
        exc = Exception("500 internal server error")
        assert classify_tg_error(exc, status_code=500) == TgErrorKind.TRANSIENT

    def test_too_many_requests_msg(self):
        exc = Exception("Too Many Requests: retry after 30")
        assert classify_tg_error(exc) == TgErrorKind.RATE_LIMIT

    def test_chat_not_found_msg(self):
        exc = Exception("Chat not found")
        assert classify_tg_error(exc) == TgErrorKind.NOT_FOUND

    def test_message_too_long_msg(self):
        exc = Exception("Message is too long")
        assert classify_tg_error(exc) == TgErrorKind.PAYLOAD

    def test_timeout_error_type(self):
        class FakeTimeoutError(Exception):
            pass
        FakeTimeoutError.__name__ = "TimeoutError"
        exc = FakeTimeoutError("timed out")
        assert classify_tg_error(exc) == TgErrorKind.TIMEOUT

    def test_unknown_fallback(self):
        exc = Exception("something completely unexpected")
        assert classify_tg_error(exc) == TgErrorKind.UNKNOWN


# ── jittered_backoff ──────────────────────────────────────────────────────────


class TestJitteredBackoff:
    def test_attempt_1_within_range(self):
        delay = jittered_backoff(1, base_delay=1.0, max_delay=60.0)
        # base=1, jitter≤0.5 → total in [1.0, 1.5]
        assert 1.0 <= delay <= 1.5 + 0.01  # small float tolerance

    def test_increases_with_attempt(self):
        d1 = jittered_backoff(1, base_delay=2.0, max_delay=120.0, jitter_ratio=0.0)
        d2 = jittered_backoff(2, base_delay=2.0, max_delay=120.0, jitter_ratio=0.0)
        d3 = jittered_backoff(3, base_delay=2.0, max_delay=120.0, jitter_ratio=0.0)
        assert d1 < d2 < d3

    def test_max_delay_capped(self):
        delay = jittered_backoff(100, base_delay=1.0, max_delay=5.0, jitter_ratio=0.0)
        assert delay <= 5.0 + 0.01

    def test_returns_float(self):
        assert isinstance(jittered_backoff(1), float)

    def test_zero_jitter_deterministic_base(self):
        d1 = jittered_backoff(1, base_delay=4.0, max_delay=60.0, jitter_ratio=0.0)
        d2 = jittered_backoff(1, base_delay=4.0, max_delay=60.0, jitter_ratio=0.0)
        assert d1 == d2 == 4.0


# ── Importer modules expose _mdv2_escape via new import ───────────────────────


class TestImporterModulesExposedAlias:
    """Ensure the 4 modules that previously defined their own _mdv2_escape now
    re-export it via the telegram_utils import, and that the behaviour is
    identical."""

    def test_keyboards_exposes_alias(self):
        from navig.gateway.channels.telegram_keyboards import _mdv2_escape as kbe

        assert kbe("hello!") == escape_mdv2("hello!")

    def test_refiner_exposes_alias(self):
        from navig.gateway.channels.telegram_refiner import _mdv2_escape as rfe

        assert rfe("hello!") == escape_mdv2("hello!")

    def test_voice_exposes_alias(self):
        from navig.gateway.channels.telegram_voice import _mdv2_escape as vce

        assert vce("hello!") == escape_mdv2("hello!")

    def test_mesh_exposes_alias(self):
        from navig.gateway.channels.telegram_mesh import _mdv2_escape as mhe

        assert mhe("hello!") == escape_mdv2("hello!")
