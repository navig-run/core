"""Tests for the connectivity reporter's debounce state machine."""

from __future__ import annotations

import asyncio

from navig.notify.producers.connectivity import ConnectivityReporter


async def test_brief_blip_does_not_announce():
    sent: list[str] = []

    async def sink(kind):
        sent.append(kind)

    r = ConnectivityReporter(offline_grace_s=0.05, sink=sink)
    r.on_status("offline")     # drop…
    await asyncio.sleep(0.01)
    r.on_status("online")      # …recovered well within the grace window
    await asyncio.sleep(0.1)
    assert sent == []          # nothing announced — it was just a blip


async def test_sustained_outage_announces_offline_then_online():
    sent: list[str] = []

    async def sink(kind):
        sent.append(kind)

    r = ConnectivityReporter(offline_grace_s=0.02, sink=sink)
    r.on_status("offline")
    await asyncio.sleep(0.06)   # outlasts the grace → offline announced
    assert sent == ["offline"]
    r.on_status("online")       # clears the announced outage
    await asyncio.sleep(0.02)
    assert sent == ["offline", "online"]


async def test_disabled_suppresses_announcements():
    sent: list[str] = []

    async def sink(kind):
        sent.append(kind)

    r = ConnectivityReporter(offline_grace_s=0.01, enabled_check=lambda: False, sink=sink)
    r.on_status("offline")
    await asyncio.sleep(0.04)
    assert sent == []


# ── Delivery is asynchronous and fallible ────────────────────────────────────
#
# The tests above use instant sinks, so a dispatch still in flight was never
# exercised. A real dispatch fans out over the network and takes real time — and
# the "offline" one is the slow case by definition, the link just died.


async def test_online_never_overtakes_an_offline_still_in_flight():
    """THE REGRESSION: emissions must be delivered in the order they occurred.

    An "online" raised while the "offline" dispatch was still awaiting the network
    used to be delivered FIRST, so the operator's last message read "Brain offline"
    while the brain was up — and the clear had already been spent, so nothing ever
    corrected it.
    """
    sent: list[str] = []

    async def slow_offline_sink(kind):
        if kind == "offline":
            await asyncio.sleep(0.05)
        sent.append(kind)

    r = ConnectivityReporter(offline_grace_s=0.01, sink=slow_offline_sink)
    r.on_status("offline")
    await asyncio.sleep(0.03)   # grace elapsed → the offline dispatch is in flight
    r.on_status("online")       # uplink returns mid-dispatch
    await asyncio.sleep(0.3)

    assert sent == ["offline", "online"], f"delivered out of order: {sent}"
    assert sent[-1] == "online", "the operator's last word must match reality"


async def test_a_failed_offline_does_not_arm_a_recovery_notice():
    """Don't announce a recovery for an outage nobody was told about."""
    sent: list[str] = []

    async def all_channels_down_sink(kind):
        sent.append(kind)
        # The shape navig.notify.dispatch returns when every channel failed.
        return {"type": "connectivity", "channels": [{"channel": "telegram", "ok": False}]}

    r = ConnectivityReporter(offline_grace_s=0.01, sink=all_channels_down_sink)
    r.on_status("offline")
    await asyncio.sleep(0.05)
    assert sent == ["offline"]
    assert r._announced_offline is False, (
        "the latch must record that the operator was TOLD, not merely that a send "
        "was attempted"
    )

    r.on_status("online")
    await asyncio.sleep(0.05)
    assert sent == ["offline"], "no recovery notice for an outage never announced"


async def test_a_delivered_offline_still_arms_the_recovery_notice():
    """The success path is unchanged: a delivered outage gets its clear."""
    sent: list[str] = []

    async def delivering_sink(kind):
        sent.append(kind)
        return {"type": "connectivity", "channels": [{"channel": "deck", "ok": True}]}

    r = ConnectivityReporter(offline_grace_s=0.01, sink=delivering_sink)
    r.on_status("offline")
    await asyncio.sleep(0.05)
    assert r._announced_offline is True
    r.on_status("online")
    await asyncio.sleep(0.05)
    assert sent == ["offline", "online"]


async def test_muted_fanout_counts_as_delivered_not_failed():
    """An empty channel list is an intentional mute, not a delivery failure."""
    sent: list[str] = []

    async def muted_sink(kind):
        sent.append(kind)
        return {"type": "connectivity", "skipped": "master_off", "channels": []}

    r = ConnectivityReporter(offline_grace_s=0.01, sink=muted_sink)
    r.on_status("offline")
    await asyncio.sleep(0.05)
    assert r._announced_offline is True, (
        "a muted fan-out must not be treated as a failed delivery — otherwise the "
        "reporter churns against a user who deliberately silenced it"
    )
