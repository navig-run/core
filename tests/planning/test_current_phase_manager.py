"""Tests for navig.plans.current_phase_manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.current_phase_manager import CurrentPhaseManager, PhaseState

pytestmark = pytest.mark.integration

_PHASE_CONTENT = """\
---
phase: 01
title: Bootstrap
started: 2025-01-15
milestone: MVP1
status: active
blocked_by: ~
---

# Phase 01 — Bootstrap

## Active Tasks

- Set up project scaffolding
- Define VISION.md
- Create initial ROADMAP.md

## Notes

Some bootstrap notes.
"""


@pytest.fixture()
def phase_tree(tmp_path: Path) -> Path:
    """Create a ``.navig/plans/phases/`` tree with CURRENT_PHASE.md."""
    phases_dir = tmp_path / ".navig" / "plans" / "phases"
    phases_dir.mkdir(parents=True)
    (phases_dir / "CURRENT_PHASE.md").write_text(_PHASE_CONTENT, encoding="utf-8")
    return tmp_path


def test_get_current_phase(phase_tree: Path) -> None:
    mgr = CurrentPhaseManager(phase_tree)
    phase = mgr.get_current_phase()
    assert phase is not None
    assert phase.phase == "01"
    assert phase.title == "Bootstrap"
    assert phase.milestone == "MVP1"
    assert phase.status == "active"
    assert phase.blocked_by == "~"
    assert len(phase.active_tasks) == 3
    assert "Set up project scaffolding" in phase.active_tasks


def test_get_current_phase_missing(tmp_path: Path) -> None:
    mgr = CurrentPhaseManager(tmp_path)
    assert mgr.get_current_phase() is None


def test_get_current_phase_fallback(tmp_path: Path) -> None:
    """Falls back to .navig/CURRENT_PHASE.md when phases/ subdir missing."""
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True)
    (navig_dir / "CURRENT_PHASE.md").write_text(_PHASE_CONTENT, encoding="utf-8")

    mgr = CurrentPhaseManager(tmp_path)
    phase = mgr.get_current_phase()
    assert phase is not None
    assert phase.phase == "01"


def test_block_phase(phase_tree: Path) -> None:
    mgr = CurrentPhaseManager(phase_tree)
    result = mgr.block_phase("waiting for API keys")
    assert result is not None
    assert result.status == "blocked"
    assert result.blocked_by == "waiting for API keys"


def test_unblock_phase(phase_tree: Path) -> None:
    mgr = CurrentPhaseManager(phase_tree)
    mgr.block_phase("some reason")
    result = mgr.unblock_phase()
    assert result is not None
    assert result.status == "active"
    assert result.blocked_by == "~"


def test_complete_phase(phase_tree: Path) -> None:
    mgr = CurrentPhaseManager(phase_tree)
    result = mgr.complete_phase()
    assert result is not None
    assert result.status == "completed"


def test_advance_phase(phase_tree: Path) -> None:
    """Advance to a new phase file."""
    next_phase = phase_tree / "next_phase.md"
    next_phase.write_text(
        "---\nphase: 02\ntitle: Build\nstarted: 2025-02-01\n"
        "milestone: MVP1\nstatus: active\nblocked_by: ~\n---\n\n"
        "# Phase 02\n\n## Active Tasks\n\n- Build feature A\n",
        encoding="utf-8",
    )

    mgr = CurrentPhaseManager(phase_tree)
    result = mgr.advance_phase(next_phase)
    assert result is not None
    assert result.phase == "02"
    assert result.title == "Build"

    # Old phase should be archived
    phases_dir = phase_tree / ".navig" / "plans" / "phases"
    archives = list(phases_dir.glob("*.archive"))
    assert len(archives) == 1


def test_advance_phase_no_existing(tmp_path: Path) -> None:
    """Advance when there is no existing phase file."""
    next_phase = tmp_path / "first_phase.md"
    next_phase.write_text(
        "---\nphase: 01\ntitle: Start\nstarted: 2025-01-01\n"
        "milestone: MVP1\nstatus: active\nblocked_by: ~\n---\n\n"
        "# Phase 01\n",
        encoding="utf-8",
    )

    mgr = CurrentPhaseManager(tmp_path)
    result = mgr.advance_phase(next_phase)
    assert result is not None
    assert result.phase == "01"


def test_active_tasks_parsing(phase_tree: Path) -> None:
    """Active tasks are bullet items under ## Active Tasks heading."""
    mgr = CurrentPhaseManager(phase_tree)
    phase = mgr.get_current_phase()
    assert phase is not None
    assert "Define VISION.md" in phase.active_tasks
    assert "Create initial ROADMAP.md" in phase.active_tasks
    # Notes section should not leak into active tasks
    assert "Some bootstrap notes." not in phase.active_tasks
