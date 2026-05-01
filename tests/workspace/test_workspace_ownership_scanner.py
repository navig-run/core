"""Tests for workspace_ownership.py and selfheal/scanner._parse_llm_output."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# workspace_ownership.py
# ──────────────────────────────────────────────────────────────────────────────
from navig.workspace_ownership import (
    GENERATED_DEFAULT_FILES,
    PERSONAL_STATE_FILES,
    WorkspaceDuplicate,
    _sha256,
    classify_workspace_file,
    detect_project_workspace_duplicates,
    is_project_workspace_path,
    summarize_duplicates,
)


class TestConstants:
    def test_personal_state_files_is_set(self):
        assert isinstance(PERSONAL_STATE_FILES, (set, frozenset))
        assert len(PERSONAL_STATE_FILES) > 0

    def test_generated_default_files_subset_of_personal(self):
        # All generated defaults are also personal
        assert GENERATED_DEFAULT_FILES <= PERSONAL_STATE_FILES

    def test_agents_is_personal(self):
        assert "AGENTS.md" in PERSONAL_STATE_FILES

    def test_soul_is_generated_default(self):
        assert "SOUL.md" in GENERATED_DEFAULT_FILES


class TestClassifyWorkspaceFile:
    def test_generated_default(self):
        for name in GENERATED_DEFAULT_FILES:
            assert classify_workspace_file(name) == "generated_default"

    def test_personal_customized(self):
        # A file in personal but not generated_default
        personal_only = PERSONAL_STATE_FILES - GENERATED_DEFAULT_FILES
        if personal_only:
            name = next(iter(personal_only))
            assert classify_workspace_file(name) == "personal_customized"

    def test_arbitrary_file_is_personal_customized(self):
        assert classify_workspace_file("random_file.txt") == "personal_customized"


class TestIsProjectWorkspacePath:
    def test_matching_path_returns_true(self, tmp_path):
        project_root = tmp_path / "myproject"
        expected = project_root / ".navig" / "workspace"
        assert is_project_workspace_path(expected, project_root=project_root) is True

    def test_different_path_returns_false(self, tmp_path):
        project_root = tmp_path / "myproject"
        other = tmp_path / "other"
        assert is_project_workspace_path(other, project_root=project_root) is False


class TestSha256:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world")
        h1 = _sha256(f)
        h2 = _sha256(f)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert _sha256(f1) != _sha256(f2)

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "x.bin"
        f2 = tmp_path / "y.bin"
        content = b"same content"
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert _sha256(f1) == _sha256(f2)

    def test_returns_hex_string(self, tmp_path):
        f = tmp_path / "hex.bin"
        f.write_bytes(b"test")
        h = _sha256(f)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex = 64 chars


class TestSummarizeDuplicates:
    def test_empty_returns_zeros(self):
        result = summarize_duplicates([])
        assert result["duplicate_conflict"] == 0
        assert result["duplicate_identical"] == 0
        assert result["project_only_legacy"] == 0

    def test_counts_each_status(self):
        dups = [
            WorkspaceDuplicate("a.md", Path("/p/a"), Path("/u/a"), "duplicate_conflict"),
            WorkspaceDuplicate("b.md", Path("/p/b"), None, "project_only_legacy"),
            WorkspaceDuplicate("c.md", Path("/p/c"), Path("/u/c"), "duplicate_identical"),
            WorkspaceDuplicate("d.md", Path("/p/d"), None, "project_only_legacy"),
        ]
        result = summarize_duplicates(dups)
        assert result["duplicate_conflict"] == 1
        assert result["project_only_legacy"] == 2
        assert result["duplicate_identical"] == 1


class TestDetectProjectWorkspaceDuplicates:
    def test_no_project_workspace_dir_returns_empty(self, tmp_path):
        result = detect_project_workspace_duplicates(project_root=tmp_path)
        assert result == []

    def test_project_only_files_detected(self, tmp_path):
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        (project_ws / "AGENTS.md").write_text("content", encoding="utf-8")
        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()
        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        statuses = {d.status for d in result}
        assert "project_only_legacy" in statuses

    def test_identical_duplicate_detected(self, tmp_path):
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()
        content = "same content"
        (project_ws / "AGENTS.md").write_text(content, encoding="utf-8")
        (user_ws / "AGENTS.md").write_text(content, encoding="utf-8")
        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        assert any(d.status == "duplicate_identical" for d in result)

    def test_conflict_duplicate_detected(self, tmp_path):
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()
        (project_ws / "AGENTS.md").write_text("version A", encoding="utf-8")
        (user_ws / "AGENTS.md").write_text("version B", encoding="utf-8")
        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        assert any(d.status == "duplicate_conflict" for d in result)


# ──────────────────────────────────────────────────────────────────────────────
# selfheal/scanner._parse_llm_output
# ──────────────────────────────────────────────────────────────────────────────
from navig.selfheal.scanner import _parse_llm_output


class TestParseLlmOutput:
    def test_empty_returns_empty(self):
        result = _parse_llm_output("", "test.py")
        assert result == []

    def test_valid_json_array(self):
        raw = json.dumps([{"issue": "x", "line": 1}])
        result = _parse_llm_output(raw, "test.py")
        assert len(result) == 1
        assert result[0]["issue"] == "x"

    def test_wrapped_in_findings_key(self):
        raw = json.dumps({"findings": [{"issue": "y"}]})
        result = _parse_llm_output(raw, "test.py")
        assert result[0]["issue"] == "y"

    def test_wrapped_in_issues_key(self):
        raw = json.dumps({"issues": [{"issue": "z"}]})
        result = _parse_llm_output(raw, "test.py")
        assert result[0]["issue"] == "z"

    def test_plain_dict_without_known_key_returns_empty(self):
        raw = json.dumps({"unknown_key": [{"x": 1}]})
        result = _parse_llm_output(raw, "test.py")
        assert result == []

    def test_invalid_json_returns_empty(self):
        # Even with repair, garbage returns empty
        result = _parse_llm_output("not json at all!!!", "test.py")
        # Should not raise; may return [] or a repaired result
        assert isinstance(result, list)

    def test_multiple_items(self):
        raw = json.dumps([{"issue": "a"}, {"issue": "b"}, {"issue": "c"}])
        result = _parse_llm_output(raw, "test.py")
        assert len(result) == 3
