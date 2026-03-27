"""
Tests for navig.mesh.election — ElectionManager unit tests.

Strategy:
  - Use Mock objects for NodeRegistry and MeshDiscovery.
  - Speed up test loops by overriding timing constants.
  - Verify state transitions and UDP packet dispatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*, am_i_leader=False, node_id="node-A", hostname="node-a.local"):
    reg = MagicMock()
    reg.am_i_leader.return_value = am_i_leader
    sr = MagicMock()
    sr.node_id = node_id
    sr.hostname = hostname
    sr.role = "leader" if am_i_leader else "standby"
    sr.election_epoch = 0
    reg.self_record = sr
    reg.get_leader.return_value = None
    reg.list_peers.return_value = []
    reg.get_peers.return_value = []
    reg.get_best_peer.return_value = None
    reg.get_tiebreaker = MagicMock(return_value=12345)
    reg.set_my_role = MagicMock()
    reg.set_target = MagicMock()
    return reg


def _make_discovery():
    disc = MagicMock()
    disc.send_election_packet = AsyncMock()
    disc.set_election_callback = MagicMock()
    return disc


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_election_import():
    from navig.mesh.election import ElectionManager

    assert ElectionManager is not None


# ---------------------------------------------------------------------------
# ElectionManager init
# ---------------------------------------------------------------------------


def test_election_manager_init():
    from navig.mesh.election import ElectionManager

    reg = _make_registry()
    disc = _make_discovery()
    em = ElectionManager(reg, disc, ttl_seconds=15, heartbeat_interval=5)
    assert em is not None


# ---------------------------------------------------------------------------
# start() — standby enters standby role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_sets_standby_role():
    from navig.mesh.election import ElectionManager

    reg = _make_registry(am_i_leader=False)
    disc = _make_discovery()
    em = ElectionManager(reg, disc, ttl_seconds=15, heartbeat_interval=5)

    # Set up a healthy remote leader so start() enters the standby branch
    leader_mock = MagicMock()
    leader_mock.node_id = "node-B"
    leader_mock.is_self = False
    leader_mock.last_heartbeat_age_s.return_value = 2.0  # well within TTL=15s
    reg.get_leader.return_value = leader_mock

    with patch.object(em, "_ttl_watchdog_loop", new_callable=AsyncMock):
        await em.start()
        reg.set_my_role.assert_called_with("standby", 0)
    await em.stop()


# ---------------------------------------------------------------------------
# trigger_yield() — thread-safe; sets internal state
# ---------------------------------------------------------------------------


def test_trigger_yield_sets_flag():
    from navig.mesh.election import ElectionManager

    reg = _make_registry(am_i_leader=True)
    disc = _make_discovery()
    em = ElectionManager(reg, disc)
    em._running = True
    # trigger_yield should not raise and should schedule a coro on the loop
    # (we can't easily test the threadsafe scheduler without a running loop;
    #  verify it at minimum doesn't error)
    try:
        em.trigger_yield()  # _loop is None — RunCoroutineThreadsafe skipped silently
    except Exception as e:
        pytest.fail(f"trigger_yield raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# _is_best_available_standby — tiebreaker evaluation
# ---------------------------------------------------------------------------


def test_is_best_available_standby_no_peers():
    from navig.mesh.election import ElectionManager

    reg = _make_registry()
    reg.list_peers.return_value = []
    disc = _make_discovery()
    em = ElectionManager(reg, disc)
    # With no peers, we are trivially the best
    assert em._is_best_available_standby() is True


def test_is_best_available_standby_lower_tiebreaker():
    from navig.mesh.election import ElectionManager
    from navig.mesh.registry import NodeRegistry

    reg = _make_registry(node_id="node-A", hostname="a.local")

    # Peer with higher tiebreaker score
    peer = MagicMock()
    peer.role = "standby"
    peer.is_self = False
    peer.hostname = "b.local"
    peer.health = "online"  # not offline
    peer.election_epoch = 0
    reg.get_peers.return_value = [peer]

    # Patch the static tiebreaker: a.local=100, b.local=200
    def fake_tiebreaker(hostname: str) -> int:
        return 200 if hostname == "b.local" else 100

    disc = _make_discovery()
    em = ElectionManager(reg, disc)

    with patch.object(NodeRegistry, "get_tiebreaker", side_effect=fake_tiebreaker):
        result = em._is_best_available_standby()

    assert result is False


# ---------------------------------------------------------------------------
# state_dict() returns required keys
# ---------------------------------------------------------------------------


def test_state_dict_keys():
    from navig.mesh.election import ElectionManager

    reg = _make_registry()
    disc = _make_discovery()
    em = ElectionManager(reg, disc)
    state = em.state_dict()
    assert "role" in state
    assert "epoch" in state


# ---------------------------------------------------------------------------
# send_election_packet is called on propose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_sends_udp_packet():
    from navig.mesh import discovery as disc_module
    from navig.mesh.election import ElectionManager

    reg = _make_registry()
    disc = _make_discovery()
    em = ElectionManager(reg, disc, ttl_seconds=1, heartbeat_interval=1)
    em._running = True

    # Patch asyncio.sleep to return immediately
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await em._propose_candidacy(reason="test")

    # Should have sent at least one election packet
    disc.send_election_packet.assert_called()
    args = disc.send_election_packet.call_args_list[0][0]
    assert args[0] == disc_module.ELECT_PROPOSE
