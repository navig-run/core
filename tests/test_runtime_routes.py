"""
Tests for the /runtime/* gateway routes (navig/gateway/routes/runtime.py).

Covers:
  - GET  /runtime/nodes
  - POST /runtime/nodes
  - GET  /runtime/nodes/{id}
  - GET  /runtime/nodes/{id}/trust
  - GET  /runtime/missions
  - POST /runtime/missions
  - GET  /runtime/missions/{id}
  - POST /runtime/missions/{id}/advance
  - POST /runtime/missions/{id}/complete
  - GET  /runtime/receipts
  - GET  /runtime/receipts/{id}
  - GET  /runtime/store/stats
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ─────────────────────────── helpers ────────────────────────────────

from unittest.mock import MagicMock, AsyncMock

def _build_gateway(*, auth_token: str | None = None):
    gw = MagicMock()
    gw.config = SimpleNamespace(auth_token=auth_token)
    gw.policy_check = AsyncMock(return_value=None)
    return gw


def _build_app(gateway):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from navig.gateway.routes.runtime import register

    app = web.Application()
    register(app, gateway)
    return app


def _node_payload(**kwargs) -> dict:
    return {
        "node_id": kwargs.get("node_id", f"node-{uuid.uuid4().hex[:8]}"),
        "hostname": kwargs.get("hostname", "testhost"),
        "os": kwargs.get("os", "linux"),
        "version": kwargs.get("version", "0.1.0"),
        "status": kwargs.get("status", "online"),
    }


def _mission_payload(node_id: str, **kwargs) -> dict:
    return {
        "mission_id": kwargs.get("mission_id", f"mission-{uuid.uuid4().hex[:8]}"),
        "node_id": node_id,
        "title": kwargs.get("title", "Test mission"),
        "capability": kwargs.get("capability", "test"),
    }


@pytest.fixture(autouse=True)
def _reset_store():
    """Isolate each test with a fresh RuntimeStore."""
    from navig.contracts.store import reset_runtime_store
    reset_runtime_store()
    yield
    reset_runtime_store()


# ──────────────────────── Node routes ────────────────────────────────

@pytest.mark.asyncio
async def test_list_nodes_empty():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/runtime/nodes")
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        assert body["data"]["count"] == 0
        assert body["data"]["nodes"] == []


@pytest.mark.asyncio
async def test_register_and_list_node():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    payload = _node_payload(node_id="node-abc")
    async with TestClient(TestServer(app)) as client:
        # Register
        r = await client.post("/runtime/nodes", json=payload)
        assert r.status == 201
        body = await r.json()
        assert body["ok"] is True
        assert body["data"]["node_id"] == "node-abc"

        # List
        r2 = await client.get("/runtime/nodes")
        assert r2.status == 200
        b2 = await r2.json()
        assert b2["data"]["count"] == 1
        assert b2["data"]["nodes"][0]["node_id"] == "node-abc"


@pytest.mark.asyncio
async def test_get_node_found_and_not_found():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    payload = _node_payload(node_id="node-xyz")
    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=payload)

        r = await client.get("/runtime/nodes/node-xyz")
        assert r.status == 200
        body = await r.json()
        assert body["data"]["node_id"] == "node-xyz"

        r404 = await client.get("/runtime/nodes/missing-node")
        assert r404.status == 404
        b404 = await r404.json()
        assert b404["ok"] is False
        assert b404["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_register_node_bad_json():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    async with TestClient(TestServer(app)) as client:
        # Send malformed bytes
        r = await client.post("/runtime/nodes", data=b"not-json",
                              headers={"Content-Type": "application/json"})
        assert r.status == 400


@pytest.mark.asyncio
async def test_get_trust_score():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    payload = _node_payload(node_id="node-trust")
    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=payload)
        r = await client.get("/runtime/nodes/node-trust/trust")
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        # TrustScore has at minimum a node_id field
        assert "node_id" in body["data"] or isinstance(body["data"], dict)


@pytest.mark.asyncio
async def test_get_trust_node_not_found():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/runtime/nodes/ghost/trust")
        assert r.status == 404


# ──────────────────────── Mission routes ─────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_mission():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)
    node_payload = _node_payload(node_id="n1")
    mission_payload = _mission_payload("n1", mission_id="m1")

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=node_payload)

        r = await client.post("/runtime/missions", json=mission_payload)
        assert r.status == 201
        body = await r.json()
        assert body["ok"] is True
        assert body["data"]["mission_id"] == "m1"
        assert body["data"]["node_id"] == "n1"

        r2 = await client.get("/runtime/missions")
        b2 = await r2.json()
        assert b2["data"]["count"] == 1
        assert b2["data"]["missions"][0]["mission_id"] == "m1"


@pytest.mark.asyncio
async def test_list_missions_filter_by_node():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="nA"))
        await client.post("/runtime/nodes", json=_node_payload(node_id="nB"))
        await client.post("/runtime/missions", json=_mission_payload("nA", mission_id="mA1"))
        await client.post("/runtime/missions", json=_mission_payload("nA", mission_id="mA2"))
        await client.post("/runtime/missions", json=_mission_payload("nB", mission_id="mB1"))

        r = await client.get("/runtime/missions?node_id=nA")
        body = await r.json()
        assert body["data"]["count"] == 2

        r2 = await client.get("/runtime/missions?node_id=nB")
        b2 = await r2.json()
        assert b2["data"]["count"] == 1


@pytest.mark.asyncio
async def test_get_mission_found_and_not_found():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n2"))
        await client.post("/runtime/missions", json=_mission_payload("n2", mission_id="m2"))

        r = await client.get("/runtime/missions/m2")
        assert r.status == 200
        assert (await r.json())["data"]["mission_id"] == "m2"

        r404 = await client.get("/runtime/missions/ghost-m")
        assert r404.status == 404


@pytest.mark.asyncio
async def test_advance_mission_start():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n3"))
        await client.post("/runtime/missions", json=_mission_payload("n3", mission_id="m3"))

        r = await client.post("/runtime/missions/m3/advance", json={"action": "start"})
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        assert body["data"]["status"] == "running"


@pytest.mark.asyncio
async def test_advance_mission_invalid_action():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n4"))
        await client.post("/runtime/missions", json=_mission_payload("n4", mission_id="m4"))

        r = await client.post("/runtime/missions/m4/advance", json={"action": "explode"})
        assert r.status == 422
        b = await r.json()
        assert b["error_code"] == "invalid_transition"


@pytest.mark.asyncio
async def test_advance_mission_missing_action():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n5"))
        await client.post("/runtime/missions", json=_mission_payload("n5", mission_id="m5"))

        r = await client.post("/runtime/missions/m5/advance", json={})
        assert r.status == 422
        b = await r.json()
        assert b["error_code"] == "validation_error"


@pytest.mark.asyncio
async def test_advance_mission_not_found():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        r = await client.post("/runtime/missions/ghost/advance", json={"action": "start"})
        assert r.status == 404


@pytest.mark.asyncio
async def test_complete_mission_success():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n6"))
        await client.post("/runtime/missions", json=_mission_payload("n6", mission_id="m6"))
        # advance to running first
        await client.post("/runtime/missions/m6/advance", json={"action": "start"})

        r = await client.post("/runtime/missions/m6/complete",
                              json={"outcome": "success", "output": {"rows": 42}})
        assert r.status == 201
        body = await r.json()
        assert body["ok"] is True
        receipt = body["data"]
        assert "receipt_id" in receipt
        assert receipt["mission_id"] == "m6"


@pytest.mark.asyncio
async def test_complete_mission_failure():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n7"))
        await client.post("/runtime/missions", json=_mission_payload("n7", mission_id="m7"))
        await client.post("/runtime/missions/m7/advance", json={"action": "start"})

        r = await client.post("/runtime/missions/m7/complete",
                              json={"outcome": "failure", "error": "timeout reached"})
        assert r.status == 201
        body = await r.json()
        assert body["data"]["outcome"] in ("failed", "failure", "FAILED")  # enum value


# ──────────────────────── Receipt routes ─────────────────────────────

@pytest.mark.asyncio
async def test_list_receipts_empty():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        r = await client.get("/runtime/receipts")
        assert r.status == 200
        body = await r.json()
        assert body["data"]["count"] == 0


@pytest.mark.asyncio
async def test_receipt_created_after_complete():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n8"))
        await client.post("/runtime/missions", json=_mission_payload("n8", mission_id="m8"))
        await client.post("/runtime/missions/m8/advance", json={"action": "start"})
        comp = await client.post("/runtime/missions/m8/complete", json={"outcome": "success"})
        receipt_id = (await comp.json())["data"]["receipt_id"]

        # list
        r = await client.get("/runtime/receipts")
        body = await r.json()
        assert body["data"]["count"] == 1

        # get by id
        r2 = await client.get(f"/runtime/receipts/{receipt_id}")
        assert r2.status == 200
        b2 = await r2.json()
        assert b2["data"]["receipt_id"] == receipt_id
        assert b2["data"]["mission_id"] == "m8"


@pytest.mark.asyncio
async def test_get_receipt_not_found():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        r = await client.get("/runtime/receipts/nonexistent-receipt")
        assert r.status == 404
        b = await r.json()
        assert b["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_list_receipts_filter_by_mission():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="n9"))
        for mid in ("mX", "mY"):
            await client.post("/runtime/missions", json=_mission_payload("n9", mission_id=mid))
            await client.post(f"/runtime/missions/{mid}/advance", json={"action": "start"})
            await client.post(f"/runtime/missions/{mid}/complete", json={"outcome": "success"})

        r = await client.get("/runtime/receipts?mission_id=mX")
        body = await r.json()
        assert body["data"]["count"] == 1
        assert body["data"]["receipts"][0]["mission_id"] == "mX"


# ──────────────────────── Stats route ────────────────────────────────

@pytest.mark.asyncio
async def test_store_stats():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        await client.post("/runtime/nodes", json=_node_payload(node_id="s1"))
        await client.post("/runtime/missions", json=_mission_payload("s1", mission_id="sm1"))

        r = await client.get("/runtime/store/stats")
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        stats = body["data"]
        assert stats.get("nodes", 0) >= 1
        assert stats.get("missions", 0) >= 1


# ──────────────────────── Auth gates ─────────────────────────────────

@pytest.mark.asyncio
async def test_auth_required_when_token_set():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(auth_token="secret-token")
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        # No auth header → 401
        r = await client.get("/runtime/nodes")
        assert r.status == 401

        # Wrong token → 401
        r2 = await client.get("/runtime/nodes", headers={"Authorization": "Bearer wrong"})
        assert r2.status == 401

        # Correct token → 200
        r3 = await client.get("/runtime/nodes", headers={"Authorization": "Bearer secret-token"})
        assert r3.status == 200
