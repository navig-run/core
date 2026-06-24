"""Tests for navig.spaces.kickoff — SpaceKickoff, _vision_goal, _extract_pending_actions."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.spaces.kickoff import (
    SpaceKickoff,
    _extract_pending_actions,
    _vision_goal,
    build_space_kickoff,
)


class TestVisionGoal:
    def test_reads_goal_from_frontmatter(self):
        text = "---\ngoal: Deploy production\n---\n# Something"
        assert _vision_goal(text, "fallback") == "Deploy production"

    def test_reads_h1_when_no_frontmatter_goal(self):
        text = "# My Main Goal\nSome content"
        assert _vision_goal(text, "fallback") == "My Main Goal"

    def test_returns_fallback_when_nothing(self):
        assert _vision_goal("some text without heading", "fallback") == "fallback"

    def test_returns_fallback_on_empty(self):
        assert _vision_goal("", "default") == "default"


class TestExtractPendingActions:
    def test_extracts_unchecked_checkboxes(self):
        md = "- [ ] First task\n- [x] Done\n- [ ] Second task"
        result = _extract_pending_actions(md)
        assert "First task" in result
        assert "Second task" in result
        assert len(result) == 2

    def test_falls_back_to_bullets_when_no_checkboxes(self):
        md = "- Deploy app\n- Run tests"
        result = _extract_pending_actions(md)
        assert "Deploy app" in result
        assert "Run tests" in result

    def test_empty_string_returns_empty(self):
        assert _extract_pending_actions("") == []

    def test_none_safe(self):
        assert _extract_pending_actions(None) == []  # type: ignore[arg-type]

    def test_bullets_skip_headings(self):
        md = "- # Not a task\n- Real task"
        result = _extract_pending_actions(md)
        assert result == ["Real task"]

    def test_bullets_skip_brackets(self):
        md = "- [ ] checkbox\n- [x] done"
        result = _extract_pending_actions(md)
        assert "checkbox" in result


class TestSpaceKickoff:
    def test_frozen_dataclass(self):
        k = SpaceKickoff(space="devops", goal="Deploy", actions=["Do X"])
        with pytest.raises((AttributeError, TypeError)):
            k.space = "other"  # type: ignore[misc]

    def test_fields(self):
        k = SpaceKickoff(space="ops", goal="Monitor", actions=["A", "B"])
        assert k.space == "ops"
        assert k.goal == "Monitor"
        assert k.actions == ["A", "B"]


class TestBuildSpaceKickoff:
    def test_returns_kickoff(self, tmp_path):
        space_dir = tmp_path / "devops"
        space_dir.mkdir()
        (space_dir / "VISION.md").write_text("# Deploy Everything\n")
        (space_dir / "CURRENT_PHASE.md").write_text("- [ ] Set up CI\n- [ ] Deploy staging\n")
        result = build_space_kickoff("devops", space_dir, cwd=tmp_path)
        assert isinstance(result, SpaceKickoff)
        assert result.space == "devops"

    def test_goal_from_vision_h1(self, tmp_path):
        space_dir = tmp_path / "sysops"
        space_dir.mkdir()
        (space_dir / "VISION.md").write_text("# Infrastructure Hardening\n")
        (space_dir / "CURRENT_PHASE.md").write_text("")
        result = build_space_kickoff("sysops", space_dir, cwd=tmp_path)
        assert "Infrastructure Hardening" in result.goal

    def test_max_items_respected(self, tmp_path):
        space_dir = tmp_path / "space"
        space_dir.mkdir()
        (space_dir / "VISION.md").write_text("# Goal\n")
        (space_dir / "CURRENT_PHASE.md").write_text(
            "- [ ] A\n- [ ] B\n- [ ] C\n- [ ] D\n- [ ] E\n"
        )
        result = build_space_kickoff("space", space_dir, cwd=tmp_path, max_items=2)
        assert len(result.actions) <= 2

    def test_empty_files_use_fallback_goal(self, tmp_path):
        space_dir = tmp_path / "empty"
        space_dir.mkdir()
        (space_dir / "VISION.md").write_text("")
        (space_dir / "CURRENT_PHASE.md").write_text("")
        result = build_space_kickoff("empty", space_dir, cwd=tmp_path)
        assert "empty" in result.goal
