from pathlib import Path

from typer.testing import CliRunner

from navig.commands.space import space_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


class _FakeConfigManager:
    def __init__(self, base: Path, global_config: dict | None = None):
        self.global_config_dir = str(base)
        self.global_config = global_config or {}


def test_space_create_switch_current_list_and_delete(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    created = runner.invoke(space_app, ["create", "work"])
    assert created.exit_code == 0
    assert (global_cfg / "spaces" / "work").is_dir()

    switched = runner.invoke(space_app, ["use", "work"])
    assert switched.exit_code == 0
    assert "Active space: work" in switched.stdout

    current = runner.invoke(space_app, ["current"])
    assert current.exit_code == 0
    assert "Active space: work" in current.stdout

    listed = runner.invoke(space_app, ["list"])
    assert listed.exit_code == 0
    assert "work" in listed.stdout

    deleted = runner.invoke(space_app, ["delete", "work", "--yes"])
    assert deleted.exit_code == 0
    assert not (global_cfg / "spaces" / "work").exists()

    current_after = runner.invoke(space_app, ["current"])
    assert current_after.exit_code == 0
    assert (
        "default" in current_after.stdout
    )  # displayed as "My Space (default)" or "Active space: default"


def test_space_create_invalid_slug_shows_builtin_hint(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    result = runner.invoke(space_app, ["create", "Bad_Name"])
    assert result.exit_code != 0
    combined = (
        (result.stdout or "")
        + (getattr(result, "stderr", "") or "")
        + (getattr(result, "output", "") or "")
    )
    assert "Invalid space name" in combined
    assert "Common spaces" in combined


def test_space_current_prefers_env_var(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg, {"space": {"active": "work"}})
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)
    monkeypatch.setenv("NAVIG_SPACE", "focus")

    result = runner.invoke(space_app, ["current"])
    assert result.exit_code == 0
    assert "Active space: focus" in result.stdout
