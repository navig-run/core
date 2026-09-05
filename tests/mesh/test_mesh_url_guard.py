"""A multicast packet must not be able to point the daemon at an off-LAN host.

`MeshDiscovery._parse_packet` stored a packet's ``gateway_url`` **verbatim** — no validation
anywhere in `core/navig/mesh/`. The daemon then makes outbound HTTP to that URL:

* ``discovery._probe_peer``  -> ``GET  {gateway_url}/health``   — automatic, every 20 s
* ``router._forward``        -> ``POST {gateway_url}/llm/chat`` — **carrying
  ``Authorization: Bearer <gateway.mesh_token>``**

so one unauthenticated UDP datagram turned the daemon into an SSRF client *and* leaked the
LAN bootstrap credential. Unauthenticated because ``_parse_packet`` only verifies the HMAC
``if secret is not None``, and ``mesh.secret`` is not set by default; the receiver binds
``0.0.0.0:5354``, so any LAN device or local process can send one. Declaring ``load: 0.0``
also makes the hostile node win ``get_best_peer()`` outright (lower score wins).

The repo had already written this guard — ``gateway/routes/mesh.py::_is_safe_mesh_url``,
*"Mesh is LAN-only by design — block SSRF to public hosts / cloud metadata"* — but wired it
only to the **manual** ``/mesh/ping`` bootstrap. That asymmetry was the bug: the
authenticated path rejected ``169.254.169.254`` while the unauthenticated one accepted it.
Both now share ``navig.mesh.url_guard``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from navig.mesh.discovery import MeshDiscovery, _build_packet
from navig.mesh.registry import NodeRegistry
from navig.mesh.url_guard import ip_is_lan, is_lan_gateway_url

# Hosts a hostile packet would name. 169.254.169.254 is the cloud metadata endpoint —
# link-local, which Python counts as *private*, so it must be excluded explicitly.
HOSTILE_URLS = [
    "http://169.254.169.254",
    "http://169.254.169.254/latest/meta-data/",
    "http://93.184.216.34:8789",
    "https://attacker.example.com:8789",
    "http://attacker.example.com",
]


class TestIpIsLan(unittest.TestCase):
    def test_private_and_loopback_are_lan(self):
        for ip in ("192.168.1.50", "10.0.0.7", "172.16.4.1", "127.0.0.1", "::1"):
            self.assertTrue(ip_is_lan(ip), ip)

    def test_public_is_not_lan(self):
        for ip in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertFalse(ip_is_lan(ip), ip)

    def test_link_local_is_not_lan(self):
        """169.254.0.0/16 is `is_private` in Python — the metadata endpoint must not slip
        through on that technicality."""
        for ip in ("169.254.169.254", "169.254.1.1"):
            self.assertTrue(__import__("ipaddress").ip_address(ip).is_private)  # premise
            self.assertFalse(ip_is_lan(ip), ip)

    def test_non_address_is_not_lan(self):
        for s in ("", "not-an-ip", "example.com", "999.999.999.999"):
            self.assertFalse(ip_is_lan(s), s)


class TestIsLanGatewayUrl(unittest.TestCase):
    def test_accepts_lan_ip_literals(self):
        for url in ("http://192.168.1.50:8789", "http://127.0.0.1:8789", "https://10.0.0.7"):
            self.assertTrue(is_lan_gateway_url(url), url)

    def test_rejects_hostile_urls(self):
        for url in HOSTILE_URLS:
            self.assertFalse(is_lan_gateway_url(url), url)

    def test_rejects_hostnames_even_if_they_would_resolve_to_lan(self):
        """The multicast path must never resolve names — `_handle_packet` runs on the
        gateway event loop, so a slow-resolving host would be a DoS. A real node always
        announces an IP literal (registry builds http://{_local_ip()}:{port})."""
        self.assertFalse(is_lan_gateway_url("http://localhost:8789"))
        self.assertFalse(is_lan_gateway_url("http://my-nas.local:8789"))

    def test_rejects_bad_scheme_and_malformed(self):
        for url in ("file:///etc/passwd", "ftp://192.168.1.5", "gopher://10.0.0.1", "", "::::"):
            self.assertFalse(is_lan_gateway_url(url), url)

    def test_rejects_non_string(self):
        for bad in (None, 123, {}, []):
            self.assertFalse(is_lan_gateway_url(bad))  # type: ignore[arg-type]


def _registry(tmp: Path, suffix: str) -> NodeRegistry:
    with (
        patch("navig.mesh.registry._derive_node_id", return_value=f"navig-test-{suffix}"),
        patch("navig.mesh.registry.NodeRegistry._local_ip", return_value="127.0.0.1"),
        patch("navig.mesh.registry._measure_load", return_value=0.1),
        patch(
            "navig.mesh.registry.NodeRegistry._detect_capabilities",
            return_value=["llm", "shell"],
        ),
    ):
        return NodeRegistry(storage_dir=tmp)


class TestHostilePacketIsDropped(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpA = tempfile.TemporaryDirectory()
        self._tmpB = tempfile.TemporaryDirectory()
        self.regA = _registry(Path(self._tmpA.name), "A-0001")
        self.regB = _registry(Path(self._tmpB.name), "B-0002")
        self.discB = MeshDiscovery(self.regB)
        self.discB._sender = MagicMock()
        self.discB._receiver = MagicMock()
        self.discB._running = True

    def tearDown(self):
        self._tmpA.cleanup()
        self._tmpB.cleanup()

    def _packet_with_url(self, url: str) -> bytes:
        return _build_packet(self.regA, "hello", extra={"gateway_url": url})

    async def test_non_lan_gateway_url_is_never_registered(self):
        """THE REGRESSION: pre-fix each of these registered a peer the daemon would then
        probe every 20 s and forward the mesh_token to."""
        for url in HOSTILE_URLS:
            with patch.object(self.discB, "_send", new=AsyncMock()):
                await self.discB._handle_packet(self._packet_with_url(url), "127.0.0.1")
            self.assertEqual(
                self.regB.get_peers(), [], f"accepted a non-LAN peer: {url}"
            )
            self.assertIsNone(self.regB.get_best_peer())

    async def test_hostile_peer_cannot_win_the_routing_race(self):
        """load=0.0 scores 0.0 and lower wins, so pre-fix the injected node outranked every
        real one — get_best_peer() is what router._forward sends the mesh_token to."""
        packet = _build_packet(
            self.regA,
            "hello",
            extra={"gateway_url": "http://169.254.169.254", "load": 0.0},
        )
        with patch.object(self.discB, "_send", new=AsyncMock()):
            await self.discB._handle_packet(packet, "127.0.0.1")
        self.assertIsNone(self.regB.get_best_peer())

    async def test_lan_peer_is_still_accepted(self):
        """The guard must not break real LAN discovery."""
        with patch.object(self.discB, "_send", new=AsyncMock()):
            await self.discB._handle_packet(
                self._packet_with_url("http://192.168.1.50:8789"), "127.0.0.1"
            )
        peers = self.regB.get_peers()
        self.assertEqual([p.gateway_url for p in peers], ["http://192.168.1.50:8789"])

    async def test_unmodified_self_record_packet_still_flows(self):
        """A packet built straight from a real registry (loopback URL) is unaffected."""
        with patch.object(self.discB, "_send", new=AsyncMock()):
            await self.discB._handle_packet(_build_packet(self.regA, "hello"), "127.0.0.1")
        self.assertEqual(len(self.regB.get_peers()), 1)

    async def test_missing_gateway_url_is_dropped(self):
        packet = json.dumps(
            {**json.loads(_build_packet(self.regA, "hello").decode()), "gateway_url": ""}
        ).encode()
        with patch.object(self.discB, "_send", new=AsyncMock()):
            await self.discB._handle_packet(packet, "127.0.0.1")
        self.assertEqual(self.regB.get_peers(), [])


class TestManualPathGuardStillWorks(unittest.TestCase):
    """`routes/mesh.py::_is_safe_mesh_url` now reuses `ip_is_lan` — a pure extraction, so
    its behaviour (including hostname resolution, which the manual path keeps) is unchanged."""

    def test_rejects_metadata_and_public(self):
        from navig.gateway.routes.mesh import _is_safe_mesh_url

        for url in ("http://169.254.169.254", "http://93.184.216.34:8789"):
            self.assertFalse(_is_safe_mesh_url(url), url)

    def test_accepts_lan_literal(self):
        from navig.gateway.routes.mesh import _is_safe_mesh_url

        self.assertTrue(_is_safe_mesh_url("http://192.168.1.50:8789"))

    def test_still_resolves_hostnames_on_the_manual_path(self):
        """Unlike the multicast path, a human-entered hostname is resolved and allowed when
        every resolved address is LAN."""
        from navig.gateway.routes.mesh import _is_safe_mesh_url

        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.50", 8789))],
        ):
            self.assertTrue(_is_safe_mesh_url("http://my-nas.local:8789"))
        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 8789))],
        ):
            self.assertFalse(_is_safe_mesh_url("http://evil.example.com:8789"))
