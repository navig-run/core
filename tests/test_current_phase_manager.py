"""Hermetic unit tests for navig.plans.current_phase_manager."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from navig.plans.current_phase_manager import (
    CurrentPhaseManager,
    PhaseState,
    _parse_active_tasks,
)

# ---------------------------------------------------------------------------
# _parse_active_tasks (pure function)
# ---------------------------------------------------------------------------


class TestParseActiveTasks:
    def test_empty_text_returns_empty(self):
        assert _parse_active_tasks("") == []

    def test_no_active_tasks_header(self):
        assert _parse_active_tasks("## Goals\n- something\n") == []

    def test_dash_items(self):
        text = "## Active Tasks\n- Task one\n- Task two\n"
        assert _parse_active_tasks(text) == ["Task one", "Task two"]

    def test_star_items(self):
        text = "## Active Tasks\n* Task alpha\n* Task beta\n"
        assert _parse_active_tasks(text) == ["Task alpha", "Task beta"]

    def test_mixed_dash_and_star(self):
        text = "## Active Tasks\n- A\n* B\n"
        assert _parse_active_tasks(text) == ["A", "B"]

    def test_stops_at_next_heading(self):
        text = "## Active Tasks\n- First\n\n## Decisions Made\n- Other\n"
        assert _parse_active_tasks(text) == ["First"]

    def test_strips_extra_whitespace(self):
        text = "## Active Tasks\n  -   padded item  \n"
        assert _parse_active_tasks(text) == ["padded item"]

    def test_empty_section_returns_empty(self):
        text = "## Active Tasks\n\n## Next Section\n"
        assert _parse_active_tasks(text) == []


# ---------------------------------------------------------------------------
# PhaseState dataclass
# ---------------------------------------------------------------------------


class TestPhaseStateDataclass:
    def _make(self) -> PhaseState:
        return PhaseState(
            phase="01",
            title="Bootstrap",
            started="2025-01-01",
            milestone="MVP1",
            status="active",
            blocked_by="~",
            active_tasks=["Task A", "Task B"],
            raw_content="---\n...\n---\n",
            source_path=Path("/tmp/CURRENT_PHASE.md"),
        )

    def test_phase(self):
        assert self._make().phase == "01"

    def test_title(self):
        assert self._make().title == "Bootstrap"

    def test_status(self):
        assert self._make().status == "active"

    def test_active_tasks_count(self):
        assert len(self._make().active_tasks) == 2


# ---------------------------------------------------------------------------
# Helper to create a CURRENT_PHASE.md in tmp_path
# ---------------------------------------------------------------------------


_PHASE_CONTENT = textwrap.dedent("""\
    ---
    phase: 01
    title: Bootstrap
    started: 2025-01-01
    milestone: MVP1
    status: active
    blocked_by: ~
    ---

    ## Objective

    Initial setup.

    ## Active Tasks

    - Configure repo
    - Add CI

    ## Decisions Made This Phase

""")


def _make_phase_file(phases_dir: Path, content: str = _PHASE_CONTENT) -> Path:
    phases_dir.mkdir(parents=True, exist_ok=True)
    path = phases_dir / "CURRENT_PHASE.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CurrentPhaseManager.get_current_phase
# ---------------------------------------------------------------------------


class TestGetCurrentPhase:
    def test_returns_none_when_no_file(self, tmp_path):
        mgr = CurrentPhaseManager(tmp_path)
        assert mgr.get_current_phase() is None

    def test_parses_from_phases_dir(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.get_current_phase()
        assert state is not None
        assert state.phase == "01"
        assert state.title == "Bootstrap"
        assert state.milestone == "MVP1"
        assert state.status == "active"

    def test_parses_active_tasks(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.get_current_phase()
        assert state is not None
        assert "Configure repo" in state.active_tasks
        assert "Add CI" in state.active_tasks

    def test_fallback_to_navig_root(self, tmp_path):
        # Place file in .navig/ instead of .navig/plans/phases/
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir(parents=True)
        (navig_dir / "CURRENT_PHASE.md").write_text(_PHASE_CONTENT, encoding="utf-8")
        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.get_current_phase()
        assert state is not None
        assert state.phase == "01"


# ---------------------------------------------------------------------------
# CurrentPhaseManager.block_phase / unblock_phase / complete_phase
# ---------------------------------------------------------------------------


class TestBlockUnblock:
    def test_block_phase(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.block_phase("Waiting for approval")
        assert state is not None
        assert state.status == "blocked"
        assert state.blocked_by == "Waiting for approval"

    def test_unblock_phase(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        mgr = CurrentPhaseManager(tmp_path)
        mgr.block_phase("blocker")
        state = mgr.unblock_phase()
        assert state is not None
        assert state.status == "active"
        assert state.blocked_by == "~"

    def test_complete_phase(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.complete_phase()
        assert state is not None
        assert state.status == "completed"

    def test_block_returns_none_when_no_file(self, tmp_path):
        mgr = CurrentPhaseManager(tmp_path)
        assert mgr.block_phase("reason") is None

    def test_complete_returns_none_when_no_file(self, tmp_path):
        mgr = CurrentPhaseManager(tmp_path)
        assert mgr.complete_phase() is None


# ---------------------------------------------------------------------------
# CurrentPhaseManager.advance_phase
# ---------------------------------------------------------------------------


class TestAdvancePhase:
    _NEXT_CONTENT = textwrap.dedent("""\
        ---
        phase: 02
        title: Phase Two
        started: 2025-02-01
        milestone: MVP1
        status: active
        blocked_by: ~
        ---

        ## Active Tasks

        - New task
    """)

    def test_advance_when_no_current(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        next_file = tmp_path / "phase_02.md"
        next_file.write_text(self._NEXT_CONTENT, encoding="utf-8")

        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.advance_phase(next_file, archive_current=False)
        assert state is not None
        assert state.phase == "02"

    def test_advance_replaces_current(self, tmp_path):
        phases_dir = tmp_path / ".navig" / "plans" / "phases"
        _make_phase_file(phases_dir)
        next_file = tmp_path / "phase_02.md"
        next_file.write_text(self._NEXT_CONTENT, encoding="utf-8")

        mgr = CurrentPhaseManager(tmp_path)
        state = mgr.advance_phase(next_file, archive_current=True)
        assert state is not None
        assert state.phase == "02"
