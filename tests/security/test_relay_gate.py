"""
Tests for navig.license.relay_gate.evaluate_relay_access().

The relay gate decides whether the hosted broker / cloudflared path
(relay.navig.run + Mini App auto-resolve) is available for a given license.
Direct mode (cloud.public_url set) is gated separately by the caller, NOT
here -- the gate only governs the broker-using path.

The rule: an active subscription has the relay; a lapsed one keeps it for a
30-day grace and then loses it; everyone else (free users, content buyers,
perpetual paid tiers) has it. A bug here either gives away the relay past a
lapse (cost to us) or makes a purchase COST a free feature (PR disaster), so
every case is covered explicitly.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional

import pytest

from navig.license.relay_gate import (
    GRACE_DAYS,
    RelayDecision,
    evaluate_relay_access,
)

# ──────────────────────────────────────────────────────────────────────
# A test double for LicenseStatus. The real dataclass lives in
# navig.license.keys; we keep the field surface minimal here so a future
# rename to LicenseStatus doesn't drag the gate tests through churn.
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FakeStatus:
    valid: bool = False
    subscription_active: bool = False
    subscription_until: Optional[_dt.datetime] = None
    perpetual_modules: list[str] = field(default_factory=list)
    billing_period: Optional[str] = None
    effective_tier: str = "solo"


NOW = _dt.datetime(2026, 6, 4, 12, 0, 0, tzinfo=_dt.timezone.utc)


# ──────────────────────────────────────────────────────────────────────
# Case 1 — active subscriber: ALLOWED, no banner.
# ──────────────────────────────────────────────────────────────────────


def test_active_subscriber_allowed():
    status = FakeStatus(
        valid=True,
        subscription_active=True,
        subscription_until=NOW + _dt.timedelta(days=180),
        billing_period="annual",
        effective_tier="pro",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "subscription_active"
    assert decision.banner is None
    assert decision.grace_days_left is None


# ──────────────────────────────────────────────────────────────────────
# Case 2 — lapsed within 30-day grace: ALLOWED with warning banner.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days_into_grace", [0, 1, 15, 29])
def test_lapsed_within_grace_allowed_with_banner(days_into_grace: int):
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=NOW - _dt.timedelta(days=days_into_grace),
        billing_period="annual",
        effective_tier="solo",  # already downgraded
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "lapsed_grace"
    assert decision.banner is not None and "disables in" in decision.banner
    assert decision.grace_days_left is not None
    # Within grace: at least 1 day remaining for the most recent lapse.
    expected_remaining = GRACE_DAYS - days_into_grace
    assert decision.grace_days_left == max(0, expected_remaining)


# ──────────────────────────────────────────────────────────────────────
# Case 3 — lapsed past grace: DENIED with renew-or-self-host banner.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days_past_grace", [1, 60, 365])
def test_lapsed_past_grace_denied(days_past_grace: int):
    sub_until = NOW - _dt.timedelta(days=GRACE_DAYS + days_past_grace)
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=sub_until,
        billing_period="annual",
        effective_tier="solo",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert not decision.allowed
    assert decision.reason == "subscription_lapsed"
    assert decision.banner is not None
    assert "subscription" in decision.banner.lower()
    assert "tailscale" in decision.banner.lower()


# ──────────────────────────────────────────────────────────────────────
# Case 4 — perpetual buyers (one_time billing): ALLOWED.
#
# A perpetual PAID tier holds its tier, relay included. A free user who
# bought content (a Bay item, a legacy module) is still a free user: the
# purchase must never take away the relay a never-paid user keeps.
# ──────────────────────────────────────────────────────────────────────


def test_perpetual_tier_holder_allowed():
    """A perpetual PAID tier holds the tier — relay included.

    This used to be DENIED ("perpetual = local only"). In practice that refused
    the relay to contract/comp Enterprise licences, which are issued one_time,
    while a never-paid free user kept it.
    """
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=None,  # one_time has no expiry
        billing_period="one_time",
        perpetual_modules=[],
        effective_tier="enterprise",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "perpetual_tier"


@pytest.mark.parametrize(
    "grants",
    [["item:homelab-space"], ["ai_operator"], ["item:devops-space", "security_ops"]],
)
def test_buying_content_never_costs_the_free_relay(grants):
    """A free user who bought a $5 space must keep what a never-paid user has.

    Regression: any perpetual grant used to deny the relay, so the purchase
    itself removed a free feature.
    """
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=None,
        billing_period="one_time",
        perpetual_modules=grants,
        effective_tier="free",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "solo_free"


# ──────────────────────────────────────────────────────────────────────
# Case 5 — genuine free Solo (no license at all): ALLOWED.
# This is the viral on-ramp. pip install → Telegram Mini App works.
# Broker rate-limits abuse globally.
# ──────────────────────────────────────────────────────────────────────


def test_no_license_solo_free_allowed():
    status = FakeStatus(
        valid=False,
        subscription_active=False,
        subscription_until=None,
        perpetual_modules=[],
        billing_period=None,
        effective_tier="solo",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "solo_free"
    assert decision.banner is None


# ──────────────────────────────────────────────────────────────────────
# Robustness — never raises, never blocks boot on a malformed status.
# ──────────────────────────────────────────────────────────────────────


def test_naive_datetime_subscription_until_normalised():
    """A naive datetime in subscription_until must not crash the gate."""
    naive = _dt.datetime(2026, 5, 1, 0, 0, 0)  # no tzinfo
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=naive,
        billing_period="annual",
    )
    decision = evaluate_relay_access(status, now=NOW)
    # 34 days past lapse → past grace → denied
    assert not decision.allowed
    assert decision.reason == "subscription_lapsed"


def test_garbage_status_degrades_to_solo_free():
    """An object missing every expected attribute must yield solo_free."""

    class Empty:
        pass

    decision = evaluate_relay_access(Empty(), now=NOW)  # type: ignore[arg-type]
    assert decision.allowed
    assert decision.reason == "solo_free"


# ──────────────────────────────────────────────────────────────────────
# RelayDecision shape — the serialised form is what the Deck consumes.
# ──────────────────────────────────────────────────────────────────────


def test_decision_serialises_for_deck_consumption():
    decision = RelayDecision(
        allowed=True,
        reason="subscription_active",
        banner=None,
        grace_days_left=None,
    )
    d = decision.as_dict()
    assert set(d.keys()) == {"allowed", "reason", "banner", "grace_days_left"}
    assert d["allowed"] is True
    assert d["reason"] == "subscription_active"


# ──────────────────────────────────────────────────────────────────────
# Sanity: exact grace boundary — at 30 days + 0s past lapse, still allowed.
# At 30 days + 1s past, denied.
# ──────────────────────────────────────────────────────────────────────


def test_grace_boundary_exactly_30_days_allowed():
    sub_until = NOW - _dt.timedelta(days=GRACE_DAYS)
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=sub_until,
        billing_period="annual",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert decision.allowed
    assert decision.reason == "lapsed_grace"


def test_grace_boundary_one_second_past_denied():
    sub_until = NOW - _dt.timedelta(days=GRACE_DAYS, seconds=1)
    status = FakeStatus(
        valid=True,
        subscription_active=False,
        subscription_until=sub_until,
        billing_period="annual",
    )
    decision = evaluate_relay_access(status, now=NOW)
    assert not decision.allowed
    assert decision.reason == "subscription_lapsed"
