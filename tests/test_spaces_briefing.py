from pathlib import Path

from navig.spaces.briefing import build_spaces_briefing_lines


def test_build_spaces_briefing_lines_empty(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    lines = build_spaces_briefing_lines(cwd=tmp_path / "repo")
    assert lines == ["_No spaces available for briefing._"]


def test_build_spaces_briefing_lines_includes_action_focus(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    project = home / ".navig" / "spaces" / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "VISION.md").write_text("---\ngoal: Ship v2\n---\n", encoding="utf-8")
    (project / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 25\n---\n\n- [ ] Write release checklist\n",
        encoding="utf-8",
    )

    lines = build_spaces_briefing_lines(cwd=tmp_path / "repo")
    joined = "\n".join(lines)
    assert "Spaces Progress:" in joined
    assert "Action Focus:" in joined
    assert "Write release checklist" in joined
