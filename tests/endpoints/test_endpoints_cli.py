"""``navig endpoints`` CLI: Answer envelope, house-style table, idempotent set."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands import endpoints as endpoints_cmd
from navig.endpoints.client import HomeBridge
from navig.endpoints.store import EndpointStore
from navig.ui.answer import ANSWER_WIRE_FIELDS, validate_answer
from tests.endpoints.fixtures import FakeClock, FakeHome, projection_now


def _wire(monkeypatch, home: FakeHome | None = None) -> tuple[FakeHome, EndpointStore]:
    """Point the CLI at a store backed by *home*; return both for assertions."""
    fake = home or FakeHome()
    store = EndpointStore(
        HomeBridge(runner=fake.runner),
        clock=FakeClock(),
        wall_clock=projection_now,
    )
    monkeypatch.setattr(endpoints_cmd, "_make_store", lambda: store)
    return fake, store


def _invoke(args: list[str]):
    return CliRunner().invoke(endpoints_cmd.endpoints_app, list(args))


def test_the_verb_is_registered_and_has_three_commands() -> None:
    from navig.cli.registration import is_external_command

    assert is_external_command("endpoints")
    names = {command.name for command in endpoints_cmd.endpoints_app.registered_commands}
    assert names == {"list", "show", "set"}


def _all_keys(value) -> set[str]:
    """Every mapping key anywhere in *value* — the leak check for query payloads."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found |= _all_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _all_keys(item)
    return found


def test_list_json_is_one_answer_envelope_document(monkeypatch) -> None:
    _wire(monkeypatch)
    result = _invoke(["list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)  # exactly one document
    assert validate_answer(payload) == []
    assert set(payload) == set(ANSWER_WIRE_FIELDS)
    assert payload["schema_version"] == "1"
    assert payload["ok"] is True
    assert payload["data"]["count"] == 2
    assert payload["data"]["providers"] == ["govee", "ha"]
    assert payload["json"]["count"] == 2
    assert {endpoint["id"] for endpoint in payload["json"]["endpoints"]} == {
        "govee:desk-lamp",
        "ha:living-room-ac",
    }
    assert payload["json"]["unmapped"] == {"total": 0, "by_capability": {}}
    assert payload["json"]["reachability"] == {"govee": True, "ha": True, "tuya": False}
    assert all(
        isinstance(ready, bool) for ready in payload["json"]["reachability"].values()
    ), "provider readiness surfaces as booleans — never credentials or health detail"
    keys = _all_keys(payload["json"]["endpoints"])
    for forbidden in ("auth", "permissions", "credentials", "token_env", "address",
                      "entity_id", "dev_id", "mac", "dps", "scale", "ir"):
        assert forbidden not in keys, forbidden
    for endpoint in payload["json"]["endpoints"]:
        from navig.endpoints.model import validate_endpoint

        assert validate_endpoint(endpoint) == []
        for capability in endpoint["capabilities"]:
            assert capability["meta"]["reportable"] is False
            assert capability["meta"]["supported_events"] == []


def test_list_filters_reach_the_projection(monkeypatch) -> None:
    _wire(monkeypatch)
    payload = json.loads(_invoke(["list", "--provider", "ha", "--json"]).output)
    assert payload["data"]["count"] == 1
    assert payload["json"]["endpoints"][0]["id"] == "ha:living-room-ac"
    room = json.loads(_invoke(["list", "--room", "office", "--json"]).output)
    assert room["json"]["endpoints"][0]["id"] == "govee:desk-lamp"


def test_list_human_prints_the_house_table_and_a_next_step(monkeypatch) -> None:
    _wire(monkeypatch)
    result = _invoke(["list"])
    assert result.exit_code == 0
    assert "Endpoint" in result.output
    assert "govee:desk-lamp" in result.output
    assert "color:rgb" in result.output
    assert "color:temperature_k" in result.output
    assert "online" in result.output
    assert "[red]" not in result.output and "[green]" not in result.output, (
        "table cells carry colour via the renderer, never literal Rich markup")
    assert "next:" in result.output


def test_an_empty_inventory_degrades_honestly(monkeypatch) -> None:
    _wire(monkeypatch, FakeHome(devices=[], states=[], providers=[]))
    human = _invoke(["list"])
    assert human.exit_code == 0
    assert "empty" in human.output.lower()
    assert "navig home import" in human.output
    payload = json.loads(_invoke(["list", "--json"]).output)
    assert payload["data"]["count"] == 0
    assert payload["table"] is None


def test_show_json_lists_capabilities_and_directives(monkeypatch) -> None:
    _wire(monkeypatch)
    payload = json.loads(_invoke(["show", "govee:desk-lamp", "--json"]).output)
    assert validate_answer(payload) == []
    assert payload["ok"] is True
    capabilities = payload["json"]["endpoint"]["capabilities"]
    types = {capability["type"] for capability in capabilities}
    assert "devices.capabilities.on_off" in types
    assert "devices.capabilities.range" in types
    assert set(payload["json"]["directives"]) == {
        "on_off", "toggle", "set_range", "set_color"}
    assert payload["json"]["directives"] == sorted(payload["json"]["directives"])


def test_show_unknown_answers_with_a_hint(monkeypatch) -> None:
    _wire(monkeypatch)
    result = _invoke(["show", "govee:missing", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "navig endpoints list" in payload["data"]["hint"]


def test_set_json_is_idempotent_across_two_runs(monkeypatch) -> None:
    home, _store = _wire(monkeypatch)
    first = json.loads(_invoke(["set", "govee:desk-lamp", "on_off", "on", "--json"]).output)
    assert first["ok"] is True
    assert first["json"]["changed"] is True
    second = json.loads(
        _invoke(["set", "govee:desk-lamp", "on_off", "on", "--json"]).output)
    assert second["json"]["changed"] is False
    assert home.action_calls == 1
    assert validate_answer(first) == [] and validate_answer(second) == []


def test_set_human_reports_the_change(monkeypatch) -> None:
    _wire(monkeypatch)
    result = _invoke(["set", "govee:desk-lamp", "set_range", "40"])
    assert result.exit_code == 0
    assert "govee:desk-lamp" in result.output
    assert "next:" in result.output


def test_set_rejects_an_unknown_directive_as_usage_error(monkeypatch) -> None:
    _wire(monkeypatch)
    result = _invoke(["set", "govee:desk-lamp", "set_volume", "5", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "on_off" in payload["data"]["hint"]


def test_set_rejects_a_bad_value_as_usage_error(monkeypatch) -> None:
    home, _store = _wire(monkeypatch)
    result = _invoke(["set", "govee:desk-lamp", "on_off", "maybe", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["ok"] is False
    assert home.action_calls == 0


def test_an_unreachable_plugin_answers_with_the_install_hint(monkeypatch) -> None:
    home = FakeHome()
    home.fail["list"] = (2, "no such command: home")
    _wire(monkeypatch, home)
    result = _invoke(["list", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "navig plugin install navig-home" in payload["data"]["hint"]
    human = _invoke(["list"])
    assert human.exit_code == 1
    assert "navig plugin install navig-home" in human.output


def test_a_provider_failure_answers_with_the_readiness_hint(monkeypatch) -> None:
    home = FakeHome()
    home.fail["on"] = (
        1,
        "the ha provider cannot control 'desk-lamp': not configured set HA_URL",
    )
    _wire(monkeypatch, home)
    result = _invoke(["set", "govee:desk-lamp", "on_off", "on", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "navig home status" in payload["data"]["hint"]


@pytest.mark.parametrize("args", [
    ["list", "--json"],
    ["show", "govee:desk-lamp", "--json"],
    ["set", "govee:desk-lamp", "on_off", "off", "--json"],
])
def test_every_json_command_emits_a_valid_answer(monkeypatch, args) -> None:
    _wire(monkeypatch)
    result = _invoke(args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert validate_answer(payload) == []
    assert set(payload) == set(ANSWER_WIRE_FIELDS)
    assert payload["schema_version"] == "1"
