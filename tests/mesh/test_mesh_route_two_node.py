"""Real two-node test of the mesh credential seam (Gate 3).

THE DEFECT: ``navig.mesh.router._forward`` POSTs a chat body to a peer's
``/llm/chat`` carrying ``Authorization: Bearer <gateway.mesh_token>``, but the
receiver's ``require_bearer_auth`` only accepted ``gateway.auth.token`` — every
hop 401'd and ``POST /mesh/route`` silently fell back to local processing.

WHAT install.py ACTUALLY DISTRIBUTES: the join one-liners embed the SOURCE
install's ``gateway.mesh_token`` and write it onto the joiner
(``navig config set gateway.mesh_token '<source token>'``), so a fleet
provisioned together shares ONE mesh secret — a peer accepts its own token,
which is therefore also its fleet-mates' token. Two installs never provisioned
together mint different mesh tokens (``_ensure_mesh_token``), no exchange
protocol exists, and that hop must be REJECTED (401 → local fallback, never
open access).

THE TEST: two real aiohttp servers in-process. Node A exposes the real
``POST /mesh/route`` wired to the real ``route_to_best_peer``/``_forward``;
node B serves the real ``/llm/chat`` handler with a stubbed channel router —
auth and transport are real end to end. ``_forward`` is NEVER mocked; only the
config source (which mesh token A sends) and A's peer addressing are wired,
exactly as discovery/config would wire them.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.integration

ADMIN_A = "admin-a"
ADMIN_B = "admin-b"
FLEET_TOKEN = "fleet-shared-mesh-token"
OTHER_FLEET_TOKEN = "another-install-s-own-token"
A_FALLBACK_REPLY = "node A processed locally"
B_REPLY = "hello from node B"


# ────────────────────────────────── helpers ──────────────────────────────────


class _StubGateway:
    """Minimal gateway stand-in with the real config shape.

    ``config.auth_token`` / ``config_manager.global_config`` mirror
    ``NavigGateway`` exactly, so ``require_bearer_auth`` and
    ``gateway_mesh_token`` run against the same fields they use in
    production; only the channel router (the LLM pipeline) is stubbed.
    """

    def __init__(self, *, auth_token, mesh_token, storage_dir: Path, reply: str):
        gw_section: dict = {}
        if auth_token:
            gw_section["auth"] = {"token": auth_token}
        if mesh_token:
            gw_section["mesh_token"] = mesh_token
        self.config = SimpleNamespace(auth_token=auth_token, raw_gateway_cfg=gw_section)
        self.config_manager = SimpleNamespace(global_config={"gateway": gw_section})
        self.router = SimpleNamespace(route_message=AsyncMock(return_value=reply))
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)


def _make_registry(tmp_path: Path, suffix: str):
    """Real NodeRegistry without real network calls (pattern from
    test_mesh_resilience.py / test_mesh_url_guard.py)."""
    from navig.mesh.registry import NodeRegistry

    with (
        patch("navig.mesh.registry._derive_node_id", return_value=f"navig-test-{suffix}"),
        patch("navig.mesh.registry.NodeRegistry._local_ip", return_value="127.0.0.1"),
        patch("navig.mesh.registry._measure_load", return_value=0.1),
        patch(
            "navig.mesh.registry.NodeRegistry._detect_capabilities",
            return_value=["llm", "shell"],
        ),
    ):
        return NodeRegistry(storage_dir=tmp_path)


def _registry_pointing_at(gateway_url: str, tmp_path: Path):
    """Tell node A about node B — addressing setup, exactly what discovery
    (or a manual ``/mesh/ping``) would do. The peer is a real NodeRecord in a
    real registry, so ``get_best_peer`` runs for real."""
    from navig.mesh.registry import NodeRecord

    reg = _make_registry(tmp_path, "nodeA")
    reg.upsert_peer(
        NodeRecord(
            node_id="node-b",
            hostname="host-b",
            os="linux",
            gateway_url=gateway_url,
            capabilities=["llm", "shell"],
            formation="",
            load=0.1,
            version="1.0.0",
            last_seen=time.time(),
            is_self=False,
        )
    )
    return reg


def _node_b_app(gw_b):
    """Node B: the REAL /llm/chat handler + mesh routes, plus a recording
    middleware. The middleware appends the attempt BEFORE dispatching, so a
    request that crossed the wire is recorded even if it is rejected."""
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import llm as llm_routes
    from navig.gateway.routes import mesh as mesh_routes

    received: list[dict] = []

    @web.middleware
    async def _record(request, handler):
        entry = {
            "path": request.path,
            "authorization": request.headers.get("Authorization"),
            "status": None,
        }
        received.append(entry)
        resp = await handler(request)
        entry["status"] = resp.status
        return resp

    app = web.Application(middlewares=[_record])
    llm_routes.register(app, gw_b)
    mesh_routes.register(app, gw_b)  # a non-/llm/chat guarded route (scoping)
    app["received"] = received
    return app


def _node_a_app(gw_a):
    """Node A: the real mesh routes (``POST /mesh/route`` is the entry under
    test)."""
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import mesh as mesh_routes

    app = web.Application()
    mesh_routes.register(app, gw_a)
    return app


@contextmanager
def _wire_node_a(registry, mesh_token: str):
    """Wire node A's addressing + credential source. Transport stays real:
    only ``get_registry`` (which registry to ask for a peer) and the global
    config read inside ``_get_mesh_token`` are pointed at test values —
    ``route_to_best_peer`` and ``_forward`` themselves run untouched."""
    with (
        patch("navig.mesh.router.get_registry", return_value=registry),
        patch(
            "navig.config.get_config_manager",
            return_value=SimpleNamespace(global_config={"gateway": {"mesh_token": mesh_token}}),
        ),
    ):
        yield


@pytest.fixture
async def cluster(tmp_path):
    """Real aiohttp TestServers (ephemeral ports on 127.0.0.1), started on
    demand and closed at test end — nothing leaks past the test."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    started: list[TestClient] = []

    async def start(app):
        client = TestClient(TestServer(app))
        await client.start_server()
        started.append(client)
        return client

    ns = SimpleNamespace(start=start, tmp_path=tmp_path)
    yield ns
    for client in started:
        await client.close()


def _llm_hits(app_b) -> list[dict]:
    return [h for h in app_b["received"] if h["path"] == "/llm/chat"]


async def _chat(client, text: str, headers: dict | None = None):
    return await client.post(
        "/llm/chat", json={"text": text, "scope": "personal"}, headers=headers or {}
    )


# ─────────────────────────── 1. shared-token fleet hop ───────────────────────


async def test_shared_token_fleet_hop_crosses_http_no_local_fallback(cluster):
    """A POST /mesh/route → B's real /llm/chat over real HTTP with the
    fleet-shared mesh bearer, and A returns B's answer without falling back."""
    gw_b = _StubGateway(
        auth_token=ADMIN_B,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b",
        reply=B_REPLY,
    )
    app_b = _node_b_app(gw_b)
    client_b = await cluster.start(app_b)
    b_url = str(client_b.server.make_url("/")).rstrip("/")

    gw_a = _StubGateway(
        auth_token=ADMIN_A,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "a",
        reply=A_FALLBACK_REPLY,
    )
    client_a = await cluster.start(_node_a_app(gw_a))
    reg_a = _registry_pointing_at(b_url, cluster.tmp_path / "reg-a")

    with _wire_node_a(reg_a, FLEET_TOKEN):
        resp = await client_a.post(
            "/mesh/route",
            json={"text": "cross the mesh", "scope": "personal"},
            headers={"Authorization": f"Bearer {ADMIN_A}"},
        )

    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    # A wraps B's envelope: data → B's envelope → B's payload
    payload = body["data"]["data"]
    assert payload["text"] == B_REPLY
    # _forward injects this only AFTER parsing B's real response
    assert payload["metadata"]["routed_via"] == "node-b"
    assert isinstance(payload["metadata"]["routed_rtt_ms"], float)

    # The hop REALLY crossed HTTP: B's middleware saw the /llm/chat request
    # carrying exactly what _forward sends — the mesh_token, never admin-a.
    hits = _llm_hits(app_b)
    assert len(hits) == 1, f"expected one real /llm/chat hit, got {hits}"
    assert hits[0]["authorization"] == f"Bearer {FLEET_TOKEN}"
    assert hits[0]["status"] == 200

    # B's channel router served it; A never fell back locally.
    gw_b.router.route_message.assert_awaited_once()
    assert gw_b.router.route_message.call_args.kwargs["message"] == "cross the mesh"
    gw_a.router.route_message.assert_not_awaited()


# ───────────────────────────── 2. admin bearer still works ───────────────────


async def test_admin_bearer_still_accepted_on_llm_chat(cluster):
    """The operator/deck credential path on /llm/chat is unchanged."""
    gw_b = _StubGateway(
        auth_token=ADMIN_B,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b",
        reply=B_REPLY,
    )
    client_b = await cluster.start(_node_b_app(gw_b))

    resp = await _chat(client_b, "direct admin call", {"Authorization": f"Bearer {ADMIN_B}"})
    assert resp.status == 200
    body = await resp.json()
    assert body["data"]["text"] == B_REPLY
    gw_b.router.route_message.assert_awaited_once()

    # ...and the admin path still rejects a wrong token.
    bad = await _chat(client_b, "nope", {"Authorization": "Bearer not-the-admin-token"})
    assert bad.status == 401
    assert (await bad.json())["error_code"] == "unauthorized"


async def test_mesh_bearer_accepted_directly_on_llm_chat(cluster):
    """The seam itself: a peer hop presenting ONLY the fleet mesh token is
    admitted by /llm/chat (what _forward sends on the wire)."""
    gw_b = _StubGateway(
        auth_token=ADMIN_B,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b",
        reply=B_REPLY,
    )
    client_b = await cluster.start(_node_b_app(gw_b))

    resp = await _chat(client_b, "peer hop", {"Authorization": f"Bearer {FLEET_TOKEN}"})
    assert resp.status == 200
    body = await resp.json()
    assert body["data"]["text"] == B_REPLY
    gw_b.router.route_message.assert_awaited_once()


# ──────────────── 3. wrong token → 401 → local fallback (no open access) ─────


async def test_wrong_token_401s_and_route_falls_back_locally(cluster):
    """Two installs never provisioned together hold different mesh tokens:
    the hop is rejected by B, A records the failure and processes locally —
    the caller never sees a proxy error, and B's pipeline is never touched."""
    gw_b = _StubGateway(
        auth_token=ADMIN_B,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b",
        reply=B_REPLY,
    )
    app_b = _node_b_app(gw_b)
    client_b = await cluster.start(app_b)
    b_url = str(client_b.server.make_url("/")).rstrip("/")

    gw_a = _StubGateway(
        auth_token=ADMIN_A,
        mesh_token=OTHER_FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "a",
        reply=A_FALLBACK_REPLY,
    )
    client_a = await cluster.start(_node_a_app(gw_a))
    reg_a = _registry_pointing_at(b_url, cluster.tmp_path / "reg-a")

    with _wire_node_a(reg_a, OTHER_FLEET_TOKEN):
        resp = await client_a.post(
            "/mesh/route",
            json={"text": "wrong fleet", "scope": "personal"},
            headers={"Authorization": f"Bearer {ADMIN_A}"},
        )

    assert resp.status == 200
    body = await resp.json()
    # Documented behavior at routes/mesh.py `_route`: None result → local.
    assert body["data"]["routed_via"] is None
    assert body["data"]["metadata"]["provider"] == "local"
    assert body["data"]["text"] == A_FALLBACK_REPLY

    # The attempt DID cross HTTP and B rejected it — not a skipped hop.
    hits = _llm_hits(app_b)
    assert len(hits) == 1
    assert hits[0]["authorization"] == f"Bearer {OTHER_FLEET_TOKEN}"
    assert hits[0]["status"] == 401
    gw_b.router.route_message.assert_not_awaited()
    gw_a.router.route_message.assert_awaited_once()


# ─── scoped-ness: mesh token opens ONLY the two peer routes (/llm/chat, ping) ─


async def test_mesh_token_rejected_on_non_llm_chat_guarded_route(cluster):
    """Regression guard for the scope of the fix: on the SAME node that
    accepts the mesh token on /llm/chat (and on /mesh/ping — the join flow's
    authenticated ping-back, covered in tests/gateway/test_install_routes.py),
    every OTHER guarded route must still reject it — admin-bearer-only."""
    gw_b = _StubGateway(
        auth_token=ADMIN_B,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b",
        reply=B_REPLY,
    )
    client_b = await cluster.start(_node_b_app(gw_b))

    via_mesh = await client_b.post(
        "/a2a", json={}, headers={"Authorization": f"Bearer {FLEET_TOKEN}"}
    )
    assert via_mesh.status == 401
    assert (await via_mesh.json())["error_code"] == "unauthorized"

    # The admin bearer is still THE credential for non-peer routes: it passes
    # the guard and fails on the empty A2A body instead (200 JSON-RPC error).
    via_admin = await client_b.post(
        "/a2a", json={}, headers={"Authorization": f"Bearer {ADMIN_B}"}
    )
    assert via_admin.status != 401
    assert "error" in await via_admin.json()

    # And the very same node still accepts the mesh token on /llm/chat.
    via_chat = await _chat(
        client_b, "scoped", {"Authorization": f"Bearer {FLEET_TOKEN}"}
    )
    assert via_chat.status == 200


# ────────────────────── 5. Gate 0: no secrets at all → 503 ───────────────────


async def test_both_tokens_missing_fails_closed_with_503(cluster):
    """A gateway with NEITHER the admin token (failed mint) NOR a mesh token
    must still refuse authenticated routes with 503 — with a bearer AND with
    no header at all."""
    gw = _StubGateway(
        auth_token=None,
        mesh_token=None,
        storage_dir=cluster.tmp_path / "b0",
        reply=B_REPLY,
    )
    client = await cluster.start(_node_b_app(gw))

    with_header = await _chat(client, "anything", {"Authorization": "Bearer some-token"})
    assert with_header.status == 503
    assert (await with_header.json())["error_code"] == "auth_not_configured"

    no_header = await _chat(client, "anything")
    assert no_header.status == 503
    assert (await no_header.json())["error_code"] == "auth_not_configured"
    gw.router.route_message.assert_not_awaited()


async def test_mesh_only_install_authenticates_peers_and_rejects_garbage(cluster):
    """Honest handling of an absent ADMIN token: a peer install always has a
    mesh token (``_ensure_mesh_token`` mints one at boot), so with the admin
    mint missing the peer path authenticates via the mesh token — while a
    wrong bearer is 401 (a credential IS configured → not 503)."""
    gw = _StubGateway(
        auth_token=None,
        mesh_token=FLEET_TOKEN,
        storage_dir=cluster.tmp_path / "b1",
        reply=B_REPLY,
    )
    client = await cluster.start(_node_b_app(gw))

    good = await _chat(
        client, "peer with broken admin mint", {"Authorization": f"Bearer {FLEET_TOKEN}"}
    )
    assert good.status == 200

    garbage = await _chat(client, "nope", {"Authorization": "Bearer garbage"})
    assert garbage.status == 401
    assert (await garbage.json())["error_code"] == "unauthorized"

    absent = await _chat(client, "no header")
    assert absent.status == 401


# ───────────────────────────────── entry point ───────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
