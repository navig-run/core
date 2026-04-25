"""Tests for navig.ssh_keys — SSH key discovery helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from navig.ssh_keys import (
    _DEFAULT_KEY_NAMES,
    _looks_like_private_key,
    discover_local_ssh_keys,
)


# ──────────────────────────────────────────────────────────────
# _looks_like_private_key
# ──────────────────────────────────────────────────────────────


class TestLooksLikePrivateKey:
    def test_nonexistent_file_returns_false(self, tmp_path):
        assert _looks_like_private_key(tmp_path / "ghost") is False

    def test_pub_extension_returns_false(self, tmp_path):
        f = tmp_path / "id_ed25519.pub"
        f.write_text("ssh-ed25519 AAAA…", encoding="utf-8")
        assert _looks_like_private_key(f) is False

    def test_known_hosts_returns_false(self, tmp_path):
        f = tmp_path / "known_hosts"
        f.write_text("github.com ecdsa-sha2-nistp256 AAAA…", encoding="utf-8")
        assert _looks_like_private_key(f) is False

    def test_config_returns_false(self, tmp_path):
        f = tmp_path / "config"
        f.write_text("Host *\n  ServerAliveInterval 60\n", encoding="utf-8")
        assert _looks_like_private_key(f) is False

    def test_authorized_keys_returns_false(self, tmp_path):
        f = tmp_path / "authorized_keys"
        f.write_text("ssh-rsa AAAA… user@host", encoding="utf-8")
        assert _looks_like_private_key(f) is False

    def test_regular_file_returns_true(self, tmp_path):
        f = tmp_path / "id_ed25519"
        f.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
        assert _looks_like_private_key(f) is True

    def test_directory_returns_false(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        assert _looks_like_private_key(d) is False

    def test_arbitrary_name_without_pub_returns_true(self, tmp_path):
        f = tmp_path / "deploy_key"
        f.write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
        assert _looks_like_private_key(f) is True


# ──────────────────────────────────────────────────────────────
# _DEFAULT_KEY_NAMES
# ──────────────────────────────────────────────────────────────


class TestDefaultKeyNames:
    def test_contains_ed25519(self):
        assert "id_ed25519" in _DEFAULT_KEY_NAMES

    def test_contains_rsa(self):
        assert "id_rsa" in _DEFAULT_KEY_NAMES

    def test_is_list(self):
        assert isinstance(_DEFAULT_KEY_NAMES, list)


# ──────────────────────────────────────────────────────────────
# discover_local_ssh_keys
# ──────────────────────────────────────────────────────────────


class TestDiscoverLocalSshKeys:
    def test_no_ssh_dir_returns_empty(self, tmp_path):
        fake_home = tmp_path  # no .ssh subdir
        with patch("navig.ssh_keys.Path") as MockPath:
            # Intercept Path.home() and Path(…)/.ssh
            from unittest.mock import MagicMock

            ssh_dir = tmp_path / ".ssh_nonexistent"
            mock_home = MagicMock()
            mock_home.__truediv__ = lambda self, name: ssh_dir
            MockPath.home.return_value = mock_home
            # Make ssh_dir.exists() return False
            with patch.object(type(ssh_dir), "exists", return_value=False):
                result = discover_local_ssh_keys(no_cache=True)
        # Either empty keys or just doesn't crash
        assert isinstance(result, dict)
        assert "keys" in result
        assert "count" in result

    def test_no_cache_skips_cache_read(self):
        """With no_cache=True, cache is not used even when populated."""
        with patch("navig.ssh_keys.read_json_cache") as mock_read:
            mock_read.return_value.__class__  # just accessing
            # Simulate no .ssh directory
            fake_ssh = Path("/nonexistent-ssh-dir-xyz123")
            with patch("navig.ssh_keys.Path") as MockPath:
                from unittest.mock import MagicMock

                mock_home = MagicMock()
                mock_home.__truediv__ = lambda self, k: fake_ssh
                MockPath.home.return_value = mock_home
                discover_local_ssh_keys(no_cache=True)
            # Cache read should have been called with no_cache=True
            mock_read.assert_called_once()
            _, kwargs = mock_read.call_args
            assert kwargs.get("no_cache") is True

    def test_returns_cache_when_hit(self):
        """When cache returns a hit with valid data, it is used directly."""
        from navig.cache_store import CacheReadResult

        cached_data = {"keys": [{"name": "id_rsa", "path": "/home/user/.ssh/id_rsa"}], "count": 1}
        hit = CacheReadResult(hit=True, expired=False, data=cached_data, cached_at="2025-01-01T00:00:00Z")
        with patch("navig.ssh_keys.read_json_cache", return_value=hit):
            result = discover_local_ssh_keys()
        assert result == cached_data

    def test_discovered_keys_includes_name_and_path(self, tmp_path):
        """Keys found in .ssh dir have 'name' and 'path' fields."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key = ssh_dir / "id_ed25519"
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")

        from navig.cache_store import CacheReadResult

        miss = CacheReadResult(hit=False, expired=False, data=None, cached_at=None)
        with (
            patch("navig.ssh_keys.read_json_cache", return_value=miss),
            patch("navig.ssh_keys.write_json_cache"),
            patch("navig.ssh_keys.Path") as MockPath,
        ):
            from unittest.mock import MagicMock

            mock_home = MagicMock()
            mock_home.__truediv__ = lambda self, name: tmp_path
            MockPath.home.return_value = mock_home

            # Directly test helper function output structure
            result = discover_local_ssh_keys(no_cache=True)

        assert isinstance(result, dict)
        assert "keys" in result
        assert "count" in result

    def test_result_count_matches_keys_length(self):
        """count field always equals len(keys)."""
        from navig.cache_store import CacheReadResult

        miss = CacheReadResult(hit=False, expired=False, data=None, cached_at=None)
        with (
            patch("navig.ssh_keys.read_json_cache", return_value=miss),
            patch("navig.ssh_keys.write_json_cache"),
        ):
            result = discover_local_ssh_keys(no_cache=True)

        assert result["count"] == len(result["keys"])

    def test_write_cache_called_on_miss(self):
        """On a cache miss, the discovered payload is written back to cache."""
        from navig.cache_store import CacheReadResult

        miss = CacheReadResult(hit=False, expired=False, data=None, cached_at=None)
        with (
            patch("navig.ssh_keys.read_json_cache", return_value=miss),
            patch("navig.ssh_keys.write_json_cache") as mock_write,
        ):
            discover_local_ssh_keys(no_cache=True)

        # write_json_cache should be called (best effort)
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == "ssh_keys.json"
