"""
Tests for navig.selfheal.heal_pr_submitter — store/list patches, token, body builder.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.selfheal.heal_pr_submitter import HealPRSubmitter


def _submitter() -> HealPRSubmitter:
    return HealPRSubmitter()


# ---------------------------------------------------------------------------
# _get_token
# ---------------------------------------------------------------------------


def test_get_token_raises_when_not_set():
    s = _submitter()
    s._token = None
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="NAVIG_GITHUB_TOKEN"):
            s._get_token()


def test_get_token_returns_env_var():
    s = _submitter()
    s._token = None
    with patch.dict("os.environ", {"NAVIG_GITHUB_TOKEN": "tok_abc123"}):
        token = s._get_token()
    assert token == "tok_abc123"


def test_get_token_caches_token():
    s = _submitter()
    s._token = None
    with patch.dict("os.environ", {"NAVIG_GITHUB_TOKEN": "tok_cached"}):
        s._get_token()
    assert s._token == "tok_cached"


# ---------------------------------------------------------------------------
# store_pending_patch
# ---------------------------------------------------------------------------


def test_store_pending_patch_creates_file(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(
            failure_class="ImportError",
            original_cmd="navig db list",
            stderr="ModuleNotFoundError",
            exit_code=1,
            patch_text="--- a\n+++ b\n",
        )
    assert isinstance(result, Path)
    assert result.exists()


def test_store_pending_patch_writes_json_fields(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(
            failure_class="ImportError",
            original_cmd="navig db list",
            stderr="No module",
            exit_code=1,
            patch_text="diff content",
        )
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["failure_class"] == "ImportError"
    assert data["original_cmd"] == "navig db list"
    assert data["exit_code"] == 1
    assert data["submitted"] is False


def test_store_pending_patch_includes_host(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(
            failure_class="RuntimeError",
            original_cmd="navig run ls",
            stderr="err",
            exit_code=2,
            patch_text="patch",
            host="prod-01",
        )
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["host"] == "prod-01"


def test_store_pending_patch_creates_directory(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "nonexistent" / "nested" / "patches"
    assert not patch_dir.exists()
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        s.store_pending_patch("E", "cmd", "stderr", 1, "patch")
    assert patch_dir.exists()


def test_store_pending_patch_truncates_long_stderr(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    long_stderr = "E" * 5000
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch("E", "cmd", long_stderr, 1, "patch")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert len(data["stderr"]) <= 2000


# ---------------------------------------------------------------------------
# list_pending_patches
# ---------------------------------------------------------------------------


def test_list_pending_patches_returns_empty_when_dir_missing(tmp_path):
    s = _submitter()
    missing = tmp_path / "no_patches_here"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", missing):
        result = s.list_pending_patches()
    assert result == []


def test_list_pending_patches_returns_unsubmitted(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    data = {"submitted": False, "failure_class": "ImportError"}
    (patch_dir / "20240101_000000_patch.patch.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()
    assert len(result) == 1
    assert isinstance(result[0], Path)


def test_list_pending_patches_skips_submitted(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "a.patch.json").write_text(
        json.dumps({"submitted": True}), encoding="utf-8"
    )
    (patch_dir / "b.patch.json").write_text(
        json.dumps({"submitted": False}), encoding="utf-8"
    )
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()
    assert len(result) == 1


def test_list_pending_patches_skips_corrupt_json(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "corrupt.patch.json").write_text("not valid json{{{", encoding="utf-8")
    (patch_dir / "valid.patch.json").write_text(
        json.dumps({"submitted": False}), encoding="utf-8"
    )
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_pr_body (static method)
# ---------------------------------------------------------------------------


def _body(**kwargs) -> str:
    defaults = dict(
        failure_class="ImportError",
        original_cmd="db list",
        stderr="No module",
        exit_code=1,
        host=None,
        patch_text="--- a\n+++ b\n",
        ts="20240101120000",
    )
    defaults.update(kwargs)
    return HealPRSubmitter._build_pr_body(**defaults)


def test_build_pr_body_contains_failure_class():
    assert "ImportError" in _body()


def test_build_pr_body_contains_cmd_and_exit_code():
    body = _body(original_cmd="run ls", exit_code=127)
    assert "run ls" in body
    assert "127" in body


def test_build_pr_body_contains_host_when_provided():
    assert "prod-server-01" in _body(host="prod-server-01")


def test_build_pr_body_no_host_section_when_absent():
    body = _body(host=None)
    assert "Host:" not in body


def test_build_pr_body_truncates_long_stderr():
    long_stderr = "E " * 600  # > 800 chars
    body = _body(stderr=long_stderr)
    assert "\u2026" in body  # truncation ellipsis added


def test_build_pr_body_short_stderr_not_truncated():
    body = _body(stderr="short error")
    assert "short error" in body
    assert "\u2026" not in body


def test_build_pr_body_is_markdown():
    body = _body()
    assert "#" in body
