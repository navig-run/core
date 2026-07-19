"""Regression: the TUI mesh badge reads the real NodeRegistry.

`resolve_mesh` was doubly dead: it called a non-existent `registry.list_nodes()`
— guarded by `hasattr`, so it silently returned `[]` and node_count was ALWAYS
0, i.e. the badge always read "single-node mode" even on a live multi-node mesh —
and it imported a phantom `navig.mesh.election.get_current_leader`, so the leader
was always "—". It now reads the real API: `registry.get_peers()` / `get_all()` /
`get_leader()` (the same leader source ElectionManager uses).
"""

from __future__ import annotations

from dataclasses import dataclass

import navig.mesh.registry as reg_mod
from navig.tui.resolvers import resolve_mesh


@dataclass
class _Node:
    node_id: str
    hostname: str = ""
    role: str = "standby"
    health: str = "online"
    is_self: bool = False


class _Reg:
    """Minimal stand-in for NodeRegistry — only the methods resolve_mesh uses."""

    def __init__(self, peers, *, self_node=None, leader=None):
        self._peers = list(peers)
        self._self = self_node or _Node("navig-self", "selfhost", is_self=True)
        self._leader = leader

    def get_peers(self):
        return list(self._peers)

    def get_all(self):
        return [self._self, *self._peers]

    def get_leader(self):
        return self._leader


def test_mesh_single_node_when_no_peers(monkeypatch):
    monkeypatch.setattr(reg_mod, "get_registry", lambda: _Reg(peers=[]))

    badge = resolve_mesh()

    assert badge.status == "missing"
    assert "single-node" in badge.detail


def test_mesh_counts_all_nodes_and_names_leader(monkeypatch):
    leader = _Node("navig-srv01", "srv01", role="leader")
    monkeypatch.setattr(reg_mod, "get_registry", lambda: _Reg(peers=[leader], leader=leader))

    badge = resolve_mesh()

    assert badge.status == "ok"
    assert "2 nodes" in badge.detail  # self + 1 peer — the old code showed 0/single-node
    assert "leader: srv01" in badge.detail


def test_mesh_no_leader_shows_dash(monkeypatch):
    peer = _Node("navig-srv02", "srv02")
    monkeypatch.setattr(reg_mod, "get_registry", lambda: _Reg(peers=[peer], leader=None))

    badge = resolve_mesh()

    assert badge.status == "ok"
    assert "2 nodes" in badge.detail
    assert "leader: —" in badge.detail


def test_mesh_leader_falls_back_to_node_id_when_hostname_blank(monkeypatch):
    leader = _Node("navig-srv03", hostname="", role="leader")
    monkeypatch.setattr(reg_mod, "get_registry", lambda: _Reg(peers=[leader], leader=leader))

    badge = resolve_mesh()

    assert "leader: navig-srv03" in badge.detail
