"""A dropped MCP client must be reconnected, not abandoned for the daemon's lifetime.

`MCPClientManager` shipped a full reconnect apparatus — `_reconnect_tasks`,
`_schedule_reconnect`, `reconnect_client` — that nothing ever triggered:

* `_schedule_reconnect` had **zero callers**;
* `_connect_with_retry` just logged an error after its 3 attempts and returned;
* and `_schedule_reconnect`'s inner guard was `if not client.is_connected and
  self._started`, while **production never calls `start()`** — every live call site
  (`gateway/server.py`, `gateway/routes/mcp.py`, `daemon/telegram_worker.py`) uses
  `add_client()`, whose docstring says it works "regardless of whether start has been
  called". So `_started` stayed `False` and the reconnect would have no-op'd anyway.

Net effect: once a client went down — the server exits, the network blips, the initial
connect fails, or the transport's reader task dies (#692/#694 made that honestly visible
as `is_connected == False`) — it was filtered out of `find_tool`/`get_all_tools` and
never came back. Its tools silently vanished until the daemon restarted.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from navig.mcp import MCPClientManager


def _down_client(client_id: str = "srv", *, auto_connect: bool = True) -> MagicMock:
    """A registered client that reports itself disconnected."""
    client = MagicMock()
    client.id = client_id
    client.is_connected = False
    client.config = MagicMock(auto_connect=auto_connect)
    client.disconnect = AsyncMock()
    return client


class TestScheduleReconnect:
    async def test_schedule_reconnect_actually_reconnects(self):
        """THE REGRESSION: a scheduled reconnect must attempt the connection.

        Pre-fix it was gated on `self._started`, which production never sets, so the
        task woke up and did nothing.
        """
        manager = MCPClientManager()
        client = _down_client()
        manager._clients["srv"] = client
        manager._connect_with_retry = AsyncMock()  # type: ignore[method-assign]

        await manager._schedule_reconnect(client, delay=0.0)
        await manager._reconnect_tasks["srv"]

        manager._connect_with_retry.assert_awaited_once()
        assert "srv" not in manager._reconnect_tasks  # dedupe slot released

    async def test_deregistered_client_is_not_reconnected(self):
        """A client removed from the registry must NOT be resurrected."""
        manager = MCPClientManager()
        client = _down_client()
        # deliberately NOT in manager._clients (i.e. remove_client() ran)
        manager._connect_with_retry = AsyncMock()  # type: ignore[method-assign]

        await manager._schedule_reconnect(client, delay=0.0)
        await manager._reconnect_tasks["srv"]

        manager._connect_with_retry.assert_not_awaited()

    async def test_reconnect_slot_released_when_connect_raises(self):
        """A raising reconnect must still free the dedupe slot.

        Pre-fix the `_reconnect_tasks.pop` sat after the await, so one exception parked
        the client id forever and every later attempt returned early as "already
        scheduled" — a permanent wedge. (`_started` is set here to isolate the finally
        from the gate bug above.)
        """
        manager = MCPClientManager()
        manager._started = True
        client = _down_client()
        manager._clients["srv"] = client
        manager._connect_with_retry = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("connect blew up")
        )

        await manager._schedule_reconnect(client, delay=0.0)
        await asyncio.gather(manager._reconnect_tasks["srv"], return_exceptions=True)

        assert "srv" not in manager._reconnect_tasks, (
            "dedupe slot wedged — this client can never be reconnected again"
        )


class TestHealthSweep:
    async def test_health_sweep_reconnects_a_dropped_client(self, monkeypatch):
        """Nothing pushes a drop to the manager, so a sweep has to look for it."""
        monkeypatch.setattr("navig.mcp.registry._HEALTH_INTERVAL_S", 0.01)
        manager = MCPClientManager()
        client = _down_client()
        manager._clients["srv"] = client
        manager._connect_with_retry = AsyncMock()  # type: ignore[method-assign]

        manager._ensure_health_loop()
        try:
            for _ in range(50):  # bounded wait — no fixed sleep
                await asyncio.sleep(0.01)
                if manager._connect_with_retry.await_count:
                    break
            assert manager._connect_with_retry.await_count >= 1
        finally:
            await manager.stop()

    async def test_health_sweep_leaves_connected_clients_alone(self, monkeypatch):
        monkeypatch.setattr("navig.mcp.registry._HEALTH_INTERVAL_S", 0.01)
        manager = MCPClientManager()
        client = _down_client()
        client.is_connected = True  # healthy — must not be touched
        manager._clients["srv"] = client
        manager._connect_with_retry = AsyncMock()  # type: ignore[method-assign]

        manager._ensure_health_loop()
        try:
            await asyncio.sleep(0.05)
            manager._connect_with_retry.assert_not_awaited()
        finally:
            await manager.stop()

    async def test_one_bad_client_does_not_kill_the_sweep(self, monkeypatch):
        """A client whose state raises must not stop the others being reconnected."""
        monkeypatch.setattr("navig.mcp.registry._HEALTH_INTERVAL_S", 0.01)
        manager = MCPClientManager()

        bad = _down_client("bad")
        type(bad).config = property(  # type: ignore[assignment]
            lambda _self: (_ for _ in ()).throw(RuntimeError("bad config"))
        )
        good = _down_client("good")
        manager._clients["bad"] = bad
        manager._clients["good"] = good
        manager._connect_with_retry = AsyncMock()  # type: ignore[method-assign]

        manager._ensure_health_loop()
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if manager._connect_with_retry.await_count:
                    break
            assert manager._connect_with_retry.await_count >= 1
        finally:
            await manager.stop()
            del type(bad).config

    async def test_stop_cancels_the_health_sweep(self, monkeypatch):
        monkeypatch.setattr("navig.mcp.registry._HEALTH_INTERVAL_S", 0.01)
        manager = MCPClientManager()
        manager._ensure_health_loop()
        task = manager._health_task
        assert task is not None

        await manager.stop()

        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        assert manager._health_task is None

    async def test_ensure_health_loop_is_idempotent(self, monkeypatch):
        monkeypatch.setattr("navig.mcp.registry._HEALTH_INTERVAL_S", 0.01)
        manager = MCPClientManager()
        manager._ensure_health_loop()
        first = manager._health_task
        manager._ensure_health_loop()
        try:
            assert manager._health_task is first  # no second sweep task
        finally:
            await manager.stop()


class TestAddClientWiring:
    async def test_add_client_starts_the_health_sweep(self):
        """The production entry point (add_client, not start) must arm the sweep."""
        manager = MCPClientManager()
        from navig.mcp.client import MCPClientConfig

        cfg = MCPClientConfig(id="srv", command="echo", args=["hi"], auto_connect=True)
        await manager.add_client(cfg)
        try:
            assert manager._health_task is not None
            assert not manager._health_task.done()
        finally:
            await manager.stop()
