"""
Cloud-relay access gate.

The hosted relay path (relay.navig.run + cloudflared registration +
Mini App auto-resolve) is the one component of NAVIG that costs us money
per active user — bandwidth, broker D1 reads, Pages Functions invocations,
relay uptime. The local Deck UI, vault, agent runtime, modules — none of
that has a per-user marginal cost. So we draw the line here:

    Subscription                 ─→  host scale + the hosted relay +
                                    future modules + support
    Lapsed subscription          ─→  30-day grace, then no hosted relay
    Everyone else                ─→  hosted relay (free on-ramp)

Buying something can never cost a user a feature they had for free. The old
split denied the relay to anyone holding a one-time token or ANY perpetual
grant — so a free user who bought a $5 Bay space lost the relay a never-paid
user keeps, and a comp/contract Enterprise token (issued `one_time`) was
refused it outright.

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

  Perpetual paid tier (billing_period="one_time" on a paid tier: a contract
  or comp licence, or a retired lifetime purchase)
      ALLOWED. They hold the tier; the relay is part of it.
      (reason="perpetual_tier")

  No license / free tier — including free users who own Bay items or
  legacy module grants (content purchases, not relay purchases)
      ALLOWED. This is the viral hook — `pip install navig` + Telegram
      Mini App works out of the box on 1 host. Their broker traffic is
      already rate-limited globally; they're not the cost problem.
      (reason="solo_free")

  `requires_subscription` is kept in the Literal for consumers of older
  payloads; nothing emits it now.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from navig.license.keys import LicenseStatus


GRACE_DAYS = 30

RelayReason = Literal[
    "subscription_active",
    "lapsed_grace",
    "subscription_lapsed",
    "requires_subscription",
    "perpetual_tier",
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

    # 3. No subscription history. A perpetual PAID tier (contract/comp
    #    licence, retired lifetime) holds the tier, relay included.
    billing = getattr(status, "billing_period", None)
    tier = str(getattr(status, "effective_tier", "") or "").lower()
    if billing == "one_time" and tier not in ("", "free", "solo"):
        return RelayDecision(allowed=True, reason="perpetual_tier")

    # 4. Free tier — never paid, or paid only for content (Bay items, legacy
    #    module grants). Viral on-ramp; the broker still applies its global
    #    rate limit so abuse is bounded.
    return RelayDecision(allowed=True, reason="solo_free")
