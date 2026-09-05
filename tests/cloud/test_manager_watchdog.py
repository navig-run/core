"""The cloudflared watchdog must keep retrying after a failed restart.

Old behaviour: cloudflared exits (machine wake / network blip), the immediate
respawn fails because connectivity hasn't returned, and the watchdog called
_mark_error() + return — so nothing ever restarted the tunnel again even once the
network came back. Now a failed restart backs off and loops.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from navig.cloud import CloudManager


class _DeadProc:
    async def wait(self) -> int:
        return 1  # already exited


async def test_watchdog_retries_after_a_failed_restart(monkeypatch):
    m = CloudManager(api_key="k", broker_url="https://api.navig.run", gateway_port=8765)
    m._proc = _DeadProc()

    spawn = {"n": 0}

    async def fake_spawn():
        spawn["n"] += 1
        m._proc = _DeadProc()

    waits = {"n": 0}

    async def fake_wait_for_url(*, timeout):
        waits["n"] += 1
        if waits["n"] == 1:
            raise TimeoutError("network down")  # first restart fails
        m._stop_requested = True  # second restart succeeds → end the loop

    async def fake_kill():
        m._proc = None  # mirrors the real _kill_proc

    m._spawn_cloudflared = fake_spawn
    m._wait_for_url = fake_wait_for_url
    m._register_current_url = AsyncMock()
    m._kill_proc = fake_kill

    orig_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: orig_sleep(0))  # no real backoff waits

    await asyncio.wait_for(m._watchdog_loop(), timeout=5.0)

    # The watchdog respawned a SECOND time after the first restart failed.
    assert spawn["n"] == 2  # pre-fix: 1 (it gave up and returned)
    assert m.state.rotations == 1  # the successful restart counted
