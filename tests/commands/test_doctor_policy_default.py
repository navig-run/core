"""`navig doctor` must state the gateway policy posture (Gate 0 groundwork).

PolicyGate's default decides what happens to actions no rule matches. Gate 0 made
TYPO'd values fail closed (deny); the default itself is an operator choice. Until
this row the choice was invisible in `navig doctor` — an operator could believe
their gateway was policy-gated while `default: allow` passed everything unlisted.
"""

from __future__ import annotations

import pytest

from navig.commands import doctor


@pytest.fixture
def gateway(monkeypatch):
    """Drive the row from a synthetic gateway config only (no run-state needed)."""
    cfg: dict = {}

    class _CM:
        @staticmethod
        def get(key, default=None):
            return cfg if key == "gateway" else default

    monkeypatch.setattr("navig.config.ConfigManager", lambda *a, **k: _CM())
    return cfg


def _row(gateway):
    return doctor.check_policy_default()[0]


def test_absent_policy_reads_as_allow_and_names_the_endpoint(gateway):
    row = _row(gateway)
    assert row[1] is True
    assert "default: allow" in row.detail
    assert "gateway.policy.default deny" in row.detail, "the fail-closed endpoint must be spelled out"


def test_explicit_allow_shows_the_rule_count(gateway):
    gateway["policy"] = {"default": "allow", "rules": [{"pattern": "x", "action": "deny"}]}
    row = _row(gateway)
    assert row[1] is True
    assert "default: allow" in row.detail and "1 rule" in row.detail


def test_deny_is_green_and_says_fail_closed(gateway):
    gateway["policy"] = {"default": "deny"}
    row = _row(gateway)
    assert row[1] is True
    assert "default: deny" in row.detail
    assert "fail-closed" in row.detail


def test_require_approval_is_green(gateway):
    gateway["policy"] = {"default": "require_approval"}
    row = _row(gateway)
    assert row[1] is True
    assert "default: require_approval" in row.detail


def test_a_typo_is_a_fault_not_a_green_tick(gateway):
    """PolicyGate fails closed on a typo — the row must not render that as healthy."""
    gateway["policy"] = {"default": "alow"}
    row = _row(gateway)
    assert row[0] == doctor._WARN, "a typo'd default is a warning, never ✓"
    assert "alow" in row.detail
    assert "fails CLOSED" in row.detail


def test_an_unreadable_config_warns_rather_than_ticking(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr("navig.config.ConfigManager", _boom)
    row = doctor.check_policy_default()[0]
    assert row[1] is False
    assert "on fire" in row.detail


def test_the_row_is_wired_into_the_gateway_section(gateway):
    """A check nothing calls is documentation."""
    sections = dict(doctor._collect_sections(port=None))
    assert any(r.label == "Gateway policy" for r in sections["Gateway"])
