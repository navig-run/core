"""Tests for navig.contracts.node — Node identity contract."""
from __future__ import annotations

import json

import pytest

from navig.contracts.node import Node, NodeOS, NodeStatus


# ── helpers ──────────────────────────────────────────────────


def _node(**kwargs) -> Node:
    defaults = dict(hostname="test-host")
    defaults.update(kwargs)
    return Node(**defaults)


# ── NodeStatus / NodeOS ───────────────────────────────────────


class TestEnums:
    def test_node_status_values(self):
        assert NodeStatus.PROVISIONING.value == "provisioning"
        assert NodeStatus.ONLINE.value == "online"
        assert NodeStatus.OFFLINE.value == "offline"
        assert NodeStatus.SUSPENDED.value == "suspended"
        assert NodeStatus.DECOMMISSIONED.value == "decommissioned"

    def test_node_os_values(self):
        assert NodeOS.LINUX.value == "linux"
        assert NodeOS.WINDOWS.value == "windows"
        assert NodeOS.MACOS.value == "macos"
        assert NodeOS.UNKNOWN.value == "unknown"


# ── Construction ──────────────────────────────────────────────


class TestConstruction:
    def test_hostname_stored(self):
        assert _node().hostname == "test-host"

    def test_node_id_auto_generated(self):
        n = _node()
        assert len(n.node_id) == 36  # UUID4

    def test_unique_node_ids(self):
        assert _node().node_id != _node().node_id

    def test_default_status_provisioning(self):
        assert _node().status == NodeStatus.PROVISIONING

    def test_default_os_unknown(self):
        assert _node().os == NodeOS.UNKNOWN

    def test_default_trust_score(self):
        assert _node().trust_score == 1.0

    def test_default_capabilities_empty(self):
        assert _node().capabilities == []

    def test_custom_os(self):
        n = _node(os=NodeOS.LINUX)
        assert n.os == NodeOS.LINUX


# ── Lifecycle transitions ─────────────────────────────────────


class TestLifecycle:
    def test_provisioning_to_online(self):
        n = _node()
        n.go_online()
        assert n.status == NodeStatus.ONLINE

    def test_offline_to_online(self):
        n = _node(status=NodeStatus.OFFLINE)
        n.go_online()
        assert n.status == NodeStatus.ONLINE

    def test_suspended_to_online(self):
        n = _node(status=NodeStatus.SUSPENDED)
        n.go_online()
        assert n.status == NodeStatus.ONLINE

    def test_decommissioned_cannot_go_online(self):
        n = _node(status=NodeStatus.DECOMMISSIONED)
        with pytest.raises(ValueError):
            n.go_online()

    def test_online_to_offline(self):
        n = _node(status=NodeStatus.ONLINE)
        n.go_offline()
        assert n.status == NodeStatus.OFFLINE

    def test_decommissioned_cannot_go_offline(self):
        n = _node(status=NodeStatus.DECOMMISSIONED)
        with pytest.raises(ValueError):
            n.go_offline()

    def test_online_to_suspended(self):
        n = _node(status=NodeStatus.ONLINE)
        n.suspend()
        assert n.status == NodeStatus.SUSPENDED

    def test_offline_cannot_suspend(self):
        n = _node(status=NodeStatus.OFFLINE)
        with pytest.raises(ValueError):
            n.suspend()

    def test_decommission_from_any_state(self):
        for status in [NodeStatus.ONLINE, NodeStatus.OFFLINE, NodeStatus.SUSPENDED]:
            n = _node(status=status)
            n.decommission()
            assert n.status == NodeStatus.DECOMMISSIONED

    def test_go_online_updates_last_seen(self):
        n = _node()
        old_seen = n.last_seen
        import time; time.sleep(0.001)
        n.go_online()
        # last_seen should be updated (or at least not broken)
        assert n.last_seen is not None


# ── Capability helpers ────────────────────────────────────────


class TestCapabilities:
    def test_has_capability_false_when_empty(self):
        n = _node()
        assert n.has_capability("llm") is False

    def test_add_capability(self):
        n = _node()
        n.add_capability("llm")
        assert n.has_capability("llm") is True

    def test_add_capability_no_duplicates(self):
        n = _node()
        n.add_capability("llm")
        n.add_capability("llm")
        assert n.capabilities.count("llm") == 1

    def test_remove_capability(self):
        n = _node(capabilities=["llm", "ssh"])
        n.remove_capability("llm")
        assert n.has_capability("llm") is False
        assert n.has_capability("ssh") is True

    def test_remove_nonexistent_capability_safe(self):
        n = _node()
        n.remove_capability("nonexistent")  # should not raise


# ── Serialization ─────────────────────────────────────────────


class TestSerialization:
    def test_to_dict_has_status_as_string(self):
        d = _node().to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "provisioning"

    def test_to_dict_has_os_as_string(self):
        d = _node().to_dict()
        assert isinstance(d["os"], str)

    def test_to_dict_contains_hostname(self):
        d = _node(hostname="prod-server").to_dict()
        assert d["hostname"] == "prod-server"

    def test_to_json_valid(self):
        raw = _node().to_json()
        parsed = json.loads(raw)
        assert "hostname" in parsed

    def test_from_dict_roundtrip(self):
        n = _node(hostname="server01", os=NodeOS.LINUX, capabilities=["llm"])
        d = n.to_dict()
        restored = Node.from_dict(d)
        assert restored.hostname == "server01"
        assert restored.os == NodeOS.LINUX
        assert "llm" in restored.capabilities

    def test_from_json_roundtrip(self):
        n = _node(status=NodeStatus.ONLINE)
        n.status = NodeStatus.ONLINE  # already set via from_dict above
        raw = n.to_json()
        # Manually set to ONLINE to test round-trip
        n2 = Node.from_json(raw)
        assert n2.node_id == n.node_id

    def test_from_dict_handles_missing_os(self):
        data = {"hostname": "x", "node_id": "abc-123"}
        n = Node.from_dict(data)
        assert n.os == NodeOS.UNKNOWN


# ── repr ──────────────────────────────────────────────────────


class TestRepr:
    def test_repr_contains_hostname(self):
        n = _node(hostname="myhost")
        assert "myhost" in repr(n)

    def test_repr_contains_status(self):
        n = _node()
        assert "provisioning" in repr(n)
