"""`navig dock status|unpair` — the CLI view of the pairing, honest in every state."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.dock import dock_app

runner = CliRunner()


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLUMNS", "120")
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


def _json_status() -> dict:
    res = runner.invoke(dock_app, ["status", "--json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_status_json_before_pairing(isolated_config):
    out = _json_status()
    assert out["paired"] == [] and out["pairing_readable"] is True
    assert out["pairing_file"].endswith("dock_pairing.json")
    assert out["daemon"] is None, "no daemon answering must not fake a connection"
    # the plain table renders too (house style: a state + a next-step nudge)
    plain = runner.invoke(dock_app, ["status"])
    assert plain.exit_code == 0
    assert "not paired" in plain.stdout


def test_pair_status_unpair_cycle(isolated_config):
    from navig.dock import pairing

    ext = "c" * 32
    pairing.pair(ext, "NAVIG Dock")
    out = _json_status()
    assert [p["extension_id"] for p in out["paired"]] == [ext]
    assert out["paired"][0]["name"] == "NAVIG Dock"

    res = runner.invoke(dock_app, ["unpair", "--yes"])
    assert res.exit_code == 0
    assert pairing.load() == ()
    assert _json_status()["paired"] == []


def test_unpair_without_yes_asks_first(isolated_config):
    from navig.dock import pairing

    pairing.pair("d" * 32)
    res = runner.invoke(dock_app, ["unpair"])  # no --yes, stdin empty → declined
    assert res.exit_code != 0
    assert len(pairing.load() or ()) == 1, "declining must not forget the pairing"
