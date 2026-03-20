"""Tests for navig.mesh.collective — PartialResultBus, TaskDecomposer,
LeaderAggregator, MeshCollective."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Import ─────────────────────────────────────────────────────────────────────

def test_import():
    from navig.mesh import collective  # noqa: F401
    from navig.mesh.collective import (
        PartialResultBus,
        TaskDecomposer,
        LeaderAggregator,
        MeshCollective,
        SUBTASK_TIMEOUT_S,
        MAX_PARALLEL_SUBTASKS,
    )
    assert SUBTASK_TIMEOUT_S > 0
    assert MAX_PARALLEL_SUBTASKS >= 2


# ── PartialResultBus ───────────────────────────────────────────────────────────

def test_bus_subscribe_and_publish():
    from navig.mesh.collective import PartialResultBus
    bus = PartialResultBus()
    received = []
    bus.subscribe("task1", received.append)
    bus.publish("task1", {"text": "hello"})
    assert received == [{"text": "hello"}]


def test_bus_publish_no_subscribers_noop():
    from navig.mesh.collective import PartialResultBus
    bus = PartialResultBus()
    bus.publish("task-unknown", {"text": "x"})  # must not raise


def test_bus_unsubscribe_clears_callbacks():
    from navig.mesh.collective import PartialResultBus
    bus = PartialResultBus()
    received = []
    bus.subscribe("task1", received.append)
    bus.unsubscribe("task1")
    bus.publish("task1", {"text": "after-unsub"})
    assert received == []


def test_bus_multiple_subscribers():
    from navig.mesh.collective import PartialResultBus
    bus = PartialResultBus()
    a, b = [], []
    bus.subscribe("t", a.append)
    bus.subscribe("t", b.append)
    bus.publish("t", {"x": 1})
    assert len(a) == 1
    assert len(b) == 1


def test_bus_callback_exception_does_not_propagate():
    from navig.mesh.collective import PartialResultBus

    def bad_cb(r):
        raise ValueError("boom")

    bus = PartialResultBus()
    bus.subscribe("t", bad_cb)
    bus.publish("t", {"x": 1})  # must not raise


# ── TaskDecomposer ─────────────────────────────────────────────────────────────

def test_decomposer_short_task_no_split():
    from navig.mesh.collective import TaskDecomposer
    d = TaskDecomposer()
    result = d.decompose("Short task", peer_count=4)
    assert result == ["Short task"]


def test_decomposer_single_peer_no_split():
    from navig.mesh.collective import TaskDecomposer
    long_task = ("This is a very long sentence. " * 20).strip()
    d = TaskDecomposer()
    result = d.decompose(long_task, peer_count=1)
    assert len(result) == 1


def test_decomposer_long_task_splits():
    from navig.mesh.collective import TaskDecomposer
    # Build something with enough sentences and length
    task = ". ".join([f"Sentence number {i} about something interesting" for i in range(20)])
    d = TaskDecomposer()
    result = d.decompose(task, peer_count=4)
    assert len(result) >= 2


def test_decomposer_chunks_capped_at_max():
    from navig.mesh.collective import TaskDecomposer, MAX_PARALLEL_SUBTASKS
    task = ". ".join([f"Sentence {i} is here and has content" for i in range(100)])
    d = TaskDecomposer()
    result = d.decompose(task, peer_count=20)
    assert len(result) <= MAX_PARALLEL_SUBTASKS


def test_decomposer_empty_task():
    from navig.mesh.collective import TaskDecomposer
    d = TaskDecomposer()
    result = d.decompose("", peer_count=3)
    assert result == [""]


# ── LeaderAggregator ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aggregator_collects_expected():
    from navig.mesh.collective import LeaderAggregator
    agg = LeaderAggregator("task1", expected_count=2, timeout_s=5)
    agg.on_partial({"text": "part1"})
    agg.on_partial({"text": "part2"})
    results = await agg.wait()
    assert len(results) == 2


@pytest.mark.asyncio
async def test_aggregator_timeout_returns_partial():
    from navig.mesh.collective import LeaderAggregator
    agg = LeaderAggregator("task1", expected_count=5, timeout_s=0.05)
    agg.on_partial({"text": "only_one"})
    results = await agg.wait()
    # Should return what arrived before timeout
    assert len(results) == 1


def test_assemble_empty_returns_empty():
    from navig.mesh.collective import LeaderAggregator
    out = LeaderAggregator.assemble([], "original")
    assert out == ""


def test_assemble_joins_text():
    from navig.mesh.collective import LeaderAggregator
    results = [{"text": "alpha"}, {"text": "beta"}, {"output": "gamma"}]
    out = LeaderAggregator.assemble(results, "original")
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out


def test_assemble_skips_empty_parts():
    from navig.mesh.collective import LeaderAggregator
    results = [{"text": ""}, {"text": "real"}, {}]
    out = LeaderAggregator.assemble(results, "original")
    assert out == "real"


# ── MeshCollective – init & start ─────────────────────────────────────────────

def test_mesh_collective_init():
    from navig.mesh.collective import MeshCollective
    reg = MagicMock()
    disc = MagicMock()
    mc = MeshCollective(reg, disc)
    assert mc._enabled is False


@pytest.mark.asyncio
async def test_mesh_collective_start_disabled_by_default():
    from navig.mesh.collective import MeshCollective
    reg = MagicMock()
    disc = MagicMock()
    mc = MeshCollective(reg, disc)

    # Patch config to return collective_enabled=False
    mock_cfg = MagicMock()
    mock_cfg.global_config.get.return_value = {"collective_enabled": False}
    with patch("navig.config.get_config_manager", return_value=mock_cfg):
        await mc.start()

    assert mc._enabled is False


@pytest.mark.asyncio
async def test_mesh_collective_start_enabled_by_config():
    from navig.mesh.collective import MeshCollective
    reg = MagicMock()
    disc = MagicMock()
    mc = MeshCollective(reg, disc)

    mock_cfg = MagicMock()
    mock_cfg.global_config.get.return_value = {"collective_enabled": True}
    # collective.start() does `from navig.config import get_config_manager` inline
    with patch("navig.config.get_config_manager", return_value=mock_cfg):
        await mc.start()

    assert mc._enabled is True
    await mc.stop()


@pytest.mark.asyncio
async def test_mesh_collective_stop_disables():
    from navig.mesh.collective import MeshCollective
    mc = MeshCollective(MagicMock(), MagicMock())
    mc._enabled = True
    await mc.stop()
    assert mc._enabled is False


# ── MeshCollective.run – disabled path ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_disabled_no_local_fn_returns_empty():
    from navig.mesh.collective import MeshCollective
    mc = MeshCollective(MagicMock(), MagicMock())
    mc._enabled = False
    result = await mc.run("do something", local_fn=None)
    assert result == ""


@pytest.mark.asyncio
async def test_run_disabled_calls_local_fn():
    from navig.mesh.collective import MeshCollective
    mc = MeshCollective(MagicMock(), MagicMock())
    mc._enabled = False

    async def local(text: str) -> str:
        return f"local:{text}"

    result = await mc.run("my task", local_fn=local)
    assert result == "local:my task"


@pytest.mark.asyncio
async def test_run_not_leader_calls_local():
    from navig.mesh.collective import MeshCollective
    reg = MagicMock()
    reg.am_i_leader.return_value = False
    mc = MeshCollective(reg, MagicMock())
    mc._enabled = True

    async def local(text: str) -> str:
        return "local-result"

    result = await mc.run("task", local_fn=local)
    assert result == "local-result"


@pytest.mark.asyncio
async def test_run_leader_no_peers_calls_local():
    from navig.mesh.collective import MeshCollective
    reg = MagicMock()
    reg.am_i_leader.return_value = True
    reg.list_peers.return_value = []
    mc = MeshCollective(reg, MagicMock())
    mc._enabled = True

    async def local(text: str) -> str:
        return "solo-result"

    result = await mc.run("task", local_fn=local)
    assert result == "solo-result"


# ── notify_partial ─────────────────────────────────────────────────────────────

def test_notify_partial_publishes_to_bus():
    from navig.mesh.collective import MeshCollective
    mc = MeshCollective(MagicMock(), MagicMock())
    received = []
    mc._bus.subscribe("t1", received.append)
    mc.notify_partial("t1", {"text": "part"})
    assert received == [{"text": "part"}]
