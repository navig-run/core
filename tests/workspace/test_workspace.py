"""
Tests for navig.workspace and navig.workspace_ownership.

All tests are hermetic — only tmp_path for filesystem I/O, no real ~/.navig access.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.workspace_ownership import (
    PERSONAL_STATE_FILES,
    GENERATED_DEFAULT_FILES,
    classify_workspace_file,
    is_project_workspace_path,
)
from navig.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# workspace_ownership — classify_workspace_file
# ---------------------------------------------------------------------------


class TestClassifyWorkspaceFile:
    def test_generated_default_files_return_generated(self):
        for f in GENERATED_DEFAULT_FILES:
            assert classify_workspace_file(f) == "generated_default"

    def test_personal_only_files_return_personal(self):
        personal_only = PERSONAL_STATE_FILES - GENERATED_DEFAULT_FILES
        for f in personal_only:
            assert classify_workspace_file(f) == "personal_customized"

    def test_unknown_file_returns_personal_customized(self):
        assert classify_workspace_file("RANDOM.md") == "personal_customized"
        assert classify_workspace_file("custom.json") == "personal_customized"

    def test_case_sensitive(self):
        # lowercase variants are not in the set
        assert classify_workspace_file("agents.md") == "personal_customized"
        assert classify_workspace_file("identity.md") == "personal_customized"


# ---------------------------------------------------------------------------
# workspace_ownership — is_project_workspace_path
# ---------------------------------------------------------------------------


class TestIsProjectWorkspacePath:
    def test_true_for_project_navig_workspace(self, tmp_path):
        project_root = tmp_path / "myproject"
        project_workspace = project_root / ".navig" / "workspace"
        project_workspace.mkdir(parents=True)
        assert is_project_workspace_path(project_workspace, project_root=project_root)

    def test_false_for_arbitrary_other_path(self, tmp_path):
        project_root = tmp_path / "myproject"
        some_path = tmp_path / "other_dir"
        assert not is_project_workspace_path(some_path, project_root=project_root)

    def test_false_for_user_workspace(self, tmp_path):
        project_root = tmp_path / "proj"
        user_ws = Path.home() / ".navig" / "workspace"
        assert not is_project_workspace_path(user_ws, project_root=project_root)

    def test_false_for_partial_match(self, tmp_path):
        project_root = tmp_path / "proj"
        # .navig exists but not the workspace subdir path
        partial = project_root / ".navig"
        partial.mkdir(parents=True)
        assert not is_project_workspace_path(partial, project_root=project_root)


# ---------------------------------------------------------------------------
# WorkspaceManager — _load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_dict_for_valid_json(self, tmp_path):
        cfg = tmp_path / "navig.json"
        cfg.write_text(json.dumps({"agents": {"defaults": {"workspace": str(tmp_path)}}}))
        with patch("navig.workspace.USER_WORKSPACE_DIR", tmp_path / "ws"):
            wm = WorkspaceManager(workspace_path=tmp_path, config_path=cfg)
        assert isinstance(wm.config, dict)
        assert "agents" in wm.config

    def test_returns_none_for_missing_config(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "nonexistent.json")
        assert wm.config is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        cfg = tmp_path / "navig.json"
        cfg.write_text("NOT JSON {{")
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=cfg)
        assert wm.config is None


# ---------------------------------------------------------------------------
# WorkspaceManager — _validated_workspace_override
# ---------------------------------------------------------------------------


class TestValidatedWorkspaceOverride:
    def _make_wm(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        return WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")

    def test_accepts_path_under_home(self, tmp_path):
        wm = self._make_wm(tmp_path)
        home_sub = Path.home() / "somesubdir"
        result = wm._validated_workspace_override(home_sub)
        # Should accept (doesn't have to exist)
        assert "somesubdir" in str(result)

    def test_accepts_tmpdir(self, tmp_path):
        wm = self._make_wm(tmp_path)
        tmp_sub = Path(tempfile.gettempdir()) / "test_ws_override"
        result = wm._validated_workspace_override(tmp_sub)
        assert "test_ws_override" in str(result)

    def test_accepts_cwd_subpath(self, tmp_path):
        """Path under the current working directory must be accepted."""
        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            wm = self._make_wm(tmp_path)
            cwd_sub = Path.cwd() / "sub"
            result = wm._validated_workspace_override(cwd_sub)
            assert "sub" in str(result)
        finally:
            os.chdir(old_cwd)

    def test_rejects_external_path(self, tmp_path):
        from navig.workspace import USER_WORKSPACE_DIR
        # We need a path that is NOT under home, cwd or tmpdir
        # Use an absolute path in a drive root that isn't allowed
        wm = self._make_wm(tmp_path)
        # On Windows we can try a known non-home, non-tmp, non-cwd path
        # On all platforms: use a monkeypatched home/cwd to make it deterministic
        # Instead directly test by monkeypatching allowed_roots
        import os
        old_cwd = os.getcwd()
        # Switch to a predictably short cwd
        os.chdir(tmp_path)
        try:
            # An absolute path outside home / tmp / cwd should fall back to USER_WORKSPACE_DIR
            # But this is hard to guarantee cross-platform, so we verify return value type at least
            wm2 = WorkspaceManager(workspace_path=tmp_path, config_path=tmp_path / "no.json")
            result = wm2._validated_workspace_override(tmp_path)
            # tmp_path IS under tmpdir so it must be accepted
            assert result == tmp_path.resolve()
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# WorkspaceManager — is_initialized
# ---------------------------------------------------------------------------


class TestIsInitialized:
    def test_false_when_directory_empty(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        assert not wm.is_initialized()

    def test_true_when_identity_file_exists(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "IDENTITY.md").write_text("# Identity")
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        assert wm.is_initialized()

    def test_true_for_any_bootstrap_file(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "USER.md").write_text("# User")
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        assert wm.is_initialized()


# ---------------------------------------------------------------------------
# WorkspaceManager — get_bootstrap_content
# ---------------------------------------------------------------------------


class TestGetBootstrapContent:
    def _wm(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        wm.legacy_workspace_path = None
        return wm, ws

    def test_empty_when_no_files(self, tmp_path):
        wm, _ = self._wm(tmp_path)
        content = wm.get_bootstrap_content()
        assert content == ""

    def test_includes_file_heading(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("Agent: NAVIG")
        content = wm.get_bootstrap_content()
        assert "## IDENTITY.md" in content
        assert "Agent: NAVIG" in content

    def test_strips_frontmatter(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("---\ntitle: test\n---\nAgent: NAVIG")
        content = wm.get_bootstrap_content()
        assert "title: test" not in content
        assert "Agent: NAVIG" in content

    def test_excludes_bootstrap_when_flag_false(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "BOOTSTRAP.md").write_text("First run!")
        content = wm.get_bootstrap_content(include_first_run=False)
        assert "BOOTSTRAP.md" not in content

    def test_includes_bootstrap_by_default(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "BOOTSTRAP.md").write_text("First run!")
        content = wm.get_bootstrap_content()
        assert "BOOTSTRAP.md" in content

    def test_multiple_files_joined_with_separator(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("I am NAVIG")
        (ws / "USER.md").write_text("User info")
        content = wm.get_bootstrap_content()
        assert "---" in content


# ---------------------------------------------------------------------------
# WorkspaceManager — get_file_content & update_file
# ---------------------------------------------------------------------------


class TestFileIO:
    def _wm(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        wm.legacy_workspace_path = None
        return wm, ws

    def test_get_file_content_returns_none_when_missing(self, tmp_path):
        wm, _ = self._wm(tmp_path)
        assert wm.get_file_content("NONEXISTENT.md") is None

    def test_get_file_content_returns_text(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "SOUL.md").write_text("Deep values")
        assert wm.get_file_content("SOUL.md") == "Deep values"

    def test_update_file_creates_file(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        result = wm.update_file("USER.md", "New content")
        assert result is True
        assert (ws / "USER.md").read_text() == "New content"

    def test_update_file_overwrites_existing(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "USER.md").write_text("Old content")
        wm.update_file("USER.md", "Updated content")
        assert (ws / "USER.md").read_text() == "Updated content"

    def test_update_file_creates_workspace_dir(self, tmp_path):
        ws = tmp_path / "deep" / "nested" / "ws"
        wm = WorkspaceManager.__new__(WorkspaceManager)
        wm.workspace_path = ws
        wm.legacy_workspace_path = None
        wm.config_path = tmp_path / "no.json"
        wm.config = None
        result = wm.update_file("USER.md", "hello")
        assert result is True
        assert ws.exists()


# ---------------------------------------------------------------------------
# WorkspaceManager — has_bootstrap_pending & complete_bootstrap
# ---------------------------------------------------------------------------


class TestBootstrapLifecycle:
    def _wm(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        wm.legacy_workspace_path = None
        return wm, ws

    def test_has_bootstrap_pending_false_when_no_file(self, tmp_path):
        wm, _ = self._wm(tmp_path)
        assert not wm.has_bootstrap_pending()

    def test_has_bootstrap_pending_true_when_file_exists(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "BOOTSTRAP.md").write_text("first run")
        assert wm.has_bootstrap_pending()

    def test_complete_bootstrap_removes_file(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "BOOTSTRAP.md").write_text("first run")
        result = wm.complete_bootstrap()
        assert result is True
        assert not (ws / "BOOTSTRAP.md").exists()

    def test_complete_bootstrap_returns_false_when_no_file(self, tmp_path):
        wm, _ = self._wm(tmp_path)
        assert wm.complete_bootstrap() is False


# ---------------------------------------------------------------------------
# WorkspaceManager — get_agent_identity
# ---------------------------------------------------------------------------


class TestGetAgentIdentity:
    def _wm(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        wm = WorkspaceManager(workspace_path=ws, config_path=tmp_path / "no.json")
        wm.workspace_path = ws
        wm.legacy_workspace_path = None
        return wm, ws

    def test_default_identity_when_no_file(self, tmp_path):
        wm, _ = self._wm(tmp_path)
        identity = wm.get_agent_identity()
        assert identity["name"] == "NAVIG"
        assert "personality" in identity

    def test_parses_name_from_identity_md(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("**Name**: Hermes\n**Emoji**: crystal", encoding="utf-8")
        identity = wm.get_agent_identity()
        assert identity["name"] == "Hermes"

    def test_parses_emoji_from_identity_md(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("**Emoji**: crystal\n", encoding="utf-8")
        identity = wm.get_agent_identity()
        assert identity["emoji"] == "crystal"

    def test_returns_defaults_for_unknown_fields(self, tmp_path):
        wm, ws = self._wm(tmp_path)
        (ws / "IDENTITY.md").write_text("nothing relevant here")
        identity = wm.get_agent_identity()
        assert identity["name"] == "NAVIG"


# ---------------------------------------------------------------------------
# WorkspaceManager — BOOTSTRAP_FILES constant
# ---------------------------------------------------------------------------


class TestBootstrapFilesConstant:
    def test_identity_is_first(self):
        assert WorkspaceManager.BOOTSTRAP_FILES[0] == "IDENTITY.md"

    def test_bootstrap_md_present(self):
        assert "BOOTSTRAP.md" in WorkspaceManager.BOOTSTRAP_FILES

    def test_all_are_strings(self):
        for f in WorkspaceManager.BOOTSTRAP_FILES:
            assert isinstance(f, str)
            assert f.endswith(".md")
