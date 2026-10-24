"""Profiles are approval presets — a name → policy table, never routing weights.

Rows: mutation class (read / single-target / destructive batch). Columns: the three
profiles. Cells: auto or plan-only. No cost/latency/quality number exists here, and
``approval.security_level = high`` can only make the table stricter.
"""

from __future__ import annotations

import pytest

from navig.reflex.errors import ReflexError
from navig.reflex.profiles import (
    AUTO,
    DEFAULT_PROFILE,
    PLAN_ONLY,
    POLICY_TABLE,
    PROFILE_NAMES,
    describe,
    mutation_class,
    needs_explicit_commit,
    resolve_profile,
)


def test_the_policy_table_has_the_planned_shape() -> None:
    assert PROFILE_NAMES == ("safe", "fast", "campaign")
    for profile in PROFILE_NAMES:
        assert set(POLICY_TABLE[profile]) == {"read", "single", "destructive"}
        assert set(POLICY_TABLE[profile].values()) <= {AUTO, PLAN_ONLY}


def test_the_policy_table_is_the_plan_3_5_table() -> None:
    assert POLICY_TABLE["safe"] == {"read": AUTO, "single": PLAN_ONLY, "destructive": PLAN_ONLY}
    assert POLICY_TABLE["fast"] == {"read": AUTO, "single": AUTO, "destructive": PLAN_ONLY}
    assert (
        POLICY_TABLE["campaign"]
        == {"read": AUTO, "single": PLAN_ONLY, "destructive": PLAN_ONLY}
    )


def test_no_weight_like_value_is_present_anywhere_in_the_table() -> None:
    """Regression: profiles are approval cells, never numeric routing weights."""
    for row in POLICY_TABLE.values():
        for cell in row.values():
            assert isinstance(cell, str) and cell in (AUTO, PLAN_ONLY)


@pytest.mark.parametrize(
    ("read_only", "risk", "destructive", "expected"),
    [
        (True, "low", False, "read"),
        (False, "low", False, "single"),
        (False, "low", True, "destructive"),
        (False, "high", False, "destructive"),
        (False, "destructive", True, "destructive"),
    ],
)
def test_mutation_class_rows(read_only, risk, destructive, expected) -> None:
    assert (
        mutation_class(read_only=read_only, risk=risk, destructive=destructive) == expected
    )


def test_default_profile_is_fast() -> None:
    assert DEFAULT_PROFILE == "fast"
    assert resolve_profile(None) == "fast"
    assert resolve_profile("SAFE") == "safe"


@pytest.mark.parametrize("bad", ["turbo", "", "  "])
def test_unknown_profile_is_a_usage_error(bad: str) -> None:
    with pytest.raises(ReflexError, match="unknown profile|use one of"):
        resolve_profile(bad)


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_read_only_never_needs_a_commit(profile: str) -> None:
    assert not needs_explicit_commit(
        profile, read_only=True, risk="low", destructive=False, tool_name="home.status"
    )


@pytest.mark.parametrize("profile", ["safe", "campaign"])
def test_safe_and_campaign_plan_every_mutation(profile: str) -> None:
    assert needs_explicit_commit(
        profile, read_only=False, risk="low", destructive=False, tool_name="home.on"
    )


def test_fast_auto_commits_one_reversible_action() -> None:
    assert not needs_explicit_commit(
        "fast", read_only=False, risk="low", destructive=False, tool_name="home.on"
    )


def test_fast_plans_a_destructive_batch() -> None:
    assert needs_explicit_commit(
        "fast", read_only=False, risk="low", destructive=True, tool_name="home.off"
    )


def test_a_high_risk_tool_is_planned_in_every_profile() -> None:
    for profile in PROFILE_NAMES:
        assert needs_explicit_commit(
            profile,
            read_only=False,
            risk="high",
            destructive=False,
            tool_name="home.set",
            security_level="medium",
        )


def test_high_security_level_makes_fast_plan_only() -> None:
    assert needs_explicit_commit(
        "fast",
        read_only=False,
        risk="low",
        destructive=False,
        tool_name="home.on",
        security_level="high",
    ) is True


def test_a_broken_approval_gate_fails_closed(monkeypatch) -> None:
    """If the approval vocabulary cannot be consulted, plan the action (never auto-run)."""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("approval vocabulary unavailable")

    import navig.tools.approval as approval

    monkeypatch.setattr(approval, "needs_approval", _explode)
    assert needs_explicit_commit(
        "fast", read_only=False, risk="medium", destructive=False, tool_name="home.set"
    )


def test_describe_is_machine_readable() -> None:
    summary = describe("fast")
    assert summary["name"] == "fast"
    assert summary["auto_commit_single_mutation"] is True
    assert summary["auto_commit_destructive"] is False
    assert summary["mutation_classes"] == POLICY_TABLE["fast"]
