"""Bounded execution fixtures for previously PARTIAL MCP handlers.

The audit classified 60 tools PARTIAL: structurally verified, but no test executed the
handler. This batch starts with the runtime read surface and the CDP discovery verbs —
externally safe, dependency-injectable, and until now reached only by inspection.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.mcp.tools.cdp import _tool_cdp_launched, _tool_cdp_targets
from navig.mcp.tools.runtime import (
    _tool_runtime_list_missions,
    _tool_runtime_list_nodes,
    _tool_runtime_list_receipts,
    _tool_runtime_trust_score,
)


class _Row:
    def __init__(self, payload: dict):
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


def test_runtime_list_nodes_executes(monkeypatch) -> None:
    store = SimpleNamespace(list_nodes=lambda status=None: [_Row({"id": "n1"})])
    monkeypatch.setattr("navig.contracts.store.get_runtime_store", lambda: store)
    result = _tool_runtime_list_nodes(None, {})
    assert result == {"nodes": [{"id": "n1"}], "count": 1}


def test_runtime_list_missions_executes(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def list_missions(**kwargs):
        seen.update(kwargs)
        return [_Row({"id": "m1"})]

    store = SimpleNamespace(list_missions=list_missions)
    monkeypatch.setattr("navig.contracts.store.get_runtime_store", lambda: store)
    result = _tool_runtime_list_missions(None, {"limit": 5, "node_id": "n1"})
    assert result == {"missions": [{"id": "m1"}], "count": 1}
    assert seen["limit"] == 5 and seen["node_id"] == "n1"


def test_runtime_list_receipts_executes(monkeypatch) -> None:
    store = SimpleNamespace(list_receipts=lambda **kwargs: [_Row({"id": "r1"})])
    monkeypatch.setattr("navig.contracts.store.get_runtime_store", lambda: store)
    result = _tool_runtime_list_receipts(None, {"mission_id": "m1"})
    assert result == {"receipts": [{"id": "r1"}], "count": 1}


def test_runtime_trust_score_executes(monkeypatch) -> None:
    store = SimpleNamespace(
        compute_trust_score=lambda node_id: _Row({"node_id": node_id, "score": 0.9})
    )
    monkeypatch.setattr("navig.contracts.store.get_runtime_store", lambda: store)
    assert _tool_runtime_trust_score(None, {"node_id": "n1"}) == {"node_id": "n1", "score": 0.9}


def test_cdp_targets_executes(monkeypatch) -> None:
    from navig.browser import cdp_actions

    monkeypatch.setattr(cdp_actions, "targets", lambda ports=None: [{"port": 9222}])
    assert _tool_cdp_targets(None, {}) == [{"port": 9222}]


def test_cdp_launched_executes(monkeypatch) -> None:
    from navig.browser import cdp_actions

    monkeypatch.setattr(cdp_actions, "launched", lambda: [{"port": 9222, "live": True}])
    assert _tool_cdp_launched(None, {}) == [{"port": 9222, "live": True}]
