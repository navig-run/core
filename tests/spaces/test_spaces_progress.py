from pathlib import Path

from navig.spaces.progress import collect_spaces_progress, read_space_progress
import pytest

pytestmark = pytest.mark.integration


def test_read_space_progress_from_frontmatter_completion(tmp_path):
    space = tmp_path / "health"
    space.mkdir(parents=True, exist_ok=True)
    (space / "VISION.md").write_text("---\ngoal: Run a marathon\n---\n\n# Vision\n")
    (space / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 62.5\nlast_updated: 2026-03-29\n---\n\n- [x] Week 1\n- [ ] Week 2\n"
    )

    row = read_space_progress("health", space, "global")
    assert row.name == "health"
    assert row.goal == "Run a marathon"
    assert row.completion_pct == 62.5
    assert row.last_updated == "2026-03-29"


def test_read_space_progress_from_checkboxes_when_no_frontmatter_pct(tmp_path):
    space = tmp_path / "project"
    space.mkdir(parents=True, exist_ok=True)
    (space / "VISION.md").write_text("# Ship v2\n")
    (space / "CURRENT_PHASE.md").write_text("- [x] task A\n- [ ] task B\n- [x] task C\n")

    row = read_space_progress("project", space, "project")
    assert row.goal == "Ship v2"
    assert row.completion_pct == 66.7


def test_collect_spaces_progress_project_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    global_space = home / ".navig" / "spaces" / "health"
    global_space.mkdir(parents=True, exist_ok=True)
    (global_space / "VISION.md").write_text("# Global health\n")
    (global_space / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 10\n---\n")

    repo = tmp_path / "repo"
    project_space = repo / ".navig" / "spaces" / "health"
    project_space.mkdir(parents=True, exist_ok=True)
    (project_space / "VISION.md").write_text("# Project health\n")
    (project_space / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 90\n---\n")

    rows = collect_spaces_progress(cwd=repo)
    health = [r for r in rows if r.name == "health"][0]
    assert health.scope == "project"
    assert health.completion_pct == 90.0
