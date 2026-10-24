"""Registry-driven guard: every registered Reflex tool produces a valid Answer.

The router (``navig.reflex``) is built in parallel with this layer. This test
activates when it lands: each tool's plan is run through ``Answer.from_plan`` and
the structural validator, and the router's own Answer bridge must resolve to the
canonical class (never a bare dict).
"""

from __future__ import annotations

import pytest

from navig.ui.answer import Answer, validate_answer

reflex_manifest = pytest.importorskip("navig.reflex.manifest")
reflex_bridge = pytest.importorskip("navig.reflex.answer_bridge")
reflex_plans = pytest.importorskip("navig.reflex.plans")

#: The frozen tool-manifest fields from plan §3.2.
MANIFEST_FIELDS = {
    "name",
    "description",
    "command",
    "intents",
    "slots",
    "requiresCommit",
    "risk",
    "contextProviders",
    "enabled",
    "patterns",
    "readOnly",
}


def tools() -> list:
    return list(reflex_manifest.default_registry().all())


def test_the_shipped_registry_is_not_empty() -> None:
    assert len(tools()) >= 10, "the shipped router ships ten tools (plan §3.2)"


def test_every_tool_declares_the_frozen_manifest_contract() -> None:
    for tool in tools():
        manifest = tool.as_dict()
        assert set(manifest) == MANIFEST_FIELDS, tool.name
        assert manifest["description"].strip(), tool.name
        assert manifest["command"], tool.name
        assert manifest["risk"] in reflex_manifest.RISK_LEVELS, tool.name
        assert isinstance(manifest["enabled"], bool), tool.name
        for slot in manifest["slots"]:
            assert set(slot) == {"name", "type", "required"}, tool.name


def test_every_tool_plan_renders_as_a_valid_answer() -> None:
    for tool in tools():
        plan = reflex_plans.CommitPlan(
            token="plan-test12345",
            tool=tool.name,
            command=list(tool.command),
            profile="fast",
            risk=tool.risk,
            read_only=tool.read_only,
            destructive=tool.risk == "destructive",
            targets=[{"endpoint": "govee:desk-lamp", "home_id": "desk-lamp"}],
            blast_radius={"kind": "single", "count": 1,
                          "targets": ["govee:desk-lamp"]},
        )
        answer = Answer.from_plan(plan)
        assert isinstance(answer, Answer), tool.name
        assert validate_answer(answer) == [], tool.name
        assert answer.plan is not None and answer.plan["token"] == "plan-test12345"
        assert bridge_problems(answer) == [], tool.name


def bridge_problems(answer: Answer) -> list[str]:
    return list(reflex_bridge.problems(answer))


def test_the_router_bridge_resolves_the_canonical_answer_class() -> None:
    built = reflex_bridge.make_answer(
        speech="Desk lamp is off.",
        display_text="Desk lamp: off",
        data={"device": "Desk lamp", "state": "off"},
        suggestions=["navig endpoints show govee:desk-lamp"],
    )
    assert isinstance(built, Answer), "the canonical Answer must be importable"
    payload = reflex_bridge.answer_as_dict(built)
    assert payload["schema_version"] == "1"
    assert payload["display_text"] == "Desk lamp: off"
    assert reflex_bridge.problems(built) == []
    replay = reflex_bridge.answer_from_dict(payload)
    assert isinstance(replay, Answer)
    assert validate_answer(replay) == []


def test_a_bare_dict_is_not_a_valid_answer() -> None:
    bare = {"speech": "Desk lamp is off."}
    assert not isinstance(bare, Answer)
    assert reflex_bridge.problems(bare), "a tool returning a bare dict must fail the guard"


def test_from_home_status_matches_a_captured_home_status_payload() -> None:
    payload = {
        "ok": True,
        "count": 2,
        "devices": [
            {"device": "desk-lamp", "name": "Desk lamp", "room": "office",
             "provider": "govee", "ok": True, "state": {"power": "on"}},
            {"device": "living-room-ac", "name": "Living room AC", "room": "living room",
             "provider": "ha", "ok": True, "state": {"power": "off"}},
        ],
        "providers": [
            {"provider": "govee", "ready": True, "detail": "", "hint": ""},
            {"provider": "ha", "ready": False, "detail": "HA_URL unset", "hint": "set it"},
        ],
        "paths": {"devices": "devices.yaml"},
    }
    answer = Answer.from_home_status(payload)
    assert validate_answer(answer) == []
    assert answer.table is not None
    assert answer.table["rows"][1] == ["Living room AC", "living room", "ha", "online"]
    assert answer.data["providers"]["ha"] is False
    assert answer.json == payload
