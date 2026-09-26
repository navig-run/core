"""A permanently-down MCP server must not retry forever at a fixed rate.

Measured on the operator's own daemon: **12,420 of 20,964 log lines — 59.2% of
the entire log** — were one unreachable client (`vscode-copilot`, a closed
editor) being retried once a minute for the daemon's whole life.

The retry *inside* one sweep already backed off (5s, 10s). The sweep itself did
not, so every 60s produced the same five lines:

    MCP client vscode-copilot is down — scheduling reconnect
    MCP client vscode-copilot connect failed (attempt 1/3): ...
    MCP client vscode-copilot connect failed (attempt 2/3): ...
    MCP client vscode-copilot connect failed (attempt 3/3): ...
    MCP client vscode-copilot failed to connect after 3 attempt(s)

That is the failure this tree already writes down for `navig doctor`: noise that
"trains the operator to skim past the one row that might have mattered."
"""

from __future__ import annotations

import pytest

from navig.mcp import registry as reg
from navig.mcp.registry import MCPClientManager


class _Client:
    """A client that is never reachable, like a server that is not running."""

    def __init__(self, client_id: str = "vscode-copilot", connects: bool = False) -> None:
        self.id = client_id
        self.is_connected = False
        self._connects = connects
        self.attempts = 0

    async def connect(self) -> None:
        self.attempts += 1
        if self._connects:
            self.is_connected = True
            return
        raise ConnectionError("connection refused")

    async def disconnect(self) -> None:
        self.is_connected = False


def _manager() -> MCPClientManager:
    return MCPClientManager(config={})


def test_a_failing_client_is_put_into_a_growing_backoff() -> None:
    """One failed round parks the client; each further round doubles the wait."""
    mgr = _manager()
    client = _Client()

    waits = []
    for _ in range(5):
        mgr._note_reconnect_result(client)
        waits.append(mgr._reconnect_not_before[client.id])

    gaps = [round(b - a, 3) for a, b in zip(waits, waits[1:])]
    # 1m -> 2m -> 4m -> 8m: each round waits longer than the last.
    assert all(g > 0 for g in gaps), f"backoff did not grow: {gaps}"
    assert mgr._reconnect_failures[client.id] == 5


def test_the_backoff_is_capped() -> None:
    """A cap keeps a recovered server from waiting hours to be noticed."""
    mgr = _manager()
    client = _Client()

    import time

    for _ in range(40):
        mgr._note_reconnect_result(client)

    remaining = mgr._reconnect_not_before[client.id] - time.monotonic()
    assert remaining <= reg._BACKOFF_MAX_S + 1, (
        f"backoff grew past the cap: {remaining}s"
    )


def test_a_successful_connect_clears_the_penalty_box() -> None:
    """The server came back: the next drop must be noticed within one sweep, not
    after the half hour the previous outage had earned."""
    mgr = _manager()
    client = _Client()

    for _ in range(6):
        mgr._note_reconnect_result(client)
    assert client.id in mgr._reconnect_not_before

    client.is_connected = True
    mgr._note_reconnect_result(client)

    assert client.id not in mgr._reconnect_not_before
    assert client.id not in mgr._reconnect_failures


def test_removing_a_client_forgets_its_backoff() -> None:
    """Re-adding the same id must not inherit a penalty it never earned."""
    mgr = _manager()
    client = _Client()
    for _ in range(4):
        mgr._note_reconnect_result(client)

    mgr._forget_backoff(client.id)

    assert client.id not in mgr._reconnect_failures
    assert client.id not in mgr._reconnect_not_before


@pytest.mark.asyncio
async def test_remove_client_actually_calls_it() -> None:
    """The wiring, not the helper — a helper nothing calls changes nothing."""
    mgr = _manager()
    client = _Client()
    mgr._clients[client.id] = client
    for _ in range(3):
        mgr._note_reconnect_result(client)

    await mgr.remove_client(client.id)

    assert client.id not in mgr._reconnect_failures, (
        "remove_client left a stale backoff behind"
    )


@pytest.mark.asyncio
async def test_an_explicit_connect_clears_the_penalty_box() -> None:
    """Someone asked for it NOW. Honour that instead of leaving them parked for
    up to half an hour."""
    mgr = _manager()
    client = _Client(connects=True)
    mgr._clients[client.id] = client
    for _ in range(6):
        mgr._note_reconnect_result(client)

    ok = await mgr.connect_client(client.id)

    assert ok is True
    assert client.id not in mgr._reconnect_not_before
    assert client.attempts == 1


def test_a_healthy_client_is_never_parked() -> None:
    """The backoff must only ever apply to something that actually failed."""
    mgr = _manager()
    client = _Client(connects=True)
    client.is_connected = True

    mgr._note_reconnect_result(client)

    assert mgr._reconnect_not_before == {}
    assert mgr._reconnect_failures == {}


def test_the_log_quietens_but_the_retry_does_not_stop() -> None:
    """The point is fewer LINES, not fewer recoveries.

    After a few rounds the "is down" line drops to debug — the client is simply
    absent and saying so every minute is what buried the log — while the retry
    itself continues on the backoff schedule. A client that stops being retried
    would be a worse bug than the noise.
    """
    mgr = _manager()
    client = _Client()

    for _ in range(10):
        mgr._note_reconnect_result(client)

    # Still tracked, still scheduled — just later.
    assert mgr._reconnect_failures[client.id] == 10
    assert mgr._reconnect_not_before[client.id] > 0


# ── the sweep itself, not just the bookkeeping ───────────────────────────────
#
# ⚠ A backoff the health loop does not consult changes nothing. These two drive
# `_health_loop` and assert the decision it actually makes.


class _Cfg:
    auto_connect = True


class _RegisteredClient(_Client):
    def __init__(self, client_id: str = "vscode-copilot") -> None:
        super().__init__(client_id)
        self.config = _Cfg()


async def _run_sweep_briefly(mgr: MCPClientManager, seconds: float = 0.2) -> None:
    """Run the real health loop for a moment, then cancel it."""
    import asyncio

    task = asyncio.create_task(mgr._health_loop())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_the_sweep_skips_a_client_inside_its_backoff(monkeypatch) -> None:
    """The bug, at the surface: a parked client must not be retried every 60s."""
    import time

    monkeypatch.setattr(reg, "_HEALTH_INTERVAL_S", 0.01)
    mgr = _manager()
    client = _RegisteredClient()
    mgr._clients[client.id] = client

    scheduled: list[str] = []

    async def _spy(c, delay=30.0):
        scheduled.append(c.id)

    monkeypatch.setattr(mgr, "_schedule_reconnect", _spy)

    # Parked for a minute, as one failed round would do.
    mgr._reconnect_not_before[client.id] = time.monotonic() + 60

    await _run_sweep_briefly(mgr)

    assert scheduled == [], (
        f"a client inside its backoff was retried {len(scheduled)} times — the "
        f"sweep is not consulting _reconnect_not_before"
    )


@pytest.mark.asyncio
async def test_the_sweep_still_retries_a_client_that_is_not_parked(monkeypatch) -> None:
    """The other half: backing off must not become never trying again."""
    monkeypatch.setattr(reg, "_HEALTH_INTERVAL_S", 0.01)
    mgr = _manager()
    client = _RegisteredClient()
    mgr._clients[client.id] = client

    scheduled: list[str] = []

    async def _spy(c, delay=30.0):
        scheduled.append(c.id)

    monkeypatch.setattr(mgr, "_schedule_reconnect", _spy)

    await _run_sweep_briefly(mgr)

    assert scheduled, "a client with no backoff was never retried at all"
