"""The approval window must survive being configured, and be long enough to answer.

Two defects, both only reachable once approvals actually STARTED reaching a human
(before that, nothing sent them, so the window merely governed how fast a request
auto-denied itself — 55 expiries on one install, every one `channel=mission`).

1. `timeout_seconds` was read raw: `approval_cfg.get("timeout_seconds", 120)`.
   `navig config set` stores raw STRINGS, and an int used raw does not misbehave
   quietly — it RAISES at the point of use, far from the config that caused it::

       approval.timeout_seconds = "600"
         -> timedelta(seconds="600")        TypeError
         -> asyncio.wait_for(timeout="600") TypeError

   So the one knob that could lengthen the window disabled approvals for anyone who
   touched it.

2. 120 s is a terminal timeout, not a human one. Measured on that install the moment
   delivery began working: the operator's answer landed SEVEN MINUTES after the ask —
   "Approval 75236e2a answered too late (already EXPIRED) — the inline decision was
   NOT applied".

Waiting longer is the safe direction: `default_action` is "deny" and DANGEROUS always
denies, so a longer window only delays an auto-deny.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from navig.approval.policies import ApprovalPolicy


def test_a_string_from_config_set_becomes_an_int() -> None:
    """THE regression: `navig config set approval.timeout_seconds 600`."""
    p = ApprovalPolicy.from_config({"approval": {"timeout_seconds": "600"}})
    assert p.timeout_seconds == 600
    assert isinstance(p.timeout_seconds, int)


def test_the_coerced_value_survives_the_two_places_it_is_used() -> None:
    """Both raised TypeError on a string — assert the real call sites, not the type."""
    p = ApprovalPolicy.from_config({"approval": {"timeout_seconds": "600"}})

    datetime.now() + timedelta(seconds=p.timeout_seconds)  # manager.py: expires_at

    async def _use() -> None:
        # manager.py: asyncio.wait_for(future, timeout=policy.timeout_seconds)
        try:
            await asyncio.wait_for(asyncio.sleep(0), timeout=p.timeout_seconds)
        except TimeoutError:  # pragma: no cover - not what this asserts
            pass

    asyncio.run(_use())


def test_garbage_falls_back_rather_than_wedging_approvals() -> None:
    """A malformed setting must not be able to break the subsystem it configures."""
    for raw in ("nonsense", "", None, [], {}):
        p = ApprovalPolicy.from_config({"approval": {"timeout_seconds": raw}})
        assert p.timeout_seconds == 900, raw


def test_zero_and_negative_fall_back() -> None:
    """A zero window would auto-deny instantly — indistinguishable from broken."""
    for raw in (0, "0", -5, "-5"):
        p = ApprovalPolicy.from_config({"approval": {"timeout_seconds": raw}})
        assert p.timeout_seconds == 900, raw


def test_a_real_int_is_honoured() -> None:
    p = ApprovalPolicy.from_config({"approval": {"timeout_seconds": 45}})
    assert p.timeout_seconds == 45


def test_the_default_leaves_time_for_a_human_to_answer() -> None:
    """Measured: a real answer arrived 7 minutes after the ask."""
    assert ApprovalPolicy().timeout_seconds >= 420, (
        "the default is shorter than the observed human response time, so approvals "
        "delivered to Telegram expire before they can be answered"
    )
