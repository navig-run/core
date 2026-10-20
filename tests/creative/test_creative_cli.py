"""`navig creative` verbs: registration, honest exit codes, JSON surfaces."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.creative import creative_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    yield
    reset_config_manager()
    ConfigSingleton._instance = None


def test_registered_in_the_lazy_app_map():
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    assert _EXTERNAL_CMD_MAP["creative"] == ("navig.commands.creative", "creative_app")


def test_status_json_shape():
    result = runner.invoke(creative_app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] in ("standard", "creative")
    assert "index" in payload and "proposals" in payload and "publish" in payload
    assert payload["publish"]["mode"] == "local"  # safe default


def test_default_callback_shows_status():
    result = runner.invoke(creative_app, [])
    assert result.exit_code == 0, result.output
    assert "Agent mode" in result.output


def test_list_empty_is_a_friendly_noop():
    result = runner.invoke(creative_app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_index_query_returns_json_entries():
    result = runner.invoke(creative_app, ["index", "--q", "docker", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] >= 0
    assert isinstance(payload["entries"], list)
    if payload["count"]:
        assert all("id" in e and "kind" in e for e in payload["entries"])


def test_unknown_proposal_ids_fail_honestly():
    for verb in ("show", "build", "dismiss", "publish"):
        result = runner.invoke(creative_app, [verb, "deadbeefdeadbeef"])
        assert result.exit_code == 1, (verb, result.output)
        assert "No proposal matches" in result.output


def test_build_refused_in_standard_mode_exits_nonzero():
    # A real proposal, then build in Standard → honest failure.
    from navig.creative import proposals as prop
    from navig.creative.decide import ArtifactSpec, Decision

    _mode("standard")
    d = Decision(
        verdict="create", reason="r", genuine_gap="g", plan=["p"],
        artifact=ArtifactSpec(kind="skill", slug="cli-gate", title="t",
                              description="d", commands=["navig x"]),
        benefit="b", complexity="low", verification="v",
    )
    p = prop.from_decision(d, source="detector:test", problem="R", evidence=[], goal="cli gate")
    created, _ = prop.propose(p)
    assert created

    result = runner.invoke(creative_app, ["build", p["id"]])
    assert result.exit_code == 1
    assert "Standard" in result.output
    assert prop.get_proposal(p["id"])["status"] == "open"


def _mode(mode: str) -> None:
    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", mode) is True


def test_publish_enable_rejects_missing_destination():
    result = runner.invoke(
        creative_app, ["publish-enable", "--repo", "Z:/definitely/not/here", "--yes"]
    )
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_publish_disable_returns_to_local():
    from navig.config import get_config_manager

    get_config_manager().set_global("creative.publish.mode", "repo")
    result = runner.invoke(creative_app, ["publish-disable"])
    assert result.exit_code == 0
    from navig.creative.policy import load_policy

    assert load_policy()["mode"] == "local"


def test_publish_enable_records_the_policy(tmp_path):
    dest = tmp_path / "collector"
    dest.mkdir()
    result = runner.invoke(
        creative_app,
        ["publish-enable", "--repo", str(dest), "--branch", "main", "--yes"],
    )
    assert result.exit_code == 0, result.output
    from navig.creative.policy import load_policy

    policy = load_policy()
    assert policy["mode"] == "repo"
    assert policy["repo"] == str(dest)
    assert str(dest) in policy["repos"]
    assert policy["auto_push"] is False
