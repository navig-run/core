"""Acceptance A — Unified Endpoints (plan §2, §2.5).

Driven only through the public contracts: the in-process ``navig endpoints`` CLI, the pure
``project``/``validate_endpoint`` model, and the documented fake HomeBridge seam.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import _support as S
import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _endpoints_of(projected) -> list[dict]:
    if isinstance(projected, dict):
        for key in ("endpoints", "devices"):
            if isinstance(projected.get(key), list):
                return [S.as_dict(item) for item in projected[key]]
    if isinstance(projected, (list, tuple)):
        return [S.as_dict(item) for item in projected]
    raise AssertionError(f"cannot find an endpoint list in project() output: {projected!r}")


def _envelope_endpoints(envelope: dict) -> list[dict]:
    """Locate the endpoint records inside an Answer-shaped CLI envelope."""
    for candidate in (envelope.get("json"), envelope.get("data"), envelope):
        if isinstance(candidate, list):
            return [S.as_dict(item) for item in candidate]
        if isinstance(candidate, dict):
            for key in ("endpoints", "devices"):
                if isinstance(candidate.get(key), list):
                    return [S.as_dict(item) for item in candidate[key]]
    pytest.fail(
        "endpoints list Answer envelope carries no endpoint list "
        "(Answer.json[] or Answer.data.devices[])",
        pytrace=False,
    )


def _endpoint_problems(model, endpoint: dict) -> list[str]:
    validator = getattr(model, "validate_endpoint", None)
    if validator is None:
        pytest.fail("navig.endpoints.model.validate_endpoint is required by plan §2.1", pytrace=False)
    try:
        result = validator(dict(endpoint))
    except AttributeError as exc:
        pytest.fail(
            "validate_endpoint must accept the plain endpoint dict produced by as_dict() "
            f"(plan §2.1); it raised AttributeError: {exc}",
            pytrace=False,
        )
    except (ValueError, TypeError) as exc:
        return [str(exc) or exc.__class__.__name__]
    if result is None or result is True:
        return []
    if result is False:
        return ["rejected"]
    return [str(problem) for problem in result]


def _projected(projection):
    now = datetime(2026, 10, 22, 0, 0, 0, tzinfo=timezone.utc)
    return projection.project(S.DEVICES, S.expected_status_rows(), now)


# --------------------------------------------------------------------------- #
# Model / projection contract (plan §2.1–§2.3)
# --------------------------------------------------------------------------- #
def test_projection_two_providers_typed_capabilities_and_no_leaks(endpoints_projection):
    projection, model = endpoints_projection
    endpoints = _endpoints_of(_projected(projection))

    assert len(endpoints) == len(S.DEVICES), f"projection must return every device, got {len(endpoints)}"
    providers = {endpoint["id"].split(":", 1)[0] for endpoint in endpoints}
    assert len(providers) >= 2, f"endpoint list must span >=2 providers, got {providers}"
    assert "govee:desk-lamp" in {endpoint["id"] for endpoint in endpoints}, (
        "id must be provider-scoped ('<provider>:<device id>'), plan §2.1"
    )

    for endpoint in endpoints:
        assert _endpoint_problems(model, endpoint) == [], f"invalid endpoint record: {endpoint!r}"
        assert isinstance(endpoint.get("status"), dict), endpoint
        assert isinstance(endpoint["status"].get("online"), bool), endpoint
        assert endpoint["status"].get("updated_at"), "status.updated_at must be the projection time"
        S.assert_no_provider_fields(endpoint)
        S.assert_no_auth_fields(endpoint)

        capabilities = endpoint.get("capabilities")
        assert isinstance(capabilities, list) and capabilities, f"no capabilities projected: {endpoint!r}"
        for capability in capabilities:
            capability_type = capability.get("type", "")
            assert capability_type in S.CAPABILITY_TYPES | S.PROPERTY_TYPES, (
                f"undeclared capability type {capability_type!r}; only the 5+3 slice is allowed"
            )
            meta = capability.get("meta")
            assert isinstance(meta, dict), capability
            assert set(meta) <= S.META_KEYS, f"undeclared meta keys: {set(meta) - S.META_KEYS}"
            for directive in meta.get("supported_directives", ()):  # noqa: SIM118
                assert directive in S.DIRECTIVES, f"undeclared directive {directive!r}"
            if capability_type in S.CAPABILITY_TYPES:
                assert isinstance(capability.get("state"), dict), (
                    f"typed capability has no live state: {capability!r}"
                )

    desk_lamp = next(endpoint for endpoint in endpoints if endpoint["id"] == "govee:desk-lamp")
    types = {capability["type"] for capability in desk_lamp["capabilities"]}
    assert "devices.capabilities.on_off" in types
    assert "devices.capabilities.range" in types, "brightness must map to range (plan §2.2)"


def test_projection_rejects_undeclared_capability_and_provider_fields(endpoints_projection):
    projection, model = endpoints_projection
    base = _endpoints_of(_projected(projection))[0]
    assert _endpoint_problems(model, base) == [], "control: a valid projection must validate"

    bad = json.loads(json.dumps(base))
    bad["capabilities"][0]["type"] = "devices.capabilities.teleporter"
    assert _endpoint_problems(model, bad), "an undeclared capability type must be rejected"

    leaky = json.loads(json.dumps(base))
    leaky["address"] = "192.0.2.40"
    assert _endpoint_problems(model, leaky), "a provider-specific field (address) must be rejected"


def test_projection_marks_offline_from_snapshot(endpoints_projection):
    projection, _model = endpoints_projection
    endpoints = {endpoint["id"]: endpoint for endpoint in _endpoints_of(_projected(projection))}
    assert endpoints["govee:desk-lamp"]["status"]["online"] is True
    assert endpoints["ha:loft-humidifier"]["status"]["online"] is False, (
        "the offline snapshot row (ok=false) must project online=false — never invented"
    )


# --------------------------------------------------------------------------- #
# CLI contract (plan §2.4/§2.5, Answer envelope from §4)
# --------------------------------------------------------------------------- #
def test_cli_list_returns_two_providers_and_valid_answers(fake_home, capsys):
    exit_code, stdout, stderr = S.invoke_cli(["endpoints", "list", "--json"], capsys)
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"navig endpoints list --json failed (exit {exit_code}): {text[-1200:]}"

    envelope = S.json_payload(text)
    S.answer_envelope_ok(envelope)
    assert envelope.get("ok") is True

    endpoints = _envelope_endpoints(envelope)
    assert len(endpoints) == len(S.DEVICES), f"expected the fixture inventory, got {len(endpoints)}"
    providers = {endpoint["id"].split(":", 1)[0] for endpoint in endpoints}
    assert {"govee", "ha"} <= providers, f"expected govee + ha endpoints, got {providers}"
    for endpoint in endpoints:
        S.assert_no_provider_fields(endpoint)
        S.assert_no_auth_fields(endpoint)
        for capability in endpoint["capabilities"]:
            assert capability["type"] in S.CAPABILITY_TYPES | S.PROPERTY_TYPES


def test_projected_meta_is_honest(endpoints_projection):
    """Lead invariant #4: no invented event support — reportable=false, supported_events=[]."""
    projection, _model = endpoints_projection
    dishonest: list[str] = []
    for endpoint in _endpoints_of(_projected(projection)):
        for capability in endpoint["capabilities"]:
            meta = capability.get("meta") or {}
            if meta.get("supported_events") != []:
                dishonest.append(
                    f"{endpoint['id']} {capability['type']}: invented supported_events "
                    f"{meta.get('supported_events')!r} — the home inventory reports no events"
                )
            if meta.get("reportable") is not False:
                dishonest.append(
                    f"{endpoint['id']} {capability['type']}: reportable={meta.get('reportable')!r} "
                    "— not genuinely known from the inventory, so it must be false"
                )
    assert not dishonest, "endpoint meta must not guess event support:\n" + "\n".join(dishonest)


def test_cli_show_prints_state_and_supported_directives(fake_home, capsys):
    exit_code, stdout, stderr = S.invoke_cli(["endpoints", "show", "govee:desk-lamp"], capsys)
    text = S.combined(stdout, stderr).lower()
    assert exit_code == 0, f"navig endpoints show failed (exit {exit_code}): {text[-1200:]}"
    assert "desk lamp" in text or "desk-lamp" in text, f"device name missing: {text[:600]}"
    assert "on_off" in text or "set_range" in text, f"supported directives missing: {text[:600]}"

    exit_code, stdout, stderr = S.invoke_cli(["endpoints", "show", "govee:desk-lamp", "--json"], capsys)
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"navig endpoints show --json failed (exit {exit_code}): {text[-1200:]}"
    envelope = S.json_payload(text)
    S.answer_envelope_ok(envelope)
    serialised = json.dumps(envelope).lower()
    assert "govee:desk-lamp" in serialised
    assert "on_off" in serialised or "set_range" in serialised


def test_cli_set_is_idempotent(fake_home, capsys):
    first_code, first_out, first_err = S.invoke_cli(
        ["endpoints", "set", "govee:desk-lamp", "on_off", "off", "--json"], capsys
    )
    first_text = S.combined(first_out, first_err)
    assert first_code == 0, f"first set failed (exit {first_code}): {first_text[-1200:]}"
    S.answer_envelope_ok(S.json_payload(first_text))

    second_code, second_out, second_err = S.invoke_cli(
        ["endpoints", "set", "govee:desk-lamp", "on_off", "off", "--json"], capsys
    )
    second_text = S.combined(second_out, second_err)
    assert second_code == 0, f"replayed set failed (exit {second_code}): {second_text[-1200:]}"
    second_payload = S.json_payload(second_text)
    S.answer_envelope_ok(second_payload)

    serialised = json.dumps(second_payload).lower()
    assert '"changed": false' in serialised, (
        "the second set must report changed:false (idempotent), got "
        f"{json.dumps(second_payload)[:600]}"
    )
    assert fake_home.mutations == [("off", "desk-lamp")], (
        "the provider must be invoked exactly once for two identical sets, got "
        f"{fake_home.mutations!r}"
    )


def test_cli_set_rejects_undeclared_capability(fake_home, capsys):
    exit_code, stdout, stderr = S.invoke_cli(
        ["endpoints", "set", "govee:desk-lamp", "set_mode", "heat", "--json"], capsys
    )
    text = S.combined(stdout, stderr).lower()
    assert exit_code != 0, f"undeclared capability must be refused, got exit 0: {text[:1000]}"
    assert "mode" in text or "capabilit" in text or "not support" in text, (
        f"the refusal must name the unsupported capability: {text[:1000]}"
    )
    assert fake_home.mutations == [], "a refused set must not reach the provider"


def test_cli_online_matches_live_snapshot(fake_home, capsys):
    exit_code, stdout, stderr = S.invoke_cli(["endpoints", "list", "--json"], capsys)
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"navig endpoints list --json failed (exit {exit_code}): {text[-1200:]}"
    endpoints = _envelope_endpoints(S.json_payload(text))
    by_id = {endpoint["id"]: endpoint for endpoint in endpoints}

    assert by_id["govee:desk-lamp"]["status"]["online"] is True
    assert by_id["ha:loft-humidifier"]["status"]["online"] is False, (
        "the offline fixture device must project status.online=false"
    )

    providers = {endpoint["id"].split(":", 1)[0] for endpoint in endpoints}
    ready = {row["provider"] for row in fake_home.provider_rows if row.get("ready")}
    assert providers <= ready, f"endpoint providers {providers} disagree with readiness {ready}"
