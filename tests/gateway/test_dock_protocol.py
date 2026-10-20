"""The Dock bridge vocabulary: one validator for the side panel and outside agents."""

from __future__ import annotations

import pytest

from navig.agent.browser_turn import validate_actions
from navig.dock.protocol import ACT_VERBS, DockCallError, call_id, validate_call


def _act(*steps, **extra):
    return validate_call("act", {"tab_id": 3, "actions": list(steps), **extra})


def test_locators_are_targets():
    out = _act({"action": "click", "role": "button", "name": "Save"},
               {"action": "fill", "testid": "email", "value": "a@b.c"},
               {"action": "fill", "role": "checkbox", "name": "Terms", "value": True},
               {"action": "hover", "css": "#menu"},
               {"action": "click", "has_text": "Next", "nth": 2})
    assert [a["action"] for a in out["actions"]] == ["click", "fill", "fill", "hover", "click"]
    assert out["actions"][2]["value"] is True
    assert out["actions"][4]["nth"] == 2


def test_a_click_needs_a_target():
    with pytest.raises(DockCallError) as exc:
        _act({"action": "click", "name": "Save"})
    assert exc.value.category == "invalid"


def test_name_goes_with_role():
    checked = validate_actions([{"action": "click", "css": "a", "name": "x"}])
    assert checked.errors and "name goes with role" in checked.errors[0]


def test_wait_for_and_extract_are_clamped():
    out = _act({"action": "wait_for", "has_text": "Done", "timeout_ms": 999_999},
               {"action": "extract", "css": "li", "max": 10_000},
               {"action": "click", "role": "link", "nth": 999})
    assert out["actions"][0]["timeout_ms"] == 15_000
    assert out["actions"][1]["max"] == 200
    assert out["actions"][2]["nth"] == 50


def test_wait_for_needs_something_to_wait_for():
    with pytest.raises(DockCallError):
        _act({"action": "wait_for", "timeout_ms": 100})


def test_oversized_selectors_are_refused():
    with pytest.raises(DockCallError):
        _act({"action": "click", "css": "a" * 501})


def test_conversation_verbs_are_not_bridge_verbs():
    assert "done" not in ACT_VERBS and "ask_user" not in ACT_VERBS
    with pytest.raises(DockCallError):
        _act({"action": "ask_user", "question": "?"})


def test_observe_diff_and_snapshot_since():
    assert _act({"action": "key", "keys": "Tab"}, observe="diff")["observe"] == "diff"
    assert _act({"action": "key", "keys": "Tab"}, observe=0)["observe"] is False
    assert validate_call("snapshot", {"tab_id": 1, "since": 4})["since"] == 4
    with pytest.raises(DockCallError):
        validate_call("snapshot", {"tab_id": 1, "since": "4"})


def test_caller_chosen_call_ids():
    assert call_id("0123456789abcdef") == "0123456789abcdef"
    assert call_id("not-hex-at-all!!") is None
    assert call_id(None) is None
    assert call_id("0123") is None
