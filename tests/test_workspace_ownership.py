"""Tests for navig.workspace_ownership — classification, path resolution, duplicate detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_personal_state_files_is_set(self):
        from navig.workspace_ownership import PERSONAL_STATE_FILES

        assert isinstance(PERSONAL_STATE_FILES, set)
        assert "AGENTS.md" in PERSONAL_STATE_FILES
        assert "goals.json" in PERSONAL_STATE_FILES

    def test_generated_default_files_subset_of_personal(self):
        from navig.workspace_ownership import GENERATED_DEFAULT_FILES, PERSONAL_STATE_FILES

        assert GENERATED_DEFAULT_FILES.issubset(PERSONAL_STATE_FILES)


# ---------------------------------------------------------------------------
# classify_workspace_file
# ---------------------------------------------------------------------------


class TestClassifyWorkspaceFile:
    def test_generated_default_known_file(self):
        from navig.workspace_ownership import GENERATED_DEFAULT_FILES, classify_workspace_file

        for name in GENERATED_DEFAULT_FILES:
            assert classify_workspace_file(name) == "generated_default"

    def test_personal_file_not_in_generated(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("goals.json") == "personal_customized"

    def test_unknown_file_is_personal(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("custom_notes.txt") == "personal_customized"


# ---------------------------------------------------------------------------
# is_project_workspace_path
# ---------------------------------------------------------------------------


class TestIsProjectWorkspacePath:
    def test_returns_true_for_project_workspace(self, tmp_path):
        from navig.workspace_ownership import is_project_workspace_path

        project_root = tmp_path / "myproject"
        project_root.mkdir()
        project_ws = project_root / ".navig" / "workspace"
        project_ws.mkdir(parents=True)

        assert is_project_workspace_path(project_ws, project_root=project_root) is True

    def test_returns_false_for_unrelated_path(self, tmp_path):
        from navig.workspace_ownership import is_project_workspace_path

        unrelated = tmp_path / "some" / "other" / "dir"
        assert is_project_workspace_path(unrelated, project_root=tmp_path) is False

    def test_user_workspace_is_not_project_workspace(self, tmp_path):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, is_project_workspace_path

        assert is_project_workspace_path(USER_WORKSPACE_DIR, project_root=tmp_path) is False


# ---------------------------------------------------------------------------
# resolve_personal_workspace_path
# ---------------------------------------------------------------------------


class TestResolvePersonalWorkspacePath:
    def test_none_requested_returns_canonical(self):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, resolve_personal_workspace_path

        canonical, legacy = resolve_personal_workspace_path(None)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_canonical_path_returns_no_legacy(self):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, resolve_personal_workspace_path

        canonical, legacy = resolve_personal_workspace_path(USER_WORKSPACE_DIR)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_different_path_returned_as_legacy(self, tmp_path):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, resolve_personal_workspace_path

        other = tmp_path / "other_workspace"
        other.mkdir()
        canonical, legacy = resolve_personal_workspace_path(other)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is not None
        assert legacy.resolve() == other.resolve()


# ---------------------------------------------------------------------------
# WorkspaceDuplicate
# ---------------------------------------------------------------------------


class TestWorkspaceDuplicate:
    def test_fields(self, tmp_path):
        from navig.workspace_ownership import WorkspaceDuplicate

        dup = WorkspaceDuplicate(
            file_name="SOUL.md",
            project_path=tmp_path / "project" / "SOUL.md",
            user_path=tmp_path / "user" / "SOUL.md",
            status="duplicate_conflict",
        )
        assert dup.file_name == "SOUL.md"
        assert dup.status == "duplicate_conflict"
        assert dup.user_path is not None


# ---------------------------------------------------------------------------
# detect_project_workspace_duplicates
# ---------------------------------------------------------------------------


class TestDetectProjectWorkspaceDuplicates:
    def test_no_project_workspace_returns_empty(self, tmp_path):
        from navig.workspace_ownership import detect_project_workspace_duplicates

        result = detect_project_workspace_duplicates(project_root=tmp_path)
        assert result == []

    def test_project_only_file_detected(self, tmp_path):
        from navig.workspace_ownership import detect_project_workspace_duplicates

        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        (project_ws / "SOUL.md").write_text("soul content", encoding="utf-8")

        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()

        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        assert len(result) == 1
        assert result[0].file_name == "SOUL.md"
        assert result[0].status == "project_only_legacy"

    def test_identical_files_detected(self, tmp_path):
        from navig.workspace_ownership import detect_project_workspace_duplicates

        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()

        content = "identical content"
        (project_ws / "AGENTS.md").write_text(content, encoding="utf-8")
        (user_ws / "AGENTS.md").write_text(content, encoding="utf-8")

        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        assert len(result) == 1
        assert result[0].status == "duplicate_identical"

    def test_conflicting_files_detected(self, tmp_path):
        from navig.workspace_ownership import detect_project_workspace_duplicates

        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        user_ws = tmp_path / "user_workspace"
        user_ws.mkdir()

        (project_ws / "IDENTITY.md").write_text("project version", encoding="utf-8")
        (user_ws / "IDENTITY.md").write_text("user version - different", encoding="utf-8")

        result = detect_project_workspace_duplicates(
            project_root=tmp_path, user_workspace=user_ws
        )
        assert any(r.status == "duplicate_conflict" for r in result)

    def test_only_personal_state_files_scanned(self, tmp_path):
        from navig.workspace_ownership import detect_project_workspace_duplicates

        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        # Add a file NOT in PERSONAL_STATE_FILES
        (project_ws / "random_notes.txt").write_text("notes", encoding="utf-8")

        result = detect_project_workspace_duplicates(project_root=tmp_path)
        # random_notes.txt should NOT appear in results
        assert all(r.file_name != "random_notes.txt" for r in result)


# ---------------------------------------------------------------------------
# summarize_duplicates
# ---------------------------------------------------------------------------


class TestSummarizeDuplicates:
    def test_empty_returns_zeros(self):
        from navig.workspace_ownership import summarize_duplicates

        summary = summarize_duplicates([])
        assert summary["duplicate_conflict"] == 0
        assert summary["duplicate_identical"] == 0
        assert summary["project_only_legacy"] == 0

    def test_counts_correctly(self, tmp_path):
        from navig.workspace_ownership import WorkspaceDuplicate, summarize_duplicates

        dups = [
            WorkspaceDuplicate("A.md", tmp_path / "A.md", None, "project_only_legacy"),
            WorkspaceDuplicate("B.md", tmp_path / "B.md", tmp_path / "B.md", "duplicate_identical"),
            WorkspaceDuplicate("C.md", tmp_path / "C.md", tmp_path / "C.md", "duplicate_conflict"),
            WorkspaceDuplicate("D.md", tmp_path / "D.md", None, "project_only_legacy"),
        ]
        summary = summarize_duplicates(dups)
        assert summary["project_only_legacy"] == 2
        assert summary["duplicate_identical"] == 1
        assert summary["duplicate_conflict"] == 1
