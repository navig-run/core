"""Hermetic unit tests for navig.plans.scaffold."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.scaffold import _DIRS, _TEMPLATE_FILES, scaffold_plans_structure

# ---------------------------------------------------------------------------
# scaffold_plans_structure
# ---------------------------------------------------------------------------


class TestScaffoldPlansStructure:
    def test_returns_list_of_paths(self, tmp_path):
        created = scaffold_plans_structure(tmp_path)
        assert isinstance(created, list)
        assert all(isinstance(p, Path) for p in created)

    def test_creates_dirs(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        navig_dir = tmp_path / ".navig"
        for rel in _DIRS:
            assert (navig_dir / rel).is_dir(), f"Missing directory: {rel}"

    def test_creates_template_files(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        navig_dir = tmp_path / ".navig"
        for rel_path in _TEMPLATE_FILES:
            assert (navig_dir / rel_path).is_file(), f"Missing file: {rel_path}"

    def test_vision_md_has_frontmatter(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        vision = tmp_path / ".navig" / "plans" / "VISION.md"
        content = vision.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "title" in content

    def test_current_phase_has_phase_field(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        phase = tmp_path / ".navig" / "plans" / "phases" / "CURRENT_PHASE.md"
        content = phase.read_text(encoding="utf-8")
        assert "phase:" in content
        assert "status:" in content

    def test_milestone_template_has_milestone_field(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        milestone_files = list((tmp_path / ".navig" / "plans" / "milestones").iterdir())
        assert len(milestone_files) == 1
        content = milestone_files[0].read_text(encoding="utf-8")
        assert "milestone:" in content

    def test_creates_reconciliation_queue_json(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        queue = tmp_path / ".navig" / "staging" / "reconciliation_queue.json"
        assert queue.is_file()

    def test_idempotent_does_not_overwrite(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        vision = tmp_path / ".navig" / "plans" / "VISION.md"
        vision.write_text("custom content", encoding="utf-8")

        # Second call should not overwrite
        scaffold_plans_structure(tmp_path)
        assert vision.read_text(encoding="utf-8") == "custom content"

    def test_idempotent_returns_empty_on_second_call(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        created_second = scaffold_plans_structure(tmp_path)
        # All files exist, nothing new should be created
        assert created_second == []

    def test_created_paths_are_absolute(self, tmp_path):
        created = scaffold_plans_structure(tmp_path)
        for p in created:
            assert p.is_absolute()

    def test_inbox_dir_created(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        assert (tmp_path / ".navig" / "inbox").is_dir()

    def test_tasks_subdirs_created(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        navig = tmp_path / ".navig"
        assert (navig / "plans" / "tasks" / "active").is_dir()
        assert (navig / "plans" / "tasks" / "done").is_dir()
        assert (navig / "plans" / "tasks" / "review").is_dir()

    def test_decisions_dir_created(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        assert (tmp_path / ".navig" / "plans" / "decisions").is_dir()

    def test_roadmap_has_roadmap_title(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        roadmap = tmp_path / ".navig" / "plans" / "ROADMAP.md"
        content = roadmap.read_text(encoding="utf-8")
        assert "Roadmap" in content

    def test_spec_md_has_architecture_section(self, tmp_path):
        scaffold_plans_structure(tmp_path)
        spec = tmp_path / ".navig" / "plans" / "SPEC.md"
        content = spec.read_text(encoding="utf-8")
        assert "Architecture" in content


# ---------------------------------------------------------------------------
# _DIRS constant
# ---------------------------------------------------------------------------


class TestDirsConstant:
    def test_is_list(self):
        assert isinstance(_DIRS, list)

    def test_contains_inbox(self):
        assert "inbox" in _DIRS

    def test_contains_staging(self):
        assert "staging" in _DIRS


# ---------------------------------------------------------------------------
# _TEMPLATE_FILES constant
# ---------------------------------------------------------------------------


class TestTemplateFilesConstant:
    def test_is_dict(self):
        assert isinstance(_TEMPLATE_FILES, dict)

    def test_contains_vision(self):
        assert "plans/VISION.md" in _TEMPLATE_FILES

    def test_contains_roadmap(self):
        assert "plans/ROADMAP.md" in _TEMPLATE_FILES

    def test_contains_current_phase(self):
        assert "plans/phases/CURRENT_PHASE.md" in _TEMPLATE_FILES
