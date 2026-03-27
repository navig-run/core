"""
Tests for Flux mesh BLAKE2b HMAC authentication (Phase 2).

Covers:
  Unit:
    auth.sign_payload          — deterministic signature
    auth.verify_payload        — correct / wrong secret / missing HMAC
    auth.attach_hmac           — idempotent, strips old HMAC before signing
    auth.load_secret           — config arg takes priority over env

  Integration (through mesh.discovery internals):
    _parse_packet(secret=None)   — accepts signed + unsigned packets
    _parse_packet(secret=B)      — accepts correctly signed; rejects unsigned
    _parse_packet(secret=B)      — rejects packet signed with wrong secret
    MeshDiscovery._send          — outgoing payload carries valid HMAC
    MeshDiscovery two-node       — authenticated HELLO registers peer
    MeshDiscovery two-node       — HELLO with wrong secret is dropped
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from navig.mesh.auth import (
    HMAC_FIELD,
    attach_hmac,
    load_secret,
    sign_payload,
    verify_payload,
)
from navig.mesh.discovery import MeshDiscovery, _build_packet, _parse_packet
from navig.mesh.registry import NodeRegistry

# ── Imports under test ───────────────────────────────────────────────────────


# ── Shared fixtures ──────────────────────────────────────────────────────────

_SECRET_A = b"my-per-mesh-shared-secret"
_SECRET_B = b"totally-different-secret"

_SAMPLE_PAYLOAD = {
    "v": 1,
    "type": "hello",
    "seq": 1,
    "node_id": "navig-test-abc123",
    "hostname": "testhost",
    "os": "Linux",
    "gateway_url": "http://127.0.0.1:8789",
    "capabilities": ["llm", "shell"],
    "formation": "",
    "load": 0.1,
    "version": "test",
    "ts": 1700000000,
}


def _make_registry(tmp_path: Path, suffix: str) -> NodeRegistry:
    with (
        patch(
            "navig.mesh.registry._derive_node_id", return_value=f"navig-test-{suffix}"
        ),
        patch("navig.mesh.registry.NodeRegistry._local_ip", return_value="127.0.0.1"),
        patch("navig.mesh.registry._measure_load", return_value=0.1),
        patch(
            "navig.mesh.registry.NodeRegistry._detect_capabilities",
            return_value=["llm"],
        ),
    ):
        return NodeRegistry(storage_dir=tmp_path)


def _make_discovery(registry: NodeRegistry, secret=None) -> MeshDiscovery:
    d = MeshDiscovery(registry, secret=secret)
    d._sender = MagicMock()
    d._receiver = MagicMock()
    d._sender.sendto = MagicMock()
    return d


# ── Unit: auth.sign_payload ──────────────────────────────────────────────────


class TestSignPayload(unittest.TestCase):
    def test_returns_hex_string(self):
        tag = sign_payload(_SAMPLE_PAYLOAD, _SECRET_A)
        self.assertIsInstance(tag, str)
        self.assertEqual(len(tag), 64)  # 32-byte BLAKE2b-256 → 64 hex chars
        int(tag, 16)  # must be valid hex

    def test_deterministic(self):
        t1 = sign_payload(_SAMPLE_PAYLOAD, _SECRET_A)
        t2 = sign_payload(_SAMPLE_PAYLOAD, _SECRET_A)
        self.assertEqual(t1, t2)

    def test_different_secrets_differ(self):
        t1 = sign_payload(_SAMPLE_PAYLOAD, _SECRET_A)
        t2 = sign_payload(_SAMPLE_PAYLOAD, _SECRET_B)
        self.assertNotEqual(t1, t2)

    def test_excludes_hmac_field(self):
        """Signing a payload that already has an hmac field ignores it."""
        p_with = dict(_SAMPLE_PAYLOAD, **{HMAC_FIELD: "deadbeef"})
        p_without = {k: v for k, v in _SAMPLE_PAYLOAD.items() if k != HMAC_FIELD}
        self.assertEqual(
            sign_payload(p_with, _SECRET_A),
            sign_payload(p_without, _SECRET_A),
        )


# ── Unit: auth.verify_payload ────────────────────────────────────────────────


class TestVerifyPayload(unittest.TestCase):
    def setUp(self):
        self.signed = attach_hmac(_SAMPLE_PAYLOAD, _SECRET_A)

    def test_correct_secret_passes(self):
        self.assertTrue(verify_payload(self.signed, _SECRET_A))

    def test_wrong_secret_fails(self):
        self.assertFalse(verify_payload(self.signed, _SECRET_B))

    def test_missing_hmac_fails(self):
        clean = {k: v for k, v in self.signed.items() if k != HMAC_FIELD}
        self.assertFalse(verify_payload(clean, _SECRET_A))

    def test_tampered_field_fails(self):
        tampered = dict(self.signed, load=99.9)
        self.assertFalse(verify_payload(tampered, _SECRET_A))

    def test_tampered_hmac_fails(self):
        tampered = dict(self.signed, **{HMAC_FIELD: "a" * 64})
        self.assertFalse(verify_payload(tampered, _SECRET_A))


# ── Unit: auth.attach_hmac ───────────────────────────────────────────────────


class TestAttachHmac(unittest.TestCase):
    def test_adds_hmac_field(self):
        result = attach_hmac(_SAMPLE_PAYLOAD, _SECRET_A)
        self.assertIn(HMAC_FIELD, result)

    def test_does_not_mutate_input(self):
        copy = dict(_SAMPLE_PAYLOAD)
        attach_hmac(copy, _SECRET_A)
        self.assertNotIn(HMAC_FIELD, copy)

    def test_idempotent(self):
        once = attach_hmac(_SAMPLE_PAYLOAD, _SECRET_A)
        twice = attach_hmac(once, _SECRET_A)
        self.assertEqual(once[HMAC_FIELD], twice[HMAC_FIELD])

    def test_result_verifies(self):
        result = attach_hmac(_SAMPLE_PAYLOAD, _SECRET_A)
        self.assertTrue(verify_payload(result, _SECRET_A))


# ── Unit: auth.load_secret ───────────────────────────────────────────────────


class TestLoadSecret(unittest.TestCase):
    def test_returns_none_when_unconfigured(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NAVIG_MESH_SECRET", None)
            self.assertIsNone(load_secret())

    def test_loads_from_env(self):
        with patch.dict(os.environ, {"NAVIG_MESH_SECRET": "env-secret"}):
            result = load_secret()
            self.assertEqual(result, b"env-secret")

    def test_config_arg_takes_priority_over_env(self):
        with patch.dict(os.environ, {"NAVIG_MESH_SECRET": "env-secret"}):
            result = load_secret("config-secret")
            self.assertEqual(result, b"config-secret")

    def test_truncates_to_64_bytes(self):
        long_secret = "x" * 100
        result = load_secret(long_secret)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 64)


# ── Integration: _parse_packet ───────────────────────────────────────────────


class TestParsePacketAuth(unittest.TestCase):
    def _make_packet(self, secret=None) -> bytes:
        payload = dict(_SAMPLE_PAYLOAD)
        if secret:
            payload = attach_hmac(payload, secret)
        return json.dumps(payload, separators=(",", ":")).encode()

    def test_no_secret_accepts_unsigned(self):
        data = self._make_packet(secret=None)
        self.assertIsNotNone(_parse_packet(data, secret=None))

    def test_no_secret_accepts_signed(self):
        data = self._make_packet(secret=_SECRET_A)
        self.assertIsNotNone(_parse_packet(data, secret=None))

    def test_with_secret_accepts_valid_hmac(self):
        data = self._make_packet(secret=_SECRET_A)
        self.assertIsNotNone(_parse_packet(data, secret=_SECRET_A))

    def test_with_secret_rejects_unsigned(self):
        data = self._make_packet(secret=None)
        self.assertIsNone(_parse_packet(data, secret=_SECRET_A))

    def test_with_secret_rejects_wrong_hmac(self):
        data = self._make_packet(secret=_SECRET_B)
        self.assertIsNone(_parse_packet(data, secret=_SECRET_A))


# ── Integration: MeshDiscovery two-node scenarios ────────────────────────────


class TestMeshDiscoveryAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpA = tempfile.TemporaryDirectory()
        self._tmpB = tempfile.TemporaryDirectory()
        self.regA = _make_registry(Path(self._tmpA.name), "nodeA-0001")
        self.regB = _make_registry(Path(self._tmpB.name), "nodeB-0002")

    async def asyncTearDown(self):
        self._tmpA.cleanup()
        self._tmpB.cleanup()

    async def _deliver(
        self, src: MeshDiscovery, dst: MeshDiscovery, ptype="hello"
    ) -> None:
        packet = _build_packet(src._registry, ptype, seq=1, secret=src._secret)
        with patch.object(dst, "_send", new=AsyncMock()):
            await dst._handle_packet(packet, "127.0.0.1")

    async def test_authenticated_hello_registers_peer(self):
        """Two nodes sharing the same secret — HELLO gets through."""
        discA = _make_discovery(self.regA, secret=_SECRET_A)
        discB = _make_discovery(self.regB, secret=_SECRET_A)
        discA._running = discB._running = True

        self.assertEqual(len(self.regB.get_peers()), 0)
        await self._deliver(discA, discB, ptype="hello")
        self.assertEqual(len(self.regB.get_peers()), 1)

    async def test_wrong_secret_hello_dropped(self):
        """Node A uses secret A, node B expects secret B — HELLO is dropped."""
        discA = _make_discovery(self.regA, secret=_SECRET_A)
        discB = _make_discovery(self.regB, secret=_SECRET_B)
        discA._running = discB._running = True

        await self._deliver(discA, discB, ptype="hello")
        self.assertEqual(len(self.regB.get_peers()), 0)

    async def test_unsigned_hello_dropped_when_secret_required(self):
        """Node A sends unsigned; node B requires HMAC — packet dropped."""
        discA = _make_discovery(self.regA, secret=None)
        discB = _make_discovery(self.regB, secret=_SECRET_B)
        discA._running = discB._running = True

        await self._deliver(discA, discB, ptype="hello")
        self.assertEqual(len(self.regB.get_peers()), 0)

    async def test_unauthenticated_nodes_still_interoperate(self):
        """Phase 1 mode (no secret on either side) — original behaviour preserved."""
        discA = _make_discovery(self.regA, secret=None)
        discB = _make_discovery(self.regB, secret=None)
        discA._running = discB._running = True

        await self._deliver(discA, discB, ptype="hello")
        self.assertEqual(len(self.regB.get_peers()), 1)

    async def test_outgoing_packet_carries_hmac(self):
        """When a secret is configured _build_packet embeds the hmac field."""
        discA = _make_discovery(self.regA, secret=_SECRET_A)
        packet = _build_packet(discA._registry, "hello", seq=1, secret=discA._secret)
        payload = json.loads(packet.decode())
        self.assertIn(HMAC_FIELD, payload)
        self.assertTrue(verify_payload(payload, _SECRET_A))


if __name__ == "__main__":
    unittest.main()
