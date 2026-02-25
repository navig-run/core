"""
Tests for navig.mesh.registry — unit coverage for NodeRecord health, eviction,
best-peer selection, and persistence round-trip.
"""
import json
import time
from pathlib import Path
import pytest

from navig.mesh.registry import (
    NodeRecord,
    NodeRegistry,
    DEGRADED_AFTER_S,
    OFFLINE_AFTER_S,
    EVICT_AFTER_S,
)


def _make_record(**kwargs) -> NodeRecord:
    defaults = dict(
        node_id="navig-linux-host01-ab12",
        hostname="host01",
        os="linux",
        gateway_url="http://10.0.0.10:8789",
        capabilities=["llm", "shell"],
        formation="dev",
        load=0.2,
        version="2.3.0",
        last_seen=time.time(),
        is_self=False,
    )
    defaults.update(kwargs)
    return NodeRecord(**defaults)


class TestNodeRecordHealth:
    def test_online(self):
        r = _make_record(last_seen=time.time() - 10)
        assert r.health == "online"

    def test_degraded(self):
        r = _make_record(last_seen=time.time() - DEGRADED_AFTER_S - 5)
        assert r.health == "degraded"

    def test_offline(self):
        r = _make_record(last_seen=time.time() - OFFLINE_AFTER_S - 5)
        assert r.health == "offline"


class TestNodeRecordSerialization:
    def test_round_trip(self):
        r = _make_record()
        d = r.to_dict()
        r2 = NodeRecord.from_dict(dict(d))   # from_dict strips 'health' and 'is_self'
        assert r2.node_id == r.node_id
        assert r2.gateway_url == r.gateway_url
        assert r2.is_self is False           # from_dict never trusts external is_self

    def test_health_not_stored(self):
        """health is a computed property — should not be in from_dict fields."""
        r = _make_record()
        d = r.to_dict()
        assert "health" in d                 # present in API output
        r2 = NodeRecord.from_dict(d)         # from_dict drops it
        assert r2.health in {"online", "degraded", "offline"}


class TestNodeRegistry:
    def test_get_best_peer_by_load(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        reg.upsert_peer(_make_record(node_id="a", load=0.8, capabilities=["llm"]))
        reg.upsert_peer(_make_record(node_id="b", load=0.2, capabilities=["llm"]))
        best = reg.get_best_peer("llm")
        assert best is not None
        assert best.node_id == "b"

    def test_no_peer_for_missing_capability(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        reg.upsert_peer(_make_record(node_id="a", capabilities=["shell"]))
        assert reg.get_best_peer("gpu") is None

    def test_offline_peer_not_returned_as_best(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        reg.upsert_peer(_make_record(
            node_id="old",
            load=0.0,
            last_seen=time.time() - OFFLINE_AFTER_S - 10,
        ))
        assert reg.get_best_peer() is None

    def test_eviction_after_long_absence(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        reg.upsert_peer(_make_record(
            node_id="ghost",
            last_seen=time.time() - EVICT_AFTER_S - 10,
        ))
        peers = reg.get_peers()
        assert not any(r.node_id == "ghost" for r in peers)

    def test_persistence_round_trip(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        reg.upsert_peer(_make_record(node_id="peer-x"))
        # Load a fresh registry from the same directory
        reg2 = NodeRegistry(tmp_path)
        peers = reg2.get_peers()
        assert any(r.node_id == "peer-x" for r in peers)

    def test_self_record_not_in_peers(self, tmp_path):
        reg = NodeRegistry(tmp_path)
        peers = reg.get_peers()
        assert all(not r.is_self for r in peers)
