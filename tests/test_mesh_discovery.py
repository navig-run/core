"""
Integration test for MeshDiscovery — two in-process instances communicating
via a mocked packet exchange (no real UDP sockets required).

Approach:
  1. Build two NodeRegistry instances (registryA, registryB) with distinct
     node IDs in temp dirs.
  2. Build corresponding MeshDiscovery instances.
  3. Override socket creation so start() succeeds without real sockets.
  4. Directly call _handle_packet on each instance with packets built by the
     other registry — simulating what would happen over the real multicast.
  5. Assert peer registration, hello-response, goodbye cleanup.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from navig.mesh.discovery import MeshDiscovery, _build_packet
from navig.mesh.registry import NodeRegistry


def _make_registry(tmp_path: Path, node_suffix: str) -> NodeRegistry:
    """Create a minimal NodeRegistry without real network calls."""
    with (
        patch(
            "navig.mesh.registry._derive_node_id",
            return_value=f"navig-test-{node_suffix}",
        ),
        patch("navig.mesh.registry.NodeRegistry._local_ip", return_value="127.0.0.1"),
        patch("navig.mesh.registry._measure_load", return_value=0.1),
        patch(
            "navig.mesh.registry.NodeRegistry._detect_capabilities",
            return_value=["llm", "shell"],
        ),
    ):
        return NodeRegistry(storage_dir=tmp_path)


def _make_discovery(registry: NodeRegistry) -> MeshDiscovery:
    """Create a MeshDiscovery without real sockets — sockets are stubbed."""
    d = MeshDiscovery(registry)
    # Stub out send so it doesn't touch real sockets
    d._sender = MagicMock()
    d._receiver = MagicMock()
    d._sender.sendto = MagicMock()
    return d


class TestMeshDiscoveryIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for two MeshDiscovery instances exchanging packets."""

    async def asyncSetUp(self):
        self._tmpA = tempfile.TemporaryDirectory()
        self._tmpB = tempfile.TemporaryDirectory()
        self.regA = _make_registry(Path(self._tmpA.name), "nodeA-0001")
        self.regB = _make_registry(Path(self._tmpB.name), "nodeB-0002")
        self.discA = _make_discovery(self.regA)
        self.discB = _make_discovery(self.regB)
        # Mark running so _handle_packet / _send don't bail early
        self.discA._running = True
        self.discB._running = True

    async def asyncTearDown(self):
        self._tmpA.cleanup()
        self._tmpB.cleanup()

    # ------------------------------------------------------------------ helpers

    async def _deliver_hello(self, src: MeshDiscovery, dst: MeshDiscovery) -> None:
        """Build a HELLO from src and deliver it directly to dst._handle_packet."""
        packet = _build_packet(src._registry, "hello")
        # Suppress the reply _send (it would try to use the real socket)
        with patch.object(dst, "_send", new=AsyncMock()):
            await dst._handle_packet(packet, "127.0.0.1")

    async def _deliver_goodbye(self, src: MeshDiscovery, dst: MeshDiscovery) -> None:
        packet = _build_packet(src._registry, "goodbye")
        await dst._handle_packet(packet, "127.0.0.1")

    async def _deliver_heartbeat(self, src: MeshDiscovery, dst: MeshDiscovery) -> None:
        packet = _build_packet(src._registry, "heartbeat")
        await dst._handle_packet(packet, "127.0.0.1")

    # ------------------------------------------------------------------ tests

    async def test_hello_registers_peer(self):
        """Delivering a HELLO from A → B causes B to register A as a peer."""
        self.assertEqual(len(self.regB.get_peers()), 0)

        await self._deliver_hello(self.discA, self.discB)

        peers = self.regB.get_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].node_id, self.regA.self_record.node_id)
        self.assertEqual(peers[0].hostname, self.regA.self_record.hostname)
        self.assertFalse(peers[0].is_self)

    async def test_hello_triggers_reply(self):
        """When B receives a HELLO from A, B should reply with its own HELLO."""
        with patch.object(self.discB, "_send", new=AsyncMock()) as mock_send:
            packet = _build_packet(self.discA._registry, "hello")
            await self.discB._handle_packet(packet, "127.0.0.1")

        mock_send.assert_awaited_once_with("hello")

    async def test_heartbeat_upserts_peer(self):
        """A heartbeat updates last_seen but doesn't duplicate peers."""
        await self._deliver_hello(self.discA, self.discB)
        self.assertEqual(len(self.regB.get_peers()), 1)

        old_ts = self.regB.get_peers()[0].last_seen

        # Small sleep then heartbeat
        await asyncio.sleep(0.01)
        await self._deliver_heartbeat(self.discA, self.discB)

        peers = self.regB.get_peers()
        self.assertEqual(len(peers), 1, "Should still be exactly one peer")
        self.assertGreaterEqual(peers[0].last_seen, old_ts)

    async def test_heartbeat_does_not_trigger_reply(self):
        """Heartbeats should NOT trigger a HELLO reply (only initial HELLO does)."""
        with patch.object(self.discB, "_send", new=AsyncMock()) as mock_send:
            packet = _build_packet(self.discA._registry, "heartbeat")
            await self.discB._handle_packet(packet, "127.0.0.1")

        mock_send.assert_not_awaited()

    async def test_goodbye_removes_peer(self):
        """A goodbye packet removes the peer from the registry."""
        await self._deliver_hello(self.discA, self.discB)
        self.assertEqual(len(self.regB.get_peers()), 1)

        await self._deliver_goodbye(self.discA, self.discB)

        self.assertEqual(len(self.regB.get_peers()), 0)

    async def test_own_packet_ignored(self):
        """Packets from self (same node_id) must be silently ignored."""
        # A receiving its own HELLO — should not add itself as a peer (already self)
        packet = _build_packet(self.regA, "hello")
        with patch.object(self.discA, "_send", new=AsyncMock()) as mock_send:
            await self.discA._handle_packet(packet, "127.0.0.1")

        # No reply, no new peer
        mock_send.assert_not_awaited()
        # get_peers() returns non-self peers only
        self.assertEqual(len(self.regA.get_peers()), 0)

    async def test_malformed_packet_ignored(self):
        """Malformed bytes must not crash the discovery loop."""
        await self.discB._handle_packet(b"not json at all!!!", "127.0.0.1")
        self.assertEqual(len(self.regB.get_peers()), 0)

    async def test_wrong_version_packet_ignored(self):
        """Packets with v != 1 must be silently dropped."""
        import json

        bad = json.dumps({"v": 99, "type": "hello", "node_id": "x"}).encode()
        await self.discB._handle_packet(bad, "127.0.0.1")
        self.assertEqual(len(self.regB.get_peers()), 0)

    async def test_bidirectional_discovery(self):
        """A discovers B AND B discovers A through mutual HELLO exchange."""
        await self._deliver_hello(self.discA, self.discB)  # A → B
        await self._deliver_hello(self.discB, self.discA)  # B → A

        peer_ids_seen_by_B = [p.node_id for p in self.regB.get_peers()]
        peer_ids_seen_by_A = [p.node_id for p in self.regA.get_peers()]

        self.assertIn(self.regA.self_record.node_id, peer_ids_seen_by_B)
        self.assertIn(self.regB.self_record.node_id, peer_ids_seen_by_A)

    async def test_capability_preserved(self):
        """Capabilities from the packet are faithfully stored in the peer record."""
        packet = _build_packet(self.regA, "hello")
        with patch.object(self.discB, "_send", new=AsyncMock()):
            await self.discB._handle_packet(packet, "127.0.0.1")

        peer = self.regB.get_peers()[0]
        self.assertIn("llm", peer.capabilities)
        self.assertIn("shell", peer.capabilities)

    async def test_start_sends_hello(self):
        """start() announces the node with an immediate HELLO."""
        new_disc = MeshDiscovery(self.regA)
        # Stub socket creation to avoid real network
        mock_sock = MagicMock()
        mock_sock.sendto = MagicMock()
        mock_sock.settimeout = MagicMock()
        mock_sock.recvfrom = MagicMock(side_effect=TimeoutError)

        with (
            patch("navig.mesh.discovery._create_sender_socket", return_value=mock_sock),
            patch("navig.mesh.discovery._create_receiver_socket", return_value=mock_sock),
        ):
            await new_disc.start()
            # Give tasks time to call sendto
            await asyncio.sleep(0.05)
            await new_disc.stop()

        # sendto should have been called (hello + goodbye)
        self.assertGreater(mock_sock.sendto.call_count, 0)


if __name__ == "__main__":
    unittest.main()
