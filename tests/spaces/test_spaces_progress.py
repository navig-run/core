"""Tests for navig.spaces.progress — _completion_from_markdown, read_space_progress."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.spaces.progress import SpaceProgress, _completion_from_markdown, read_space_progress


class TestCompletionFromMarkdown:
    def test_all_done_returns_100(self) -> None:
        text = "- [x] done\n- [X] also done"
        assert _completion_from_markdown(text) == 100.0

    def test_none_done_returns_0(self) -> None:
        text = "- [ ] task 1\n- [ ] task 2"
        assert _completion_from_markdown(text) == 0.0

    def test_half_done(self) -> None:
        text = "- [x] done\n- [ ] pending"
        assert _completion_from_markdown(text) == 50.0

    def test_empty_text_returns_0(self) -> None:
        assert _completion_from_markdown("") == 0.0

    def test_no_checkboxes_returns_0(self) -> None:
        assert _completion_from_markdown("Just some text\n## Heading") == 0.0

    def test_mixed_case_x(self) -> None:
        text = "- [x] lower\n- [X] upper\n- [ ] pending"
        pct = _completion_from_markdown(text)
        assert abs(pct - 66.7) < 1.0


class TestReadSpaceProgress:
    def test_returns_space_progress_instance(self, tmp_path: Path) -> None:
        result = read_space_progress("myspace", tmp_path, "project")
        assert isinstance(result, SpaceProgress)

    def test_name_stored(self, tmp_path: Path) -> None:
        result = read_space_progress("myspace", tmp_path, "project")
        assert result.name == "myspace"

    def test_scope_stored(self, tmp_path: Path) -> None:
        result = read_space_progress("myspace", tmp_path, "global")
        assert result.scope == "global"

    def test_path_stored(self, tmp_path: Path) -> None:
        result = read_space_progress("myspace", tmp_path, "project")
        assert result.path == tmp_path

    def test_goal_from_vision_frontmatter(self, tmp_path: Path) -> None:
        vision = tmp_path / "VISION.md"
        vision.write_text("---\ngoal: Ship MVP\n---\n# Some heading\n", encoding="utf-8")
        result = read_space_progress("myspace", tmp_path, "project")
        assert result.goal == "Ship MVP"

    def test_goal_falls_back_to_h1(self, tmp_path: Path) -> None:
        vision = tmp_path / "VISION.md"
        vision.write_text("# My Space Goal\n\nSome description\n", encoding="utf-8")
        result = read_space_progress("myspace", tmp_path, "project")
        assert "My Space Goal" in result.goal

    def test_goal_fallback_to_space_name(self, tmp_path: Path) -> None:
        # No VISION.md at all → fallback to "<name> goals"
        result = read_space_progress("devops", tmp_path, "project")
        assert "devops" in result.goal.lower()

    def test_completion_from_frontmatter(self, tmp_path: Path) -> None:
        phase = tmp_path / "CURRENT_PHASE.md"
        phase.write_text("---\ncompletion_pct: 75\n---\n", encoding="utf-8")
        result = read_space_progress("s", tmp_path, "project")
        assert result.completion_pct == 75.0

    def test_completion_calculated_from_checkboxes(self, tmp_path: Path) -> None:
        phase = tmp_path / "CURRENT_PHASE.md"
        phase.write_text("- [x] done\n- [ ] pending\n", encoding="utf-8")
        result = read_space_progress("s", tmp_path, "project")
        assert result.completion_pct == 50.0

    def test_last_updated_from_frontmatter(self, tmp_path: Path) -> None:
        phase = tmp_path / "CURRENT_PHASE.md"
        phase.write_text("---\nlast_updated: 2024-01-15\n---\n", encoding="utf-8")
        result = read_space_progress("s", tmp_path, "project")
        assert result.last_updated == "2024-01-15"
