from pathlib import Path

from typer.testing import CliRunner

from navig.commands.space import space_app
from navig.commands.start import app as start_app
from navig.spaces.kickoff import build_space_kickoff

runner = CliRunner()


class _FakeConfigManager:
    def __init__(self, base: Path):
        self.global_config_dir = str(base)
        self.global_config = {}


def test_build_space_kickoff_collects_top_actions(tmp_path):
    space = tmp_path / "space"
    space.mkdir(parents=True, exist_ok=True)
    (space / "VISION.md").write_text("---\ngoal: Ship premium onboarding\n---\n", encoding="utf-8")
    (space / "CURRENT_PHASE.md").write_text(
        "- [ ] Wire navigation shell\n- [ ] Polish copy and micro-UX\n",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    plans = repo / ".navig" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "DEV_PLAN.md").write_text("- [ ] Add kickoff smoke tests\n", encoding="utf-8")

    kickoff = build_space_kickoff("focus", space, cwd=repo, max_items=3)
    assert kickoff.goal == "Ship premium onboarding"
    assert kickoff.actions == [
        "Wire navigation shell",
        "Polish copy and micro-UX",
        "Add kickoff smoke tests",
    ]


def test_space_switch_prints_top_next_actions(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    space_path = global_cfg / "spaces" / "focus"
    space_path.mkdir(parents=True, exist_ok=True)
    (space_path / "VISION.md").write_text("# Focus Goal\n", encoding="utf-8")
    (space_path / "CURRENT_PHASE.md").write_text("- [ ] Build kickoff flow\n", encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / ".navig" / "plans").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo)

    result = runner.invoke(space_app, ["switch", "focus"])
    assert result.exit_code == 0
    assert "Active space: focus" in result.stdout
    assert "Top next actions:" in result.stdout
    assert "Build kickoff flow" in result.stdout


def test_start_alias_switches_space_and_prints_kickoff(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    space_path = global_cfg / "spaces" / "project"
    space_path.mkdir(parents=True, exist_ok=True)
    (space_path / "VISION.md").write_text("---\ngoal: Launch project\n---\n", encoding="utf-8")
    (space_path / "CURRENT_PHASE.md").write_text("- [ ] Finalize MVP scope\n", encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / ".navig" / "plans").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo)

    result = runner.invoke(start_app, ["project"])
    assert result.exit_code == 0
    assert "Active space: project" in result.stdout
    assert "Top next actions:" in result.stdout
    assert "Finalize MVP scope" in result.stdout
