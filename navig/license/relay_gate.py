"""
Cloud-relay access gate.

The hosted relay path (relay.navig.run + cloudflared registration +
Mini App auto-resolve) is the one component of NAVIG that costs us money
per active user — bandwidth, broker D1 reads, Pages Functions invocations,
relay uptime. The local Deck UI, vault, agent runtime, modules — none of
that has a per-user marginal cost. So we draw the line here:

    Perpetual packs (one-time)  ─→  local app, yours forever, ONE host,
                                    no hosted relay (self-host the Mini
                                    App with `navig cloud tailscale
                                    --enable` for free)
    Subscription                 ─→  host scale + the hosted relay +
                                    future modules + support

This module is a PURE decision function — no I/O, no side effects, no
caching — so it's trivially testable and can be re-used from the daemon,
the deck status endpoint, and any future CLI command.

The gate does NOT apply to direct mode (`cloud.public_url` is set) nor to
**Lighthouse** (`cloud.mode=lighthouse`): in both cases the user hosts their
own public ingress (their reverse proxy / their own Cloudflare account), the
broker isn't involved, and we have no per-user cost. Those paths are always
free — the gate is bypassed before it's ever evaluated (see
`navig/gateway/server.py:_start_cloud_manager`). It now governs only the legacy
hosted cloudflared/broker relay.

Cases
-----

  subscription_active=True
      ALLOWED — they're paying us. (reason="subscription_active")

  Lapsed subscription within 30-day grace
      ALLOWED with warning banner. Gives existing subscribers a runway
      to renew without bricking their Telegram setup mid-week.
      (reason="lapsed_grace")

  Lapsed subscription past 30-day grace
      DENIED. Banner directs to renew or self-host with Tailscale.
      (reason="subscription_lapsed")

  Perpetual-only (billing_period="one_time", subscription_active=False)
      DENIED. Banner explains the split honestly: their local app is
      forever, the hosted relay needs a subscription.
      (reason="requires_subscription")

  No license / brand new free Solo
      ALLOWED. This is the viral hook — `pip install navig` + Telegram
      Mini App works out of the box on 1 host. Their broker traffic is
      already rate-limited globally; they're not the cost problem.
      (reason="solo_free")
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from navig.license.keys import LicenseStatus


GRACE_DAYS = 30

RelayReason = Literal[
    "subscription_active",
    "lapsed_grace",
    "subscription_lapsed",
    "requires_subscription",
    "solo_free",
]


@dataclass(frozen=True)
class RelayDecision:
    """The result of evaluating cloud-relay access.

    ``allowed`` decides whether the daemon brings up CloudManager's
    broker-registering path and whether the Deck's cloud panel offers
    "enable relay" as an action. ``banner`` is the user-facing message
    (markdown allowed). ``grace_days_left`` is only meaningful for
    ``lapsed_grace``.
    """

    allowed: bool
    reason: RelayReason
    banner: Optional[str] = None
    grace_days_left: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "banner": self.banner,
            "grace_days_left": self.grace_days_left,
        }


def evaluate_relay_access(
    status: "LicenseStatus",
    now: Optional[_dt.datetime] = None,
) -> RelayDecision:
    """Decide whether the user may use the hosted cloud relay.

    Pure function. Accepts an injected ``now`` for testability; defaults
    to UTC now. Never raises — degrades to "solo_free" on any unexpected
    input so the daemon never refuses to boot because of a license
    parsing edge case.
    """
    if now is None:
        now = _dt.datetime.now(tz=_dt.timezone.utc)

    # 1. Active subscriber — the simple case.
    if getattr(status, "subscription_active", False):
        return RelayDecision(allowed=True, reason="subscription_active")

    # 2. Lapsed subscription: 30-day grace before we cut relay.
    sub_until = getattr(status, "subscription_until", None)
    if isinstance(sub_until, _dt.datetime):
        # Normalise to aware UTC for the comparison.
        if sub_until.tzinfo is None:
            sub_until = sub_until.replace(tzinfo=_dt.timezone.utc)
        grace_end = sub_until + _dt.timedelta(days=GRACE_DAYS)
        if now <= grace_end:
            days_left = max(0, (grace_end - now).days)
            return RelayDecision(
                allowed=True,
                reason="lapsed_grace",
                grace_days_left=days_left,
                banner=(
                    f"Your subscription lapsed. Cloud relay disables in "
                    f"{days_left} days. Renew at navig.run/buy, or self-host "
                    f"the Mini App with `navig cloud tailscale --enable`."
                ),
            )
        return RelayDecision(
            allowed=False,
            reason="subscription_lapsed",
            banner=(
                "Cloud relay requires an active subscription. Renew at "
                "navig.run/buy, or self-host the Mini App with "
                "`navig cloud tailscale --enable` — free and stable."
            ),
        )

    # 3. No subscription history. Distinguish perpetual-only (paid once)
    #    from genuine free Solo (never paid).
    billing = getattr(status, "billing_period", None)
    perpetual_mods = list(getattr(status, "perpetual_modules", None) or [])

    is_perpetual_buyer = billing == "one_time" or bool(perpetual_mods)
    if is_perpetual_buyer:
        return RelayDecision(
            allowed=False,
            reason="requires_subscription",
            banner=(
                "Cloud relay is a subscription feature. Your perpetual "
                "entitlements stay yours forever; self-host the Mini App "
                "for free with `navig cloud tailscale --enable`."
            ),
        )

    # 4. Genuine free Solo — viral on-ramp. Broker still applies its
    #    global rate limit so abuse is bounded.
    return RelayDecision(allowed=True, reason="solo_free")
