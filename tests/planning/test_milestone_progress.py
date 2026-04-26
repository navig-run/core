"""Hermetic unit tests for navig.plans.milestone_progress."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.milestone_progress import (
    MilestoneProgressEngine,
    MilestoneState,
    _count_checkboxes,
)

# ---------------------------------------------------------------------------
# _count_checkboxes — pure regex
# ---------------------------------------------------------------------------


class TestCountCheckboxes:
    def test_empty_text(self):
        done, total = _count_checkboxes("")
        assert done == 0 and total == 0

    def test_only_done(self):
        text = "- [x] task one\n- [X] task two\n"
        done, total = _count_checkboxes(text)
        assert done == 2 and total == 2

    def test_only_pending(self):
        text = "- [ ] task a\n- [ ] task b\n- [ ] task c\n"
        done, total = _count_checkboxes(text)
        assert done == 0 and total == 3

    def test_mixed(self):
        text = "- [x] done\n- [ ] pending\n- [X] also done\n"
        done, total = _count_checkboxes(text)
        assert done == 2 and total == 3

    def test_bullet_star(self):
        text = "* [x] done with star\n* [ ] pending with star\n"
        done, total = _count_checkboxes(text)
        assert done == 1 and total == 2

    def test_no_checkboxes(self):
        text = "# Header\n\nSome prose without any tasks.\n"
        done, total = _count_checkboxes(text)
        assert done == 0 and total == 0


# ---------------------------------------------------------------------------
# MilestoneState.progress_pct
# ---------------------------------------------------------------------------


def _make_state(done: int, total: int, status: str = "active") -> MilestoneState:
    return MilestoneState(
        name="MVP1",
        title="MVP 1",
        status=status,
        target_date="2025-12-31",
        done_count=done,
        total_count=total,
        source_path=Path("/fake/MVP1.md"),
    )


class TestMilestoneStateProgressPct:
    def test_zero_total_returns_zero(self):
        assert _make_state(0, 0).progress_pct == 0.0

    def test_half_done(self):
        assert _make_state(1, 2).progress_pct == 50.0

    def test_all_done(self):
        assert _make_state(5, 5).progress_pct == 100.0

    def test_rounded_to_one_decimal(self):
        pct = _make_state(1, 3).progress_pct
        assert pct == pytest.approx(33.3, abs=0.1)


# ---------------------------------------------------------------------------
# MilestoneProgressEngine.render_strip
# ---------------------------------------------------------------------------


class TestRenderStrip:
    def _engine(self) -> MilestoneProgressEngine:
        return MilestoneProgressEngine(Path("."))

    def test_no_tasks_message(self):
        ms = _make_state(0, 0)
        result = self._engine().render_strip(ms, width=10)
        assert "no tasks" in result

    def test_all_done_shows_checks(self):
        ms = _make_state(5, 5)
        strip = self._engine().render_strip(ms, width=5)
        assert strip.count("✓") == 5

    def test_blocked_shows_warning_symbol(self):
        ms = _make_state(2, 5, status="blocked")
        strip = self._engine().render_strip(ms, width=5)
        assert "⚠" in strip

    def test_in_progress_shows_bullet(self):
        ms = _make_state(2, 5, status="active")
        strip = self._engine().render_strip(ms, width=5)
        assert "●" in strip

    def test_strip_contains_name_and_pct(self):
        ms = _make_state(1, 2)
        result = self._engine().render_strip(ms, width=4)
        assert "MVP1" in result
        assert "50.0%" in result


# ---------------------------------------------------------------------------
# MilestoneProgressEngine.list_milestones — filesystem-based
# ---------------------------------------------------------------------------


class TestListMilestones:
    def _milestone_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / ".navig" / "plans" / "milestones"
        d.mkdir(parents=True)
        return d

    def _write_milestone(self, d: Path, name: str, content: str) -> None:
        (d / f"{name}.md").write_text(content, encoding="utf-8")

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        self._milestone_dir(tmp_path)
        engine = MilestoneProgressEngine(tmp_path)
        assert engine.list_milestones() == []

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        engine = MilestoneProgressEngine(tmp_path)
        assert engine.list_milestones() == []

    def test_reads_milestone_file(self, tmp_path: Path):
        d = self._milestone_dir(tmp_path)
        self._write_milestone(
            d,
            "MVP1",
            "---\ntitle: MVP 1\nstatus: active\n---\n- [x] done\n- [ ] pending\n",
        )
        engine = MilestoneProgressEngine(tmp_path)
        milestones = engine.list_milestones()
        assert len(milestones) == 1
        ms = milestones[0]
        assert ms.name == "MVP1"
        assert ms.done_count == 1
        assert ms.total_count == 2

    def test_ignores_non_md_files(self, tmp_path: Path):
        d = self._milestone_dir(tmp_path)
        (d / "notes.txt").write_text("some text", encoding="utf-8")
        engine = MilestoneProgressEngine(tmp_path)
        assert engine.list_milestones() == []

    def test_multiple_milestones_sorted(self, tmp_path: Path):
        d = self._milestone_dir(tmp_path)
        for name in ("Z_last", "A_first", "M_mid"):
            self._write_milestone(d, name, f"---\ntitle: {name}\n---\n")
        engine = MilestoneProgressEngine(tmp_path)
        names = [ms.name for ms in engine.list_milestones()]
        assert names == sorted(names)
