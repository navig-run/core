"""Tests for the connectivity reporter's debounce state machine."""

from __future__ import annotations

import asyncio

import pytest

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
