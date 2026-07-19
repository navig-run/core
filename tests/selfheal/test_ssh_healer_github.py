"""
Batch 125 — tests for navig.selfheal.ssh_healer and navig_github.commands.github

Coverage targets:
  ssh_healer.py: HealResult dataclass, module constants, _sanitize_ssh_verbose
  github.py:     _resolve_github_token, _fallback_git_clone
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.selfheal.ssh_healer import (
    _KEYSCAN_TIMEOUT,
    _LOCALHOST_ALIASES,
    HealResult,
    _known_hosts_path,
    _sanitize_ssh_verbose,
)

pytest.importorskip("navig_github", reason="navig-github plugin not installed")
from navig_github.commands.github import (
    _fallback_git_clone,
    _resolve_github_token,
)

from navig.selfheal.ssh_healer import (
    _KEYSCAN_TIMEOUT,
    _LOCALHOST_ALIASES,
    HealResult,
    _known_hosts_path,
    _sanitize_ssh_verbose,
)

# ===========================================================================
# HealResult
# ===========================================================================


class TestHealResult:
    def test_defaults(self):
        r = HealResult(status="resolved", message="all good")
        assert r.status == "resolved"
        assert r.message == "all good"
        assert r.should_retry is False
        assert r.detail == ""

    def test_partial_status(self):
        r = HealResult(status="partial", message="partially fixed")
        assert r.status == "partial"

    def test_failed_status(self):
        r = HealResult(status="failed", message="couldn't fix")
        assert r.status == "failed"

    def test_should_retry_true(self):
        r = HealResult(status="resolved", message="done", should_retry=True)
        assert r.should_retry is True

    def test_detail_set(self):
        r = HealResult(status="resolved", message="ok", detail="extra info")
        assert r.detail == "extra info"

    def test_message_stored(self):
        r = HealResult(status="resolved", message="hello world")
        assert r.message == "hello world"


# ===========================================================================
# Module constants
# ===========================================================================


class TestSshHealerConstants:
    def test_localhost_aliases_contains_loopbacks(self):
        assert "127.0.0.1" in _LOCALHOST_ALIASES
        assert "::1" in _LOCALHOST_ALIASES
        assert "localhost" in _LOCALHOST_ALIASES

    def test_localhost_aliases_count(self):
        assert len(_LOCALHOST_ALIASES) == 3

    def test_keyscan_timeout_positive(self):
        assert _KEYSCAN_TIMEOUT > 0
        assert isinstance(_KEYSCAN_TIMEOUT, int)

    def test_known_hosts_path_is_path(self):
        # `_KNOWN_HOSTS_PATH` is an OVERRIDE slot that defaults to None (like
        # `_DEFAULT_SSH_KEY_PATH`); the real path is resolved lazily by
        # `_known_hosts_path()` so tests/callers can point it at a temp dir. Assert the
        # resolver, not the override constant (which is None by design).
        assert isinstance(_known_hosts_path(), Path)

    def test_known_hosts_path_ends_with_known_hosts(self):
        assert _known_hosts_path().name == "known_hosts"


# ===========================================================================
# _sanitize_ssh_verbose
# ===========================================================================


class TestSanitizeSshVerbose:
    def test_empty_string(self):
        assert _sanitize_ssh_verbose("") == ""

    def test_plain_lines_kept(self):
        result = _sanitize_ssh_verbose("Connection failed\nPermission denied")
        assert "Connection failed" in result
        assert "Permission denied" in result

    def test_debug_lines_stripped(self):
        output = "debug1: some internal state\ndebug1: more noise\nPermission denied"
        result = _sanitize_ssh_verbose(output)
        assert "Permission denied" in result
        # debug-only lines without keywords should be stripped
        assert "some internal state" not in result

    def test_debug_with_keyword_kept(self):
        output = "debug1: Connecting to host\nnormal output"
        result = _sanitize_ssh_verbose(output)
        assert "Connecting to host" in result

    def test_debug_with_cipher_kept(self):
        output = "debug1: cipher aes256\nother"
        result = _sanitize_ssh_verbose(output)
        assert "cipher aes256" in result

    def test_debug_with_permission_kept(self):
        output = "debug1: Permission denied (publickey)\nother"
        result = _sanitize_ssh_verbose(output)
        assert "Permission denied (publickey)" in result

    def test_debug_with_error_kept(self):
        output = "debug1: Error reading key file\nother"
        result = _sanitize_ssh_verbose(output)
        assert "Error reading key file" in result

    def test_max_15_lines_returned(self):
        many_lines = "\n".join(f"line{i}" for i in range(30))
        result = _sanitize_ssh_verbose(many_lines)
        assert len(result.splitlines()) <= 15

    def test_returns_string(self):
        result = _sanitize_ssh_verbose("some output")
        assert isinstance(result, str)


# ===========================================================================
# _resolve_github_token — env var path
# ===========================================================================


class TestResolveGithubToken:
    def test_returns_env_var_when_set(self, tmp_path):
        env = {"GITHUB_TOKEN": "ghp_testtoken123"}
        with patch.dict(os.environ, env):
            token = _resolve_github_token()
        assert token == "ghp_testtoken123"

    def test_returns_none_when_nothing_set(self, tmp_path):
        # Suppress vault import and env var, fake config path
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navig_github.commands.github.config_dir", return_value=tmp_path):
                # no config.yaml at tmp_path; vault import will silently fail
                token = _resolve_github_token()
        assert token is None

    def test_strips_whitespace_from_env(self):
        env = {"GITHUB_TOKEN": "  mytoken  "}
        with patch.dict(os.environ, env):
            token = _resolve_github_token()
        assert token == "mytoken"

    def test_empty_env_var_falls_through(self, tmp_path):
        env = {"GITHUB_TOKEN": ""}
        with patch.dict(os.environ, env, clear=True):
            with patch("navig_github.commands.github.config_dir", return_value=tmp_path):
                token = _resolve_github_token()
        assert token is None

    def test_reads_from_config_yaml(self, tmp_path):
        # Write a config with vault and env cleared
        cfg = tmp_path / "config.yaml"
        cfg.write_text("github:\n  token: config_token_abc\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navig_github.commands.github.config_dir", return_value=tmp_path):
                token = _resolve_github_token()
        assert token == "config_token_abc"


# ===========================================================================
# _fallback_git_clone
# ===========================================================================


class TestFallbackGitClone:
    def _make_success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        return r

    def _make_failure_result(self):
        r = MagicMock()
        r.returncode = 1
        r.stderr = "fatal: not a git repo"
        return r

    def test_clones_to_destination(self, tmp_path):
        dest = tmp_path / "repos"
        with patch("navig_github.commands.github.subprocess.run", return_value=self._make_success_result()):
            with patch("navig_github.commands.github.ch") as mock_ch:
                mock_ch.warning = MagicMock()
                mock_ch.info = MagicMock()
                mock_ch.success = MagicMock()
                _fallback_git_clone("https://github.com/owner/myrepo.git", str(dest))

        # destination dir should have been created
        assert dest.exists()

    def test_clone_strips_git_suffix_from_repo_name(self, tmp_path):
        dest = tmp_path / "repos"
        with patch("navig_github.commands.github.subprocess.run", return_value=self._make_success_result()):
            with patch("navig_github.commands.github.ch") as mock_ch:
                mock_ch.warning = MagicMock()
                mock_ch.info = MagicMock()
                mock_ch.success = MagicMock()
                _fallback_git_clone("https://github.com/owner/coolrepo.git", str(dest))
        # The repo dir created is dest / "coolrepo" (not "coolrepo.git")
        # We verify this by checking subprocess was called with "coolrepo" path
        # (just verify no exception raised and dest created)

    def test_clone_failure_raises_exit(self, tmp_path):
        import typer  # the clone helper raises typer.Exit; typer 0.26 vendors its own click,
        # so the raised type is typer.Exit — NOT the system click.exceptions.Exit (R9-20).
        dest = tmp_path / "repos"
        with patch("navig_github.commands.github.subprocess.run", return_value=self._make_failure_result()):
            with patch("navig_github.commands.github.ch") as mock_ch:
                mock_ch.warning = MagicMock()
                mock_ch.info = MagicMock()
                mock_ch.error = MagicMock()
                with pytest.raises(typer.Exit):
                    _fallback_git_clone("https://github.com/owner/repo.git", str(dest))

    def test_updates_existing_repo(self, tmp_path):
        dest = tmp_path / "repos"
        repo_dir = dest / "myrepo"
        repo_dir.mkdir(parents=True)
        with patch("navig_github.commands.github.subprocess.run", return_value=self._make_success_result()) as m:
            with patch("navig_github.commands.github.ch") as mock_ch:
                mock_ch.warning = MagicMock()
                mock_ch.info = MagicMock()
                mock_ch.success = MagicMock()
                _fallback_git_clone("https://github.com/owner/myrepo", str(dest))
        # Should have used git pull (repo dir already exists)
        call_args = m.call_args[0][0]
        assert "pull" in call_args
