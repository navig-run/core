"""Tests for navig.identity.seed — generate_seed, _get_username."""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import pytest

from navig.identity.seed import _get_username, generate_seed


class TestGenerateSeed:
    def test_returns_64_char_hex_string(self):
        seed = generate_seed()
        assert len(seed) == 64
        assert all(c in "0123456789abcdef" for c in seed)

    def test_deterministic_on_same_machine(self):
        seed1 = generate_seed()
        seed2 = generate_seed()
        assert seed1 == seed2

    def test_returns_hex_when_all_parts_fail(self):
        """When all stable attributes fail, falls back to uuid4."""
        with (
            patch("navig.identity.seed.uuid.getnode", side_effect=Exception("fail")),
            patch("navig.identity.seed.platform.node", side_effect=Exception("fail")),
            patch("navig.identity.seed._get_username", side_effect=Exception("fail")),
            patch("navig.identity.seed.platform.system", side_effect=Exception("fail")),
        ):
            seed = generate_seed()
        assert len(seed) == 32  # uuid4.hex is 32 chars

    def test_seed_is_sha256_of_parts(self):
        """Verify the SHA-256 construction when we control all parts."""
        with (
            patch("navig.identity.seed.uuid.getnode", return_value=12345),
            patch("navig.identity.seed.platform.node", return_value="myhost"),
            patch("navig.identity.seed._get_username", return_value="alice"),
            patch("navig.identity.seed.platform.system", return_value="Linux"),
        ):
            seed = generate_seed()
        raw = "12345myhostaliceLinux"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert seed == expected


class TestGetUsername:
    def test_returns_env_var_when_login_fails(self, monkeypatch):
        monkeypatch.setenv("USERNAME", "testuser")
        with patch("navig.identity.seed.os.getlogin", side_effect=OSError):
            result = _get_username()
        assert result == "testuser"

    def test_returns_user_env_var(self, monkeypatch):
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.setenv("USER", "linuxuser")
        with patch("navig.identity.seed.os.getlogin", side_effect=OSError):
            result = _get_username()
        assert result == "linuxuser"

    def test_fallback_to_operator(self, monkeypatch):
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("LOGNAME", raising=False)
        with patch("navig.identity.seed.os.getlogin", side_effect=OSError):
            result = _get_username()
        assert result == "operator"

    def test_returns_login_when_available(self):
        with patch("navig.identity.seed.os.getlogin", return_value="realuser"):
            result = _get_username()
        assert result == "realuser"
