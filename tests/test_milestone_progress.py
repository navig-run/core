"""Tests for navig.plans.milestone_progress."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.milestone_progress import MilestoneProgressEngine, MilestoneState

_MVP1_CONTENT = """\
---
title: Minimum Viable Product
status: active
target_date: 2025-06-01
---

# MVP1 — Minimum Viable Product

## Tasks

- [x] Project scaffolding
- [x] Core inbox reader
- [x] Phase manager
- [ ] Inbox processor
- [ ] Review queue
- [ ] Milestone tracking
- [ ] Corpus scanner
"""

_MVP2_CONTENT = """\
---
title: Enhanced Features
status: blocked
target_date: 2025-09-01
---

# MVP2 — Enhanced Features

## Tasks

- [x] LM integration
- [ ] Auto-reconciliation
- [ ] Dashboard UI
"""


@pytest.fixture()
def milestone_tree(tmp_path: Path) -> Path:
    """Create .navig/plans/milestones/ with sample milestones."""
    ms_dir = tmp_path / ".navig" / "plans" / "milestones"
    ms_dir.mkdir(parents=True)
    (ms_dir / "MVP1.md").write_text(_MVP1_CONTENT, encoding="utf-8")
    (ms_dir / "MVP2.md").write_text(_MVP2_CONTENT, encoding="utf-8")
    return tmp_path


def test_list_milestones(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    milestones = engine.list_milestones()
    assert len(milestones) == 2
    names = [m.name for m in milestones]
    assert "MVP1" in names
    assert "MVP2" in names


def test_milestone_checkbox_counts(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    mvp1 = engine.get_milestone("MVP1")
    assert mvp1 is not None
    assert mvp1.done_count == 3
    assert mvp1.total_count == 7


def test_milestone_progress_pct(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    mvp1 = engine.get_milestone("MVP1")
    assert mvp1 is not None
    expected = round((3 / 7) * 100, 1)
    assert mvp1.progress_pct == expected


def test_milestone_status(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    mvp2 = engine.get_milestone("MVP2")
    assert mvp2 is not None
    assert mvp2.status == "blocked"


def test_render_strip_active(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    mvp1 = engine.get_milestone("MVP1")
    assert mvp1 is not None
    strip = engine.render_strip(mvp1, width=10)
    assert "✓" in strip
    assert "●" in strip
    assert "MVP1" in strip


def test_render_strip_blocked(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    mvp2 = engine.get_milestone("MVP2")
    assert mvp2 is not None
    strip = engine.render_strip(mvp2, width=10)
    assert "⚠" in strip
    assert "MVP2" in strip


def test_render_all(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    output = engine.render_all(width=10)
    assert "MVP1" in output
    assert "MVP2" in output
    lines = output.strip().splitlines()
    assert len(lines) == 2


def test_get_milestone_case_insensitive(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    ms = engine.get_milestone("mvp1")
    assert ms is not None
    assert ms.name == "MVP1"


def test_get_milestone_missing(milestone_tree: Path) -> None:
    engine = MilestoneProgressEngine(milestone_tree)
    assert engine.get_milestone("NONEXISTENT") is None


def test_list_milestones_empty(tmp_path: Path) -> None:
    engine = MilestoneProgressEngine(tmp_path)
    assert engine.list_milestones() == []


def test_render_strip_no_tasks(tmp_path: Path) -> None:
    ms_dir = tmp_path / ".navig" / "plans" / "milestones"
    ms_dir.mkdir(parents=True)
    (ms_dir / "EMPTY.md").write_text(
        "---\ntitle: Empty\nstatus: active\n---\n\nNo checkboxes here.\n",
        encoding="utf-8",
    )
    engine = MilestoneProgressEngine(tmp_path)
    ms = engine.get_milestone("EMPTY")
    assert ms is not None
    strip = engine.render_strip(ms, width=10)
    assert "no tasks" in strip


def test_milestone_zero_total_pct() -> None:
    ms = MilestoneState(
        name="X",
        title="X",
        status="active",
        target_date="",
        done_count=0,
        total_count=0,
        source_path=Path("/fake"),
    )
    assert ms.progress_pct == 0.0
