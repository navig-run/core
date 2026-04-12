from pathlib import Path

from navig.spaces.briefing import build_spaces_briefing_lines
import pytest

pytestmark = pytest.mark.integration


def _set_global_config_dir(monkeypatch, tmp_path) -> Path:
    config_dir = tmp_path / ".navig-global"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config_dir))
    return config_dir


def test_build_spaces_briefing_lines_empty(tmp_path, monkeypatch):
    _set_global_config_dir(monkeypatch, tmp_path)
    cwd = tmp_path / "repo"
    (cwd / ".navig").mkdir(parents=True, exist_ok=True)

    lines = build_spaces_briefing_lines(cwd=cwd)
    assert lines == ["_No spaces available for briefing._"]


def test_build_spaces_briefing_lines_includes_action_focus(tmp_path, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, tmp_path)
    cwd = tmp_path / "repo"
    (cwd / ".navig").mkdir(parents=True, exist_ok=True)

    project = config_dir / "spaces" / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "VISION.md").write_text("---\ngoal: Ship v2\n---\n", encoding="utf-8")
    (project / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 25\n---\n\n- [ ] Write release checklist\n",
        encoding="utf-8",
    )

    lines = build_spaces_briefing_lines(cwd=cwd)
    joined = "\n".join(lines)
    assert "Spaces Progress:" in joined
    assert "Action Focus:" in joined
    assert "Write release checklist" in joined
