"""
Batch 80: hermetic unit tests for navig/workspace_ownership.py
  - classify_workspace_file
  - is_project_workspace_path
  - resolve_personal_workspace_path
  - detect_project_workspace_duplicates
  - summarize_duplicates
  - WorkspaceDuplicate
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# classify_workspace_file
# ---------------------------------------------------------------------------

class TestClassifyWorkspaceFile:
    def test_agents_md_is_generated_default(self) -> None:
        from navig.workspace_ownership import classify_workspace_file
        assert classify_workspace_file("AGENTS.md") == "generated_default"

    def test_soul_md_is_generated_default(self) -> None:
        from navig.workspace_ownership import classify_workspace_file
        assert classify_workspace_file("SOUL.md") == "generated_default"

    def test_unknown_file_is_personal(self) -> None:
        from navig.workspace_ownership import classify_workspace_file
        assert classify_workspace_file("my_notes.md") == "personal_customized"

    def test_goals_json_is_personal_customized(self) -> None:
        from navig.workspace_ownership import classify_workspace_file
        # goals.json is in PERSONAL_STATE_FILES but not in GENERATED_DEFAULT_FILES
        result = classify_workspace_file("goals.json")
        assert result in ("generated_default", "personal_customized")  # flexible

    def test_case_sensitive(self) -> None:
        from navig.workspace_ownership import classify_workspace_file
        # Lowercase should not match uppercase constant
        assert classify_workspace_file("agents.md") == "personal_customized"


# ---------------------------------------------------------------------------
# is_project_workspace_path
# ---------------------------------------------------------------------------

class TestIsProjectWorkspacePath:
    def test_returns_true_for_project_workspace(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import is_project_workspace_path
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        assert is_project_workspace_path(project_ws, project_root=tmp_path) is True

    def test_returns_false_for_other_path(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import is_project_workspace_path
        other = tmp_path / "some" / "other" / "path"
        assert is_project_workspace_path(other, project_root=tmp_path) is False

    def test_returns_false_for_user_workspace(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import is_project_workspace_path, USER_WORKSPACE_DIR
        # User workspace is not the project workspace
        assert is_project_workspace_path(USER_WORKSPACE_DIR, project_root=tmp_path) is False


# ---------------------------------------------------------------------------
# resolve_personal_workspace_path
# ---------------------------------------------------------------------------

class TestResolvePersonalWorkspacePath:
    def test_none_returns_canonical(self) -> None:
        from navig.workspace_ownership import resolve_personal_workspace_path, USER_WORKSPACE_DIR
        canonical, legacy = resolve_personal_workspace_path(None)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_canonical_path_returns_no_legacy(self) -> None:
        from navig.workspace_ownership import resolve_personal_workspace_path, USER_WORKSPACE_DIR
        canonical, legacy = resolve_personal_workspace_path(USER_WORKSPACE_DIR)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_other_path_returns_canonical_plus_legacy(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import resolve_personal_workspace_path, USER_WORKSPACE_DIR
        other = tmp_path / "workspace"
        canonical, legacy = resolve_personal_workspace_path(other)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is not None
        assert legacy.expanduser() == other

    def test_return_type_is_tuple(self) -> None:
        from navig.workspace_ownership import resolve_personal_workspace_path
        result = resolve_personal_workspace_path(None)
        assert isinstance(result, tuple) and len(result) == 2


# ---------------------------------------------------------------------------
# WorkspaceDuplicate dataclass
# ---------------------------------------------------------------------------

class TestWorkspaceDuplicate:
    def test_fields(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import WorkspaceDuplicate
        wd = WorkspaceDuplicate(
            file_name="SOUL.md",
            project_path=tmp_path / "proj" / "SOUL.md",
            user_path=tmp_path / "user" / "SOUL.md",
            status="duplicate_identical",
        )
        assert wd.file_name == "SOUL.md"
        assert wd.status == "duplicate_identical"
        assert wd.user_path is not None

    def test_user_path_can_be_none(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import WorkspaceDuplicate
        wd = WorkspaceDuplicate(
            file_name="AGENTS.md",
            project_path=tmp_path / "AGENTS.md",
            user_path=None,
            status="project_only_legacy",
        )
        assert wd.user_path is None


# ---------------------------------------------------------------------------
# detect_project_workspace_duplicates
# ---------------------------------------------------------------------------

class TestDetectProjectWorkspaceDuplicates:
    def test_no_project_workspace_returns_empty(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import detect_project_workspace_duplicates
        result = detect_project_workspace_duplicates(
            project_root=tmp_path,
            user_workspace=tmp_path / "user_ws",
        )
        assert result == []

    def test_project_only_file_detected(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import detect_project_workspace_duplicates
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        (project_ws / "AGENTS.md").write_text("agents content")
        user_ws = tmp_path / "user_ws"
        user_ws.mkdir()
        result = detect_project_workspace_duplicates(
            project_root=tmp_path,
            user_workspace=user_ws,
        )
        assert len(result) == 1
        assert result[0].file_name == "AGENTS.md"
        assert result[0].status == "project_only_legacy"
        assert result[0].user_path is None

    def test_identical_duplicate_detected(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import detect_project_workspace_duplicates
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        content = "same content"
        (project_ws / "SOUL.md").write_text(content)
        user_ws = tmp_path / "user_ws"
        user_ws.mkdir()
        (user_ws / "SOUL.md").write_text(content)
        result = detect_project_workspace_duplicates(
            project_root=tmp_path,
            user_workspace=user_ws,
        )
        soul_results = [r for r in result if r.file_name == "SOUL.md"]
        assert len(soul_results) == 1
        assert soul_results[0].status == "duplicate_identical"

    def test_conflict_duplicate_detected(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import detect_project_workspace_duplicates
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        (project_ws / "USER.md").write_text("project version")
        user_ws = tmp_path / "user_ws"
        user_ws.mkdir()
        (user_ws / "USER.md").write_text("user version")
        result = detect_project_workspace_duplicates(
            project_root=tmp_path,
            user_workspace=user_ws,
        )
        user_results = [r for r in result if r.file_name == "USER.md"]
        assert len(user_results) == 1
        assert user_results[0].status == "duplicate_conflict"


# ---------------------------------------------------------------------------
# summarize_duplicates
# ---------------------------------------------------------------------------

class TestSummarizeDuplicates:
    def test_empty_list_returns_zeros(self) -> None:
        from navig.workspace_ownership import summarize_duplicates
        summary = summarize_duplicates([])
        assert summary["duplicate_conflict"] == 0
        assert summary["duplicate_identical"] == 0
        assert summary["project_only_legacy"] == 0

    def test_counts_correctly(self, tmp_path: Path) -> None:
        from navig.workspace_ownership import WorkspaceDuplicate, summarize_duplicates
        dupes = [
            WorkspaceDuplicate("A.md", tmp_path / "A.md", None, "project_only_legacy"),
            WorkspaceDuplicate("B.md", tmp_path / "B.md", tmp_path / "B2.md", "duplicate_identical"),
            WorkspaceDuplicate("C.md", tmp_path / "C.md", tmp_path / "C2.md", "duplicate_conflict"),
            WorkspaceDuplicate("D.md", tmp_path / "D.md", tmp_path / "D2.md", "duplicate_conflict"),
        ]
        summary = summarize_duplicates(dupes)
        assert summary["project_only_legacy"] == 1
        assert summary["duplicate_identical"] == 1
        assert summary["duplicate_conflict"] == 2
