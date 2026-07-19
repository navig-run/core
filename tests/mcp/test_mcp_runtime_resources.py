"""MCP runtime-contract resources — ``navig://runtime/{nodes,missions,receipts}``.

The resources shipped in ``navig.mcp_server`` (registration in
``_setup_navig_resources`` + read path in ``_read_resource``), but nothing
exercised them: the existing resource tests (tests/mcp/test_mcp_server.py)
stop at the agent resources. These tests close that gap, mirroring the same
fixture style — ``register_all_tools`` is patched so the handler stays light,
and the RuntimeStore singleton is pointed at a per-test temp dir (the resource
reader resolves it via ``get_runtime_store()`` at call time).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from navig.contracts import (
    ExecutionReceipt,
    Mission,
    MissionStatus,
    Node,
    NodeStatus,
    ReceiptOutcome,
)
from navig.contracts.store import RuntimeStore, reset_runtime_store
from navig.mcp_server import MCPProtocolHandler

pytestmark = pytest.mark.unit

RUNTIME_URIS = (
    "navig://runtime/nodes",
    "navig://runtime/missions",
    "navig://runtime/receipts",
)


@pytest.fixture()
def store(tmp_path):
    """Isolate the RuntimeStore singleton in a per-test temp dir."""
    s = reset_runtime_store(tmp_path / "runtime")
    yield s
    # Leave the singleton pointing at a fresh empty dir, never at test data.
    reset_runtime_store(tmp_path / "runtime-teardown")


@pytest.fixture()
def handler():
    with patch("navig.mcp.tools.register_all_tools", side_effect=lambda h: None):
        return MCPProtocolHandler()


def _seed(store: RuntimeStore) -> tuple[Node, Mission, ExecutionReceipt]:
    node = Node(hostname="mcp-host", status=NodeStatus.ONLINE, capabilities=["llm"])
    store.register_node(node)
    mission = Mission(title="mcp mission", node_id=node.node_id, capability="llm")
    store.create_mission(mission)
    store.advance_mission(mission.mission_id, "start")
    receipt = store.complete_mission(mission.mission_id, succeeded=True, result={"x": 1})
    return node, mission, receipt


def _read_json(handler: MCPProtocolHandler, uri: str):
    res = handler._handle_resources_read({"uri": uri})
    assert "contents" in res, f"read {uri!r} errored: {res}"
    content = res["contents"][0]
    assert content["uri"] == uri
    assert content["mimeType"] == "application/json"
    return json.loads(content["text"])


# ── Registration ─────────────────────────────────────────────────────


def test_runtime_resources_registered(handler):
    for uri in RUNTIME_URIS:
        assert uri in handler.resources
        assert handler.resources[uri]["mimeType"] == "application/json"


def test_resources_list_includes_runtime(handler):
    listed = {r["uri"] for r in handler._handle_resources_list({})["resources"]}
    for uri in RUNTIME_URIS:
        assert uri in listed


# ── Read paths ───────────────────────────────────────────────────────


def test_read_runtime_resources_empty_store(store, handler):
    for uri in RUNTIME_URIS:
        assert _read_json(handler, uri) == []


def test_read_runtime_nodes(store, handler):
    node, _, _ = _seed(store)
    payload = _read_json(handler, "navig://runtime/nodes")
    assert len(payload) == 1
    assert payload[0]["node_id"] == node.node_id
    assert payload[0]["hostname"] == "mcp-host"
    assert payload[0]["status"] == "online"


def test_read_runtime_missions(store, handler):
    _, mission, _ = _seed(store)
    payload = _read_json(handler, "navig://runtime/missions")
    assert len(payload) == 1
    assert payload[0]["mission_id"] == mission.mission_id
    assert payload[0]["status"] == "succeeded"


def test_read_runtime_receipts_round_trips_into_contract(store, handler):
    """The MCP payload must re-parse into ExecutionReceipt — the wire format
    IS the contract's serialisation, not a lookalike."""
    _, mission, receipt = _seed(store)
    payload = _read_json(handler, "navig://runtime/receipts")
    assert len(payload) == 1
    restored = ExecutionReceipt.from_dict(payload[0])
    assert restored.receipt_id == receipt.receipt_id
    assert restored.mission_id == mission.mission_id
    assert restored.outcome == ReceiptOutcome.SUCCEEDED
    assert restored.is_success


def test_read_runtime_nodes_and_missions_round_trip(store, handler):
    node, mission, _ = _seed(store)
    restored_node = Node.from_dict(_read_json(handler, "navig://runtime/nodes")[0])
    assert restored_node.node_id == node.node_id
    assert restored_node.status == NodeStatus.ONLINE
    restored_mission = Mission.from_dict(_read_json(handler, "navig://runtime/missions")[0])
    assert restored_mission.mission_id == mission.mission_id
    assert restored_mission.status == MissionStatus.SUCCEEDED


# ── Full JSON-RPC dispatch ───────────────────────────────────────────


def test_resources_read_via_handle_message(store, handler):
    _seed(store)
    resp = handler.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": "navig://runtime/receipts"},
        }
    )
    assert resp["id"] == 7
    assert "result" in resp
    payload = json.loads(resp["result"]["contents"][0]["text"])
    assert payload[0]["outcome"] == "succeeded"
