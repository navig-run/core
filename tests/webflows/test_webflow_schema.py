"""navig.webflow/1 validation: the vocabulary, the param rules, origin pinning, the
secret rules, and round-trip stability."""

from __future__ import annotations

import copy
import json

import pytest

from navig.webflows import (
    SCHEMA,
    WebflowError,
    dumps,
    loads,
    origin_of,
    resolve_params,
    substitute,
    validate_webflow,
)

ORIGIN = "http://127.0.0.1:8123"


def flow(**over) -> dict:
    doc = {
        "schema": SCHEMA,
        "name": "Place an order",
        "origins": [ORIGIN],
        "start_url": f"{ORIGIN}/order.html",
        "params": [
            {"name": "customer", "type": "string", "label": "Customer"},
            {"name": "password", "type": "secret"},
        ],
        "steps": [
            {"action": "fill", "role": "textbox", "name": "Full name", "value": "{{customer}}",
             "fallbacks": [{"css": "#name"}]},
            {"action": "select", "role": "combobox", "name": "Plan", "value": "pro"},
            {"action": "fill", "css": "#pw", "value": "{{password}}", "sensitive": True},
            {"action": "click", "role": "button", "name": "Next", "expect": {"url_contains": "/confirm"}},
            {"action": "assert", "has_text": "Order confirmed"},
        ],
    }
    doc.update(over)
    return doc


def test_valid_flow_normalizes() -> None:
    v = validate_webflow(flow())
    assert v.ok, v.errors
    steps = v.doc["steps"]
    assert [s["id"] for s in steps] == ["s1", "s2", "s3", "s4", "s5"]
    assert steps[0]["fallbacks"] == [{"css": "#name"}]
    assert steps[2]["sensitive"] is True
    assert steps[3]["expect"] == {"url_contains": "/confirm"}
    assert v.doc["params"][0]["required"] is True


def test_round_trip_is_stable_and_idempotent() -> None:
    once = validate_webflow(flow()).doc
    twice = validate_webflow(copy.deepcopy(once)).doc
    assert once == twice
    text = dumps(once)
    assert dumps(loads(text)) == text


def test_unknown_fields_are_dropped() -> None:
    raw = flow()
    raw["cookies"] = "session=abc"
    raw["steps"][0]["innerHTML"] = "<b>x</b>"
    doc = validate_webflow(raw).doc
    assert "cookies" not in doc and "innerHTML" not in doc["steps"][0]


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["steps"].append({"action": "eval_js", "code": "1"}), "unknown action"),
        (lambda d: d["steps"].append({"action": "click"}), "needs one of"),
        (lambda d: d["steps"].append({"action": "fill", "css": "#x", "value": "{{nope}}"}), "not a declared param"),
        (lambda d: d["steps"].append({"action": "navigate", "url": "https://evil.example/"}), "not one of this flow's origins"),
        (lambda d: d["steps"].append({"action": "navigate", "url": "http://{{customer}}/x"}), "cannot choose the site"),
        (lambda d: d["steps"].append({"action": "navigate", "url": f"{ORIGIN}/?pw={{{{password}}}}"}), "secret param cannot go into a URL"),
        (lambda d: d["steps"].append({"action": "navigate", "url": "javascript:alert(1)"}), "only http(s)"),
        (lambda d: d["steps"].append({"action": "fill", "css": "#pw", "value": "hunter2", "sensitive": True}), "never a literal"),
        (lambda d: d["steps"].append({"action": "fill", "css": "#x", "value": "pw: {{password}}"}), "whole value of a fill"),
        (lambda d: d["steps"].append({"action": "click", "role": "button", "name": "x", "nth": "2"}), "wrong type"),
        (lambda d: d["steps"].append({"action": "click", "name": "Next"}), "needs one of"),
        (lambda d: d["steps"].append({"action": "key", "keys": "Enter; rm -rf"}), "key chord"),
        (lambda d: d["params"].append({"name": "token", "type": "secret", "default": "x"}), "cannot have a default"),
        (lambda d: d["params"].append({"name": "Bad Name"}), "lower_snake_case"),
        (lambda d: d.update(start_url="https://other.example/"), "not one of this flow's origins"),
        (lambda d: d.update(origins=[]), "origins must list"),
        (lambda d: d.update(schema="navig.webflow/9"), "unsupported schema"),
        (lambda d: d.update(steps=[]), "at least one step"),
    ],
)
def test_rejections(mutate, needle) -> None:
    raw = flow()
    mutate(raw)
    v = validate_webflow(raw)
    assert not v.ok and v.doc == {}
    assert any(needle in e for e in v.errors), v.errors


def test_secret_looking_literal_is_refused_not_silently_changed() -> None:
    raw = flow()
    raw["steps"].append({"action": "fill", "css": "#k", "value": "sk-ant-api03-" + "a" * 80})
    v = validate_webflow(raw)
    assert any("looks like a secret" in e for e in v.errors), v.errors


def test_a_secret_ref_makes_the_step_sensitive() -> None:
    raw = flow()
    raw["steps"][2].pop("sensitive")
    assert validate_webflow(raw).doc["steps"][2]["sensitive"] is True


def test_origin_normalization() -> None:
    assert origin_of("HTTPS://Example.COM:443/a?b") == "https://example.com"
    assert origin_of("http://127.0.0.1:8123/x") == "http://127.0.0.1:8123"
    assert origin_of("file:///etc/passwd") is None
    doc = validate_webflow(flow(origins=["http://127.0.0.1:8123/whatever", ORIGIN])).doc
    assert doc["origins"] == [ORIGIN]


def test_timeouts_are_clamped() -> None:
    raw = flow()
    raw["steps"][4]["timeout_ms"] = 10**9
    raw["steps"][3]["expect"]["timeout_ms"] = 1
    doc = validate_webflow(raw).doc
    assert doc["steps"][4]["timeout_ms"] == 60_000
    assert doc["steps"][3]["expect"]["timeout_ms"] == 100


def test_params_resolution_and_substitution() -> None:
    doc = validate_webflow(flow()).doc
    with pytest.raises(WebflowError, match="customer"):
        resolve_params(doc, {"password": "x"})
    vals = resolve_params(doc, {"customer": "Ada", "password": "s3cret"})
    assert substitute("{{customer}}", vals) == "Ada"
    assert substitute("Dear {{ customer }}!", vals) == "Dear Ada!"
    assert substitute(True, vals) is True


def test_loads_rejects_non_json() -> None:
    with pytest.raises(WebflowError, match="not JSON"):
        loads("{nope")
    with pytest.raises(WebflowError):
        loads(json.dumps({"schema": SCHEMA}))
