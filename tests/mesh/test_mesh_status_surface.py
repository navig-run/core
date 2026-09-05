"""The whole mesh status + targeting surface reported nothing on a healthy mesh.

Five bugs, all in code that *reads* mesh state — so a fully-working mesh looked empty:

* ``gateway/routes/daemon.py`` called ``gw._mesh_discovery.list_peers()``. ``list_peers``
  lives on **NodeRegistry**, not ``MeshDiscovery``, so it raised ``AttributeError`` — which
  the surrounding ``except Exception: pass`` swallowed. ``GET /daemon/status`` therefore
  answered ``nodes: []`` on **every** call, regardless of how many peers were online.
* ``commands/flux.py`` read ``data.get("peers")`` off the **raw** ``GET /mesh/peers`` body
  at three call sites. That route answers ``json_ok(registry.to_api_dict())``, i.e.
  ``{"ok": …, "data": {"self": …, "peers": [...]}, "error": …}`` — the peers are one level
  down, so every read missed: ``flux peers`` printed "No peers discovered yet",
  ``flux target`` exited 1 with "No peers", and ``flux status`` counted zeroes.
* ``flux status`` also compared ``health == "healthy"``, but ``NodeRecord.health`` only ever
  holds ``online`` / ``degraded`` / ``offline`` — so the healthy count was 0 even once the
  peers were found.

Same family as the DOA gateway MCP routes: code calling names the real objects don't have,
on a surface nothing exercised. See [[navig-lying-test-fakes]].
"""

from __future__ import annotations

import unittest

from navig.commands.flux import _peer_list

# The exact shape GET /mesh/peers returns: json_ok(registry.to_api_dict()).
ENVELOPE = {
    "ok": True,
    "data": {
        "self": {"node_id": "self-1", "hostname": "me", "health": "online"},
        "peers": [
            {"node_id": "peer-1", "hostname": "boxA", "health": "online", "load_pct": 12.0},
            {"node_id": "peer-2", "hostname": "boxB", "health": "degraded", "load_pct": 80.0},
            {"node_id": "peer-3", "hostname": "boxC", "health": "offline", "load_pct": 0.0},
        ],
    },
    "error": None,
}


class TestPeerListUnwrapsTheEnvelope(unittest.TestCase):
    def test_finds_peers_inside_the_json_ok_envelope(self):
        """THE REGRESSION: pre-fix `data.get("peers")` on the envelope returned [], so every
        flux command that reads peers behaved as if the mesh were empty."""
        peers = _peer_list(ENVELOPE)
        self.assertEqual([p["node_id"] for p in peers], ["peer-1", "peer-2", "peer-3"])

    def test_accepts_a_bare_payload_dict(self):
        """Unwrapped payload (no envelope) must still work."""
        self.assertEqual(len(_peer_list(ENVELOPE["data"])), 3)

    def test_accepts_a_bare_list(self):
        """The pre-existing `isinstance(data, list)` path is preserved."""
        rows = [{"node_id": "x"}]
        self.assertEqual(_peer_list(rows), rows)

    def test_empty_and_malformed_degrade_to_empty_not_crash(self):
        for bad in ({}, {"ok": True, "data": None, "error": None}, {"data": {}}, None, "nope", 7):
            self.assertEqual(_peer_list(bad), [], repr(bad))

    def test_non_list_peers_value_is_not_returned_raw(self):
        self.assertEqual(_peer_list({"ok": True, "data": {"peers": "oops"}}), [])


class TestHealthValuesMatchTheRegistry(unittest.TestCase):
    """`flux status` counted `health == "healthy"`, a value NodeRecord never produces."""

    def test_registry_health_vocabulary(self):
        from navig.mesh.registry import NodeRecord

        rec = NodeRecord(
            node_id="n",
            hostname="h",
            os="linux",
            gateway_url="http://127.0.0.1:8789",
            capabilities=[],
            formation="",
            load=0.0,
            version="1",
            last_seen=0.0,
        )
        # Whatever the record reports, it is never the string the CLI used to look for.
        self.assertIn(rec.health, {"online", "degraded", "offline"})
        self.assertNotEqual(rec.health, "healthy")

    def test_flux_status_counts_online_as_healthy(self):
        """Mirrors the counting logic in `flux status` against the real vocabulary."""
        peers = _peer_list(ENVELOPE)
        healthy = sum(1 for p in peers if p.get("health") == "online")
        degraded = sum(1 for p in peers if p.get("health") == "degraded")
        self.assertEqual((healthy, degraded, len(peers) - healthy - degraded), (1, 1, 1))
        # Pre-fix the CLI looked for "healthy", which no peer ever reports:
        self.assertEqual(sum(1 for p in peers if p.get("health") == "healthy"), 0)


class TestDaemonStatusUsesTheRegistry(unittest.TestCase):
    """`list_peers` is on NodeRegistry; calling it on MeshDiscovery was an AttributeError
    swallowed into an always-empty node list."""

    def test_list_peers_is_not_on_mesh_discovery(self):
        from navig.mesh.discovery import MeshDiscovery
        from navig.mesh.registry import NodeRegistry

        self.assertFalse(
            hasattr(MeshDiscovery, "list_peers"),
            "MeshDiscovery grew list_peers — re-check routes/daemon.py",
        )
        self.assertTrue(hasattr(NodeRegistry, "list_peers"))

    def test_daemon_route_reads_the_registry_not_the_discovery_loop(self):
        """Source-level guard: the handler must go through _mesh_registry."""
        import inspect

        from navig.gateway.routes import daemon as daemon_routes

        src = inspect.getsource(daemon_routes)
        self.assertIn("_mesh_registry", src)
        self.assertNotIn("_mesh_discovery.list_peers()", src)
