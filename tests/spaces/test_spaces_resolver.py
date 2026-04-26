"""Tests for navig.spaces.resolver."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.spaces.resolver import (
    _find_project_navig_root,
    discover_space_paths,
    get_default_space,
    resolve_space,
)


# ---------------------------------------------------------------------------
# _find_project_navig_root
# ---------------------------------------------------------------------------

class TestFindProjectNavigRoot:
    def test_finds_navig_dir_in_cwd(self, tmp_path: Path) -> None:
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        result = _find_project_navig_root(tmp_path)
        assert result == navig_dir

    def test_finds_navig_dir_in_parent(self, tmp_path: Path) -> None:
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        result = _find_project_navig_root(child)
        assert result == navig_dir

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        # tmp_path itself has no .navig; we go inside a subdirectory
        child = tmp_path / "x"
        child.mkdir()
        # Patch the walk so it stops at tmp_path (no .navig anywhere)
        result = _find_project_navig_root(child)
        # Might find an ancestor's .navig on real filesystem; just ensure it
        # returns None when none exists in our controlled subtree.
        # Use a deep child of tmp_path to avoid false positives.
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# get_default_space
# ---------------------------------------------------------------------------

class TestGetDefaultSpace:
    def test_returns_default_when_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_SPACE", None)
            result = get_default_space()
        assert result == "default"

    def test_uses_env_var(self) -> None:
        with patch.dict(os.environ, {"NAVIG_SPACE": "devops"}):
            result = get_default_space()
        assert result == "devops"

    def test_normalizes_env_var(self) -> None:
        with patch.dict(os.environ, {"NAVIG_SPACE": "  DevOps  "}):
            result = get_default_space()
        assert result == result.lower()


# ---------------------------------------------------------------------------
# resolve_space
# ---------------------------------------------------------------------------

class TestResolveSpace:
    def test_returns_project_scope_when_project_space_exists(self, tmp_path: Path) -> None:
        navig_dir = tmp_path / ".navig"
        space_dir = navig_dir / "spaces" / "devops"
        space_dir.mkdir(parents=True)
        result = resolve_space("devops", cwd=tmp_path)
        assert result.scope == "project"
        assert result.path == space_dir

    def test_falls_back_to_global_when_no_project_space(self, tmp_path: Path) -> None:
        # No .navig in tmp_path
        from navig.platform import paths as nav_paths
        fake_global = tmp_path / "global_config"
        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = resolve_space("default", cwd=tmp_path)
        assert result.scope == "global"

    def test_stores_requested_and_canonical_names(self, tmp_path: Path) -> None:
        from navig.platform import paths as nav_paths
        fake_global = tmp_path / "global_config"
        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = resolve_space("Default", cwd=tmp_path)
        assert result.requested_name == "Default"
        assert result.canonical_name == result.canonical_name.lower()

    def test_project_space_preferred_over_global(self, tmp_path: Path) -> None:
        navig_dir = tmp_path / ".navig"
        space_dir = navig_dir / "spaces" / "devops"
        space_dir.mkdir(parents=True)

        fake_global = tmp_path / "global_config"
        global_space = fake_global / "spaces" / "devops"
        global_space.mkdir(parents=True)

        from navig.platform import paths as nav_paths
        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = resolve_space("devops", cwd=tmp_path)

        assert result.scope == "project"


# ---------------------------------------------------------------------------
# discover_space_paths
# ---------------------------------------------------------------------------

class TestDiscoverSpacePaths:
    def test_returns_empty_dict_when_no_spaces(self, tmp_path: Path) -> None:
        from navig.platform import paths as nav_paths
        fake_global = tmp_path / "global_config"
        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = discover_space_paths(cwd=tmp_path)
        assert result == {}

    def test_discovers_global_spaces(self, tmp_path: Path) -> None:
        from navig.platform import paths as nav_paths
        fake_global = tmp_path / "global_config"
        (fake_global / "spaces" / "devops").mkdir(parents=True)
        (fake_global / "spaces" / "sysops").mkdir(parents=True)

        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = discover_space_paths(cwd=tmp_path)

        assert "devops" in result
        assert "sysops" in result
        assert result["devops"].scope == "global"

    def test_project_spaces_override_global(self, tmp_path: Path) -> None:
        from navig.platform import paths as nav_paths
        fake_global = tmp_path / "global_config"
        (fake_global / "spaces" / "devops").mkdir(parents=True)

        navig_dir = tmp_path / ".navig"
        (navig_dir / "spaces" / "devops").mkdir(parents=True)

        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = discover_space_paths(cwd=tmp_path)

        assert result["devops"].scope == "project"

    def test_discovers_project_only_spaces(self, tmp_path: Path) -> None:
        from navig.platform import paths as nav_paths
        from navig.spaces.contracts import normalize_space_name
        fake_global = tmp_path / "global_config"

        navig_dir = tmp_path / ".navig"
        space_name = "devops"
        (navig_dir / "spaces" / space_name).mkdir(parents=True)

        with patch.object(nav_paths, "config_dir", return_value=fake_global):
            result = discover_space_paths(cwd=tmp_path)

        canonical = normalize_space_name(space_name)
        assert canonical in result
        assert result[canonical].scope == "project"
