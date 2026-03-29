from pathlib import Path

from navig.spaces.next_action import (
    build_continuation_prompt,
    first_pending_task,
    select_best_next_action,
)


def test_first_pending_task_extracts_checkbox_text():
    text = "- [x] done\n- [ ] Ship milestone\n"
    assert first_pending_task(text) == "Ship milestone"


def test_select_best_next_action_prefers_lowest_progress(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    low = home / ".navig" / "spaces" / "project"
    low.mkdir(parents=True, exist_ok=True)
    (low / "VISION.md").write_text("---\ngoal: Launch v2\n---\n", encoding="utf-8")
    (low / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 10\n---\n\n- [ ] Draft release checklist\n", encoding="utf-8")

    high = home / ".navig" / "spaces" / "health"
    high.mkdir(parents=True, exist_ok=True)
    (high / "VISION.md").write_text("---\ngoal: Strong body\n---\n", encoding="utf-8")
    (high / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 70\n---\n\n- [ ] Gym session\n", encoding="utf-8")

    action = select_best_next_action(cwd=tmp_path / "repo")
    assert action is not None
    assert action.space == "project"
    assert action.next_task == "Draft release checklist"


def test_build_continuation_prompt_includes_space_goal_task(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    space = home / ".navig" / "spaces" / "finance"
    space.mkdir(parents=True, exist_ok=True)
    (space / "VISION.md").write_text("---\ngoal: Build emergency fund\n---\n", encoding="utf-8")
    (space / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 33\n---\n\n- [ ] Move 10% salary to savings\n", encoding="utf-8")

    prompt = build_continuation_prompt(preferred_space="finance", cwd=tmp_path / "repo")
    assert "finance" in prompt
    assert "Build emergency fund" in prompt
    assert "Move 10% salary to savings" in prompt
