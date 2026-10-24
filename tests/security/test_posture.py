"""One security posture switch drives every level system in NAVIG.

The tree has several level vocabularies: ``approval.security_level`` (high/medium/low),
reflex risk levels (per tool), reflex routing profiles (safe/fast/campaign), the tools
``ApprovalPolicy`` (yolo/confirm_destructive/confirm_all), and MCP read-only/destructive
hints. ``navig.security.posture`` is the single mapping so one level change moves the
derived systems together — and these tests pin the mapping, not just the code path.
"""

from __future__ import annotations

import pytest

from navig.security import posture


def test_the_mapping_covers_every_level():
    assert set(posture.POSTURES) == {"strict", "balanced", "permissive"}
    for level in ("high", "medium", "low"):
        data = posture.describe(level)
        assert data["level"] == level
        assert data["posture"] in posture.POSTURES
        assert data["reflex_profile"] in {"safe", "fast", "campaign"}
        assert data["approval_policy"] in {"confirm_all", "confirm_destructive"}


def test_high_is_strict_plans_everything_and_asks_about_everything():
    data = posture.describe("high")
    assert data["posture"] == "strict"
    assert data["reflex_profile"] == "safe"
    assert data["approval_policy"] == "confirm_all"


def test_medium_is_balanced_and_keeps_the_shipped_fast_profile():
    """The default posture must not change the default routing profile — `fast` is
    the shipped default and the reflex suite pins it."""
    data = posture.describe("medium")
    assert data["posture"] == "balanced"
    assert data["reflex_profile"] == "fast"
    assert data["approval_policy"] == "confirm_destructive"


def test_low_is_permissive_but_keeps_confirmation_for_gated_tools():
    """``low`` exempts the shell family at the level classifier — it must NOT turn the
    chat agent's approval gate into YOLO: db/cdp/connector tools still ask."""
    data = posture.describe("low")
    assert data["posture"] == "permissive"
    assert data["reflex_profile"] == "fast"
    assert data["approval_policy"] == "confirm_destructive"


def test_alias_spellings_resolve_to_the_same_posture():
    assert posture.level("strict") == "high"
    assert posture.level("balanced") == "medium"
    assert posture.level("permissive") == "low"


def test_an_unknown_override_falls_back_to_the_configured_level(monkeypatch):
    monkeypatch.setattr(posture, "_configured_level", lambda: "high")
    assert posture.level("nonsense") == "high"


def test_reflex_default_profile_follows_the_posture(monkeypatch):
    from navig.reflex import profiles

    monkeypatch.setattr(posture, "reflex_profile", lambda *a: "safe")
    assert profiles.resolve_profile(None) == "safe"


def test_an_explicit_reflex_profile_still_wins(monkeypatch):
    from navig.reflex import profiles

    monkeypatch.setattr(posture, "reflex_profile", lambda *a: "safe")
    assert profiles.resolve_profile("fast") == "fast"
    with pytest.raises(Exception):
        profiles.resolve_profile("made-up")


def test_the_agent_approval_default_follows_the_posture(monkeypatch):
    from navig.tools import approval

    monkeypatch.setattr(approval, "_policy", None)
    monkeypatch.setattr(posture, "approval_policy", lambda *a: "confirm_all")
    assert approval.get_approval_policy() is approval.ApprovalPolicy.CONFIRM_ALL


def test_an_explicit_approval_policy_still_wins(monkeypatch):
    from navig.tools import approval

    monkeypatch.setattr(posture, "approval_policy", lambda *a: "confirm_all")
    approval.set_approval_policy("yolo")
    try:
        assert approval.get_approval_policy() is approval.ApprovalPolicy.YOLO
    finally:
        approval._policy = None
