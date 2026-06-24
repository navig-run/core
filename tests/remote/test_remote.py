"""Hermetic unit tests for navig.remote — pure helper functions."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# _resolve_ssh_timeout_seconds
# ---------------------------------------------------------------------------


class TestResolveSshTimeoutSeconds:
    def test_returns_default_when_env_not_set(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_SSH_TIMEOUT", None)
            result = _resolve_ssh_timeout_seconds(default=30)

        assert result == 30

    def test_reads_valid_value_from_env(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {"NAVIG_SSH_TIMEOUT": "60"}):
            result = _resolve_ssh_timeout_seconds()

        assert result == 60

    def test_invalid_env_value_falls_back_to_default(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {"NAVIG_SSH_TIMEOUT": "not-a-number"}):
            result = _resolve_ssh_timeout_seconds(default=45)

        assert result == 45

    def test_zero_falls_back_to_default(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {"NAVIG_SSH_TIMEOUT": "0"}):
            result = _resolve_ssh_timeout_seconds(default=30)

        assert result == 30

    def test_negative_value_falls_back_to_default(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {"NAVIG_SSH_TIMEOUT": "-5"}):
            result = _resolve_ssh_timeout_seconds(default=30)

        assert result == 30

    def test_whitespace_stripped(self):
        from navig.remote import _resolve_ssh_timeout_seconds

        with patch.dict(os.environ, {"NAVIG_SSH_TIMEOUT": "  120  "}):
            result = _resolve_ssh_timeout_seconds()

        assert result == 120


# ---------------------------------------------------------------------------
# _require_server_identity
# ---------------------------------------------------------------------------


class TestRequireServerIdentity:
    def test_returns_user_and_host(self):
        from navig.remote import _require_server_identity

        user, host = _require_server_identity({"user": "admin", "host": "192.168.1.1"})
        assert user == "admin"
        assert host == "192.168.1.1"

    def test_raises_when_user_missing(self):
        from navig.remote import _require_server_identity

        with pytest.raises(ValueError, match="user"):
            _require_server_identity({"host": "192.168.1.1"})

    def test_raises_when_host_missing(self):
        from navig.remote import _require_server_identity

        with pytest.raises(ValueError, match="host"):
            _require_server_identity({"user": "admin"})

    def test_raises_when_user_empty_string(self):
        from navig.remote import _require_server_identity

        with pytest.raises(ValueError):
            _require_server_identity({"user": "  ", "host": "10.0.0.1"})

    def test_raises_when_both_missing(self):
        from navig.remote import _require_server_identity

        with pytest.raises(ValueError):
            _require_server_identity({})


# ---------------------------------------------------------------------------
# is_local_host
# ---------------------------------------------------------------------------


class TestIsLocalHost:
    def test_is_local_flag_true(self):
        from navig.remote import is_local_host

        assert is_local_host({"is_local": True}) is True

    def test_type_local_string(self):
        from navig.remote import is_local_host

        assert is_local_host({"type": "local"}) is True

    def test_type_local_case_insensitive(self):
        from navig.remote import is_local_host

        assert is_local_host({"type": "LOCAL"}) is True

    def test_host_localhost(self):
        from navig.remote import is_local_host

        assert is_local_host({"host": "localhost"}) is True

    def test_host_loopback_ipv4(self):
        from navig.remote import is_local_host

        assert is_local_host({"host": "127.0.0.1"}) is True

    def test_host_loopback_ipv6(self):
        from navig.remote import is_local_host

        assert is_local_host({"host": "::1"}) is True

    def test_remote_host_returns_false(self):
        from navig.remote import is_local_host

        assert is_local_host({"host": "192.168.1.100", "user": "admin"}) is False

    def test_empty_config_returns_false(self):
        from navig.remote import is_local_host

        assert is_local_host({}) is False
