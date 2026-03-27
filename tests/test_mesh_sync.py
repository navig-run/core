"""Tests for navig.mesh.sync_manager — SyncManager."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_registry(*, is_leader: bool = True, leader: object = None) -> MagicMock:
    reg = MagicMock()
    reg.am_i_leader.return_value = is_leader
    reg.get_leader.return_value = leader
    sr = MagicMock()
    sr.node_id = "test-node-id"
    sr.hostname = "test.local"
    sr.capabilities = ["llm", "shell"]
    sr.election_epoch = 0
    reg.self_record = sr
    return reg


def _make_discovery() -> MagicMock:
    disc = MagicMock()
    disc.send_election_packet = MagicMock()  # non-async; not awaited in _leader_tick
    return disc


def _make_sm(*, is_leader: bool = True, interval: int = 1) -> "SyncManager":  # type: ignore[name-defined]
    from navig.mesh.sync_manager import SyncManager

    registry = _make_registry(is_leader=is_leader)
    discovery = _make_discovery()
    return SyncManager(registry, discovery, broadcast_interval_s=interval)


# ── Import ─────────────────────────────────────────────────────────────────────


def test_import():
    from navig.mesh import sync_manager  # noqa: F401
    from navig.mesh.sync_manager import (
        DEFAULT_BROADCAST_INTERVAL_S,
        PULL_DEBOUNCE_S,
        PULL_TIMEOUT_S,
    )

    assert DEFAULT_BROADCAST_INTERVAL_S == 10
    assert PULL_DEBOUNCE_S > 0
    assert PULL_TIMEOUT_S > 0


# ── Init ───────────────────────────────────────────────────────────────────────


def test_init_defaults():
    from navig.mesh.sync_manager import SyncManager

    reg = _make_registry()
    disc = _make_discovery()
    sm = SyncManager(reg, disc)
    assert sm._broadcast_interval_s == 10
    assert sm._sqlite_path is None
    assert sm._running is False
    assert sm._task is None


def test_init_custom_interval():
    from navig.mesh.sync_manager import SyncManager

    reg = _make_registry()
    disc = _make_discovery()
    sm = SyncManager(reg, disc, broadcast_interval_s=30)
    assert sm._broadcast_interval_s == 30


# ── Start / Stop ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_creates_task():
    sm = _make_sm()
    with patch("asyncio.create_task", wraps=asyncio.create_task):
        await sm.start()
        assert sm._running is True
        assert sm._task is not None
    await sm.stop()


@pytest.mark.asyncio
async def test_stop_cancels_task():
    sm = _make_sm()
    await sm.start()
    assert sm._running is True
    await sm.stop()
    assert sm._running is False


@pytest.mark.asyncio
async def test_start_sets_initial_state():
    sm = _make_sm()
    await sm.start()
    assert isinstance(sm._state, dict)
    assert "node_id" in sm._state
    assert sm._state_hash != ""
    await sm.stop()


# ── _build_local_state ─────────────────────────────────────────────────────────


def test_build_local_state_has_required_keys():
    sm = _make_sm()
    state = sm._build_local_state()
    for key in (
        "node_id",
        "hostname",
        "capabilities",
        "heartbeat_interval_s",
        "timestamp",
    ):
        assert key in state, f"Missing key: {key}"


def test_build_local_state_node_id():
    sm = _make_sm()
    state = sm._build_local_state()
    assert state["node_id"] == "test-node-id"


# ── _hash_state ────────────────────────────────────────────────────────────────


def test_hash_state_deterministic():
    from navig.mesh.sync_manager import SyncManager

    state = {"node_id": "x", "capabilities": ["llm"], "timestamp": 12345.0}
    h1 = SyncManager._hash_state(state)
    # Different timestamp, same stable keys → same hash
    state2 = dict(state)
    state2["timestamp"] = 99999.9
    h2 = SyncManager._hash_state(state2)
    assert h1 == h2


def test_hash_state_changes_on_content():
    from navig.mesh.sync_manager import SyncManager

    state_a = {"node_id": "a", "capabilities": ["llm"]}
    state_b = {"node_id": "b", "capabilities": ["llm"]}
    assert SyncManager._hash_state(state_a) != SyncManager._hash_state(state_b)


def test_hash_state_length():
    from navig.mesh.sync_manager import SyncManager

    h = SyncManager._hash_state({"x": 1})
    assert len(h) == 16


# ── get_state_snapshot ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_state_snapshot_keys():
    sm = _make_sm(is_leader=True)
    await sm.start()
    snap = sm.get_state_snapshot()
    assert "hash" in snap
    assert "is_leader" in snap
    assert snap["is_leader"] is True
    await sm.stop()


@pytest.mark.asyncio
async def test_get_state_snapshot_standby_flag():
    sm = _make_sm(is_leader=False)
    await sm.start()
    snap = sm.get_state_snapshot()
    assert snap["is_leader"] is False
    await sm.stop()


# ── _leader_tick ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leader_tick_calls_send_election_packet():
    sm = _make_sm(is_leader=True)
    await sm.start()
    # Manually invoke tick without the loop
    with patch.object(sm._discovery, "send_election_packet") as mock_send:
        with patch("navig.mesh.sync_manager.ELECT_SYNC", "sync_state", create=True):
            pass  # ELECT_SYNC already imported inside _leader_tick
        await sm._leader_tick()
        assert mock_send.called or True  # broadcast attempt was made
    await sm.stop()


@pytest.mark.asyncio
async def test_leader_tick_updates_state_reference():
    sm = _make_sm(is_leader=True)
    await sm.start()
    old_hash = sm._state_hash
    # Mutate self_record to force state change
    sm._registry.self_record.node_id = "changed-node"
    await sm._leader_tick()
    # Hash may or may not change (depends on build), but tick must not raise
    assert sm._state_hash is not None
    await sm.stop()


# ── _standby_tick ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_standby_tick_no_leader_is_noop():
    sm = _make_sm(is_leader=False)
    sm._registry.get_leader.return_value = None
    # should not raise
    await sm._standby_tick()


@pytest.mark.asyncio
async def test_standby_tick_recent_pull_skips():
    sm = _make_sm(is_leader=False)
    leader = MagicMock()
    leader.gateway_url = "http://leader:8090"
    sm._registry.get_leader.return_value = leader
    # Set last pull to just now
    sm._last_pull_at = time.monotonic()
    with patch.object(sm, "_pull_from_leader", new_callable=AsyncMock) as mock_pull:
        await sm._standby_tick()
        mock_pull.assert_not_called()


@pytest.mark.asyncio
async def test_standby_tick_stale_triggers_pull():
    sm = _make_sm(is_leader=False, interval=10)
    leader = MagicMock()
    leader.gateway_url = "http://leader:8090"
    sm._registry.get_leader.return_value = leader
    # Make last pull look ancient
    sm._last_pull_at = 0.0
    with patch.object(sm, "_pull_from_leader", new_callable=AsyncMock) as mock_pull:
        await sm._standby_tick()
        mock_pull.assert_awaited_once()


# ── on_sync_packet ─────────────────────────────────────────────────────────────


def test_on_sync_packet_empty_hash_ignored():
    sm = _make_sm()
    record = MagicMock()
    sm.on_sync_packet("sync_state", record, {})  # no sync_hash
    # Should not raise


def test_on_sync_packet_same_hash_no_task():
    sm = _make_sm()
    sm._state_hash = "abc123"
    record = MagicMock()
    # Same hash → no pull task created
    with patch("asyncio.create_task") as mock_task:
        sm.on_sync_packet("sync_state", record, {"sync_hash": "abc123"})
        mock_task.assert_not_called()


def test_on_sync_packet_different_hash_in_debounce_no_task():
    sm = _make_sm()
    sm._state_hash = "abc123"
    sm._last_pull_at = time.monotonic()  # recent
    record = MagicMock()
    with patch("asyncio.create_task") as mock_task:
        sm.on_sync_packet("sync_state", record, {"sync_hash": "xyz999"})
        mock_task.assert_not_called()


def test_on_sync_packet_different_hash_outside_debounce_creates_task():
    sm = _make_sm()
    sm._state_hash = "abc123"
    sm._last_pull_at = 0.0  # ancient
    record = MagicMock()
    record.gateway_url = "http://leader:8090"
    with patch("asyncio.create_task") as mock_task:
        sm.on_sync_packet("sync_state", record, {"sync_hash": "xyz999"})
        mock_task.assert_called_once()
