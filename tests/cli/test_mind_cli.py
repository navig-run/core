"""CLI tests for ``navig mind`` — the NAVIG MIND preference command."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.mind import mind_app

runner = CliRunner()


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Fresh ConfigManager pointed at an isolated NAVIG_CONFIG_DIR.

    Mirrors ``tests/config/test_goal_config.py`` — the ConfigManager singleton's
    cached path outlives a ``NAVIG_CONFIG_DIR`` change, so it is reset on both
    sides of the test (and the shared ConfigSingleton, for any legacy reader).
    """
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None, raising=False)
    yield cfg_dir
    reset_config_manager()
    ConfigSingleton._instance = None


def test_status_json_shows_profile_and_firewall(isolated_config) -> None:
    result = runner.invoke(mind_app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["enabled"] is True
    assert data["creative_language"] == "ru"
    assert data["engineering_language"] == "en"
    assert data["commentary_language"] == "ru"
    # The firewall is always reported and never disableable.
    assert data["artifact_language_firewall"] is True


def test_status_plain_renders_a_table(isolated_config) -> None:
    result = runner.invoke(mind_app, ["status"])
    assert result.exit_code == 0, result.output
    assert "NAVIG MIND" in result.stdout
    assert "Artifact firewall" in result.stdout


def test_lexicon_and_digitalghost_persist(isolated_config) -> None:
    assert runner.invoke(mind_app, ["lexicon", "intensive"]).exit_code == 0
    assert runner.invoke(mind_app, ["digitalghost", "deep"]).exit_code == 0
    data = json.loads(runner.invoke(mind_app, ["status", "--json"]).stdout)
    assert data["lexicon"]["density"] == "intensive"
    assert data["lexicon"]["enabled"] is True
    assert data["digitalghost"]["frequency"] == "deep"
    assert data["digitalghost"]["enabled"] is True


def test_on_off_toggles_the_layer(isolated_config) -> None:
    assert runner.invoke(mind_app, ["off"]).exit_code == 0
    data = json.loads(runner.invoke(mind_app, ["status", "--json"]).stdout)
    assert data["enabled"] is False
    assert runner.invoke(mind_app, ["on"]).exit_code == 0
    data = json.loads(runner.invoke(mind_app, ["status", "--json"]).stdout)
    assert data["enabled"] is True


def test_rejects_unknown_values(isolated_config) -> None:
    assert runner.invoke(mind_app, ["lexicon", "bogus"]).exit_code == 2
    assert runner.invoke(mind_app, ["digitalghost", "bogus"]).exit_code == 2
