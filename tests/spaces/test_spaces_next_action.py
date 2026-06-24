"""Tests for navig.spaces.next_action — first_pending_task, SpaceNextAction."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.spaces.next_action import SpaceNextAction, first_pending_task


# ---------------------------------------------------------------------------
# first_pending_task
# ---------------------------------------------------------------------------

class TestFirstPendingTask:
    def test_extracts_pending_checkbox(self) -> None:
        text = "- [ ] Fix the bug"
        assert first_pending_task(text) == "Fix the bug"

    def test_returns_empty_when_no_pending(self) -> None:
        text = "- [x] Done already"
        assert first_pending_task(text) == ""

    def test_returns_empty_for_empty_input(self) -> None:
        assert first_pending_task("") == ""

    def test_returns_empty_for_none_input(self) -> None:
        assert first_pending_task(None) == ""

    def test_picks_first_pending_task(self) -> None:
        text = "- [x] Completed\n- [ ] First pending\n- [ ] Second pending"
        assert first_pending_task(text) == "First pending"

    def test_handles_leading_whitespace(self) -> None:
        text = "   - [ ] Indented task"
        result = first_pending_task(text)
        assert "Indented task" in result

    def test_ignores_completed_checkboxes(self) -> None:
        text = "- [x] Done\n- [X] Also done"
        assert first_pending_task(text) == ""

    def test_multiline_finds_first(self) -> None:
        lines = "\n".join([f"- [ ] Task {i}" for i in range(5)])
        assert first_pending_task(lines) == "Task 0"


# ---------------------------------------------------------------------------
# SpaceNextAction
# ---------------------------------------------------------------------------

class TestSpaceNextAction:
    def test_is_frozen_dataclass(self) -> None:
        action = SpaceNextAction(
            space="devops",
            scope="project",
            goal="Ship v2",
            completion_pct=0.4,
            next_task="Write tests",
        )
        with pytest.raises((AttributeError, TypeError)):
            action.space = "other"  # type: ignore[misc]

    def test_fields_accessible(self) -> None:
        action = SpaceNextAction(
            space="sysops",
            scope="global",
            goal="Stabilize infra",
            completion_pct=0.75,
            next_task="Configure monitoring",
        )
        assert action.space == "sysops"
        assert action.scope == "global"
        assert action.goal == "Stabilize infra"
        assert action.completion_pct == 0.75
        assert action.next_task == "Configure monitoring"

    def test_completion_pct_is_float(self) -> None:
        action = SpaceNextAction(
            space="x", scope="project", goal="g", completion_pct=0.5, next_task="t"
        )
        assert isinstance(action.completion_pct, float)
