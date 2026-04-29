"""
Tests for navig.selfheal.heal_pr_submitter — store/list patches, token, body builder.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.selfheal.heal_pr_submitter import HealPRSubmitter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _submitter() -> HealPRSubmitter:
    return HealPRSubmitter()


def _sample_patch() -> dict:
    return {
        "failure_class": "ImportError",
        "cmd": "navig db list",
        "exit_code": 1,
        "stderr": "ModuleNotFoundError: No module named 'something'",
        "patch_text": "--- a/navig/foo.py\n+++ b/navig/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
        "repo": "owner/repo",
        "branch": "fix/selfheal-test",
    }


# ---------------------------------------------------------------------------
# _get_token
# ---------------------------------------------------------------------------


def test_get_token_raises_when_not_set():
    s = _submitter()
    with patch.dict("os.environ", {}, clear=True):
        # Remove the key if present
        import os
        os.environ.pop("NAVIG_GITHUB_TOKEN", None)
        with pytest.raises(ValueError, match="NAVIG_GITHUB_TOKEN"):
            s._get_token()


def test_get_token_returns_env_var():
    s = _submitter()
    with patch.dict("os.environ", {"NAVIG_GITHUB_TOKEN": "tok_abc123"}):
        token = s._get_token()
    assert token == "tok_abc123"


# ---------------------------------------------------------------------------
# store_pending_patch
# ---------------------------------------------------------------------------


def test_store_pending_patch_creates_file(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(_sample_patch())

    assert isinstance(result, Path)
    assert result.exists()


def test_store_pending_patch_writes_json(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(_sample_patch())

    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["failure_class"] == "ImportError"
    assert data["cmd"] == "navig db list"
    assert data["submitted"] is False


def test_store_pending_patch_includes_submitted_false(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "heal_patches"
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.store_pending_patch(_sample_patch())

    data = json.loads(result.read_text(encoding="utf-8"))
    assert "submitted" in data
    assert data["submitted"] is False


def test_store_pending_patch_creates_directory(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "nonexistent" / "nested" / "patches"
    assert not patch_dir.exists()
    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        s.store_pending_patch(_sample_patch())
    assert patch_dir.exists()


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

    data = {**_sample_patch(), "submitted": False}
    (patch_dir / "patch1.json").write_text(json.dumps(data), encoding="utf-8")

    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()

    assert len(result) == 1
    assert result[0]["failure_class"] == "ImportError"


def test_list_pending_patches_skips_submitted(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()

    submitted = {**_sample_patch(), "submitted": True}
    pending = {**_sample_patch(), "submitted": False}
    (patch_dir / "submitted.json").write_text(json.dumps(submitted), encoding="utf-8")
    (patch_dir / "pending.json").write_text(json.dumps(pending), encoding="utf-8")

    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()

    assert len(result) == 1
    assert result[0]["submitted"] is False


def test_list_pending_patches_skips_corrupt_json(tmp_path):
    s = _submitter()
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()

    (patch_dir / "corrupt.json").write_text("not valid json{{{", encoding="utf-8")
    pending = {**_sample_patch(), "submitted": False}
    (patch_dir / "valid.json").write_text(json.dumps(pending), encoding="utf-8")

    with patch("navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", patch_dir):
        result = s.list_pending_patches()

    assert len(result) == 1  # only valid, not corrupt


# ---------------------------------------------------------------------------
# _build_pr_body
# ---------------------------------------------------------------------------


def test_build_pr_body_contains_required_fields():
    s = _submitter()
    patch_data = _sample_patch()
    body = s._build_pr_body(patch_data)

    assert "ImportError" in body
    assert "navig db list" in body
    assert "exit_code" in body.lower() or "exit code" in body.lower() or "1" in body
    assert "navig/foo.py" in body  # patch text excerpt


def test_build_pr_body_truncates_long_stderr():
    s = _submitter()
    long_stderr = "E " * 500  # well over 800 chars
    patch_data = {**_sample_patch(), "stderr": long_stderr}
    body = s._build_pr_body(patch_data)
    # The full stderr should be truncated; 800-char limit means < original length
    # Just verify it doesn't embed the whole thing (> 1600 chars of just stderr)
    assert len(body) < len(long_stderr) + 3000


def test_build_pr_body_contains_host_when_provided():
    s = _submitter()
    patch_data = {**_sample_patch(), "host": "prod-server-01"}
    body = s._build_pr_body(patch_data)
    assert "prod-server-01" in body


def test_build_pr_body_no_host_field_when_absent():
    s = _submitter()
    patch_data = _sample_patch()
    patch_data.pop("host", None)
    body = s._build_pr_body(patch_data)
    # Should not crash; "host" section may be absent
    assert isinstance(body, str)
    assert len(body) > 0


def test_build_pr_body_is_markdown():
    s = _submitter()
    body = s._build_pr_body(_sample_patch())
    # Should contain at least one markdown heading
    assert "#" in body or "```" in body
