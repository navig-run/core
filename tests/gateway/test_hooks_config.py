"""
tests/gateway/test_hooks_config.py
───────────────────────────────────
Tests for navig.gateway.hooks — HooksConfig fail-fast validation and
HooksHandler request enforcement (Item 10).

These tests import only navig.gateway.hooks (no server, no Telegram),
so they are safe to run in isolation.
"""
from __future__ import annotations

import pytest

from navig.gateway.hooks import (
    HooksConfig,
    HooksConfigError,
    HooksHandler,
    _extract_session_prefix,
    _header,
    load_hooks_config,
)

# ──────────────────────────────────────────────────────────────────────────────
# load_hooks_config — parse & validate
# ──────────────────────────────────────────────────────────────────────────────


class TestLoadHooksConfig:
    def test_minimal_valid(self):
        cfg = load_hooks_config({"auth_token": "s3cr3t"})
        assert cfg.auth_token == "s3cr3t"
        assert cfg.base_path == "/hooks"
        assert cfg.max_body_bytes == 262_144
        assert cfg.idempotency_key_max_len == 256
        assert cfg.session_prefix_allowlist == ()

    def test_all_fields(self):
        cfg = load_hooks_config(
            {
                "auth_token": "tok",
                "base_path": "/webhooks",
                "max_body_bytes": 1024,
                "idempotency_key_max_len": 64,
                "session_prefix_allowlist": ["app", "bot"],
            }
        )
        assert cfg.base_path == "/webhooks"
        assert cfg.max_body_bytes == 1024
        assert cfg.idempotency_key_max_len == 64
        assert cfg.session_prefix_allowlist == ("app", "bot")

    def test_missing_auth_token_raises(self):
        with pytest.raises(HooksConfigError, match="auth_token is required"):
            load_hooks_config({})

    def test_empty_auth_token_raises(self):
        with pytest.raises(HooksConfigError, match="auth_token is required"):
            load_hooks_config({"auth_token": "   "})

    def test_non_dict_raises(self):
        with pytest.raises(HooksConfigError, match="mapping"):
            load_hooks_config("not-a-dict")  # type: ignore[arg-type]

    def test_invalid_allowlist_type_raises(self):
        with pytest.raises(HooksConfigError, match="list of strings"):
            load_hooks_config({"auth_token": "x", "session_prefix_allowlist": "bad"})


# ──────────────────────────────────────────────────────────────────────────────
# HooksConfig.__post_init__ validations
# ──────────────────────────────────────────────────────────────────────────────


class TestHooksConfigValidation:
    def _make(self, **kw):
        defaults = dict(auth_token="tok", base_path="/hooks",
                        max_body_bytes=1024, idempotency_key_max_len=32,
                        session_prefix_allowlist=())
        defaults.update(kw)
        return HooksConfig(**defaults)

    def test_base_path_no_slash_raises(self):
        with pytest.raises(HooksConfigError, match="must start with '/'"):
            self._make(base_path="noslash")

    def test_base_path_root_raises(self):
        with pytest.raises(HooksConfigError, match="must not be '/'"):
            self._make(base_path="/")

    def test_max_body_bytes_zero_raises(self):
        with pytest.raises(HooksConfigError, match="max_body_bytes must be positive"):
            self._make(max_body_bytes=0)

    def test_idempotency_key_max_len_zero_raises(self):
        with pytest.raises(HooksConfigError, match="idempotency_key_max_len must be positive"):
            self._make(idempotency_key_max_len=0)

    def test_valid_config_frozen(self):
        cfg = self._make()
        with pytest.raises((AttributeError, TypeError)):
            cfg.auth_token = "changed"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────────
# HooksHandler — request enforcement
# ──────────────────────────────────────────────────────────────────────────────


def _make_handler(**kw) -> HooksHandler:
    defaults = dict(
        auth_token="secret",
        base_path="/hooks",
        max_body_bytes=1024,
        idempotency_key_max_len=32,
        session_prefix_allowlist=(),
    )
    defaults.update(kw)
    cfg = HooksConfig(**defaults)
    return HooksHandler(cfg)


class TestHooksHandlerAuth:
    def _handler(self):
        return _make_handler()

    def _good_headers(self, token="secret"):
        return {"Authorization": f"Bearer {token}"}

    def test_valid_request_200(self):
        h = self._handler()
        status, _ = h.handle("POST", "/hooks/x", self._good_headers(), b"body")
        assert status == 200

    def test_missing_auth_401(self):
        h = self._handler()
        status, _ = h.handle("POST", "/hooks/x", {}, b"body")
        assert status == 401

    def test_wrong_token_401(self):
        h = self._handler()
        status, _ = h.handle("POST", "/hooks/x", self._good_headers("wrong"), b"body")
        assert status == 401

    def test_malformed_bearer_401(self):
        h = self._handler()
        status, _ = h.handle("POST", "/hooks/x", {"Authorization": "secret"}, b"body")
        assert status == 401

    def test_case_insensitive_header(self):
        h = self._handler()
        status, _ = h.handle("POST", "/hooks/x", {"authorization": "Bearer secret"}, b"body")
        assert status == 200


class TestHooksHandlerBodySize:
    def test_body_exactly_at_limit_ok(self):
        h = _make_handler(max_body_bytes=5)
        status, _ = h.handle("POST", "/hooks/x", {"Authorization": "Bearer secret"}, b"12345")
        assert status == 200

    def test_body_one_byte_over_413(self):
        h = _make_handler(max_body_bytes=5)
        status, _ = h.handle("POST", "/hooks/x", {"Authorization": "Bearer secret"}, b"123456")
        assert status == 413

    def test_body_check_before_auth(self):
        # 413 must win even when auth is missing — body size is checked first
        h = _make_handler(max_body_bytes=1)
        status, _ = h.handle("POST", "/hooks/x", {}, b"toolong")
        assert status == 413


class TestHooksHandlerIdempotencyKey:
    def _headers(self, idem_key: str | None = None) -> dict[str, str]:
        h = {"Authorization": "Bearer secret"}
        if idem_key is not None:
            h["X-Idempotency-Key"] = idem_key
        return h

    def test_no_key_ok(self):
        h = _make_handler()
        status, _ = h.handle("POST", "/hooks/x", self._headers(), b"")
        assert status == 200

    def test_key_at_limit_ok(self):
        h = _make_handler(idempotency_key_max_len=8)
        status, _ = h.handle("POST", "/hooks/x", self._headers("12345678"), b"")
        assert status == 200

    def test_key_over_limit_400(self):
        h = _make_handler(idempotency_key_max_len=4)
        status, body = h.handle("POST", "/hooks/x", self._headers("12345"), b"")
        assert status == 400
        assert b"X-Idempotency-Key" in body


class TestHooksHandlerAllowlist:
    def _headers(self):
        return {"Authorization": "Bearer secret"}

    def test_empty_allowlist_accepts_any_prefix(self):
        h = _make_handler(session_prefix_allowlist=())
        status, _ = h.handle("POST", "/hooks/anythinghere", self._headers(), b"")
        assert status == 200

    def test_known_prefix_200(self):
        h = _make_handler(session_prefix_allowlist=("app", "bot"))
        status, _ = h.handle("POST", "/hooks/app/event", self._headers(), b"")
        assert status == 200

    def test_unknown_prefix_403(self):
        h = _make_handler(session_prefix_allowlist=("app", "bot"))
        status, body = h.handle("POST", "/hooks/unknown/event", self._headers(), b"")
        assert status == 403
        assert b"Forbidden" in body

    def test_path_not_under_base_403(self):
        h = _make_handler(session_prefix_allowlist=("app",))
        status, _ = h.handle("POST", "/other/app/event", self._headers(), b"")
        assert status == 403

    def test_no_suffix_after_base_403(self):
        h = _make_handler(session_prefix_allowlist=("app",))
        status, _ = h.handle("POST", "/hooks", self._headers(), b"")
        assert status == 403


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


class TestHeaderHelper:
    def test_exact_match(self):
        assert _header({"Content-Type": "json"}, "Content-Type") == "json"

    def test_lowercase_name(self):
        assert _header({"Authorization": "Bearer x"}, "authorization") == "Bearer x"

    def test_title_case_lookup(self):
        assert _header({"x-idempotency-key": "abc"}, "X-Idempotency-Key") == "abc"

    def test_missing_returns_none(self):
        assert _header({}, "x-missing") is None


class TestExtractSessionPrefix:
    def test_simple(self):
        assert _extract_session_prefix("/hooks/app/event", "/hooks") == "app"

    def test_trailing_slash_base(self):
        assert _extract_session_prefix("/hooks/bot", "/hooks/") == "bot"

    def test_no_suffix_returns_none(self):
        assert _extract_session_prefix("/hooks", "/hooks") is None

    def test_wrong_base_returns_none(self):
        assert _extract_session_prefix("/other/app", "/hooks") is None

    def test_only_base_slash_returns_none(self):
        assert _extract_session_prefix("/hooks/", "/hooks") is None
