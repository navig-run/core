"""GET /mesh/topology — the endpoint the mesh guide has always documented.

``docs/guides/mesh-multi-machine.md`` tells users to run

    curl http://localhost:8789/mesh/topology     # Topology report with SPOF analysis

and lists it beside ``/mesh/peers`` in its API table. The route was never
registered, so that documented command returned a 404 — while
``mesh/router.get_topology_report()``, the function it describes, was complete,
tested, and had **zero production callers**.

Two questions, deliberately separate, because #1050 is what happens when only one
of them is asked:

* **Does it EXIST?** — ``register()`` must actually add the route. A test that
  drives ``_topology(gw)`` directly passes just as happily when nobody wires it,
  which is exactly how four Telegram mesh commands shipped calling 404s.
* **Does it WORK?** — the handler must produce the report over real HTTP, and
  degrade rather than 500 when analysis fails.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.routes.mesh as mesh_routes

# The exact path the shipped guide tells users to curl. Spelled out here rather
# than imported so that renaming the route fails this test instead of silently
# following it — the doc is a published contract.
DOCUMENTED_PATH = "/mesh/topology"

_REPORT = {
    "topology": {"min_redundant_paths": 0, "redundancy_satisfied": False,
                 "nodes_with_single_path": []},
    "criteria": {},
    "all_criteria_pass": False,
    "spof_nodes": [],
    "peers": [],
    "routing_metrics": {},
}


async def _ok_data(resp):
    body = await resp.json()
    assert body.get("ok") is True, body
    return body["data"]


def test_register_actually_adds_the_documented_route() -> None:
    """`register()` wires it — not just that a handler exists somewhere."""
    app = web.Application()
    mesh_routes.register(app, SimpleNamespace(storage_dir=None))

    routes = {
        (r.method, r.resource.canonical)
        for r in app.router.routes()
        if r.resource is not None
    }
    assert ("GET", DOCUMENTED_PATH) in routes, (
        f"{DOCUMENTED_PATH} is not registered, so the curl in "
        "docs/guides/mesh-multi-machine.md 404s. Registering the handler is the "
        "whole point — a handler nothing routes to is #1050 again."
    )


async def test_topology_returns_the_report_over_http(monkeypatch) -> None:
    """Real HTTP against the registered route returns the SPOF report."""
    import navig.mesh.router as mesh_router

    monkeypatch.setattr(mesh_router, "get_topology_report", lambda: _REPORT)

    app = web.Application()
    mesh_routes.register(app, SimpleNamespace(storage_dir=None))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(DOCUMENTED_PATH)
        assert resp.status == 200
        data = await _ok_data(resp)

    assert data["spof_nodes"] == []
    assert "topology" in data and "criteria" in data


async def test_topology_degrades_instead_of_500(monkeypatch) -> None:
    """A failing analysis is a 200 with available:false — mesh never hard-fails.

    Every mesh route follows this Phase-1 rule (see `_scan`); a 500 here would
    make a transient registry problem indistinguishable from a broken gateway.
    """
    import navig.mesh.router as mesh_router

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(mesh_router, "get_topology_report", _boom)

    app = web.Application()
    mesh_routes.register(app, SimpleNamespace(storage_dir=None))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(DOCUMENTED_PATH)
        assert resp.status == 200
        data = await _ok_data(resp)

    assert data["available"] is False
    assert "registry unavailable" in data["reason"]


async def test_topology_binds_the_gateways_own_registry(monkeypatch) -> None:
    """The handler must prime the registry singleton with the gateway's dir.

    ``get_registry()`` honours ``storage_dir`` only on the FIRST call process-wide,
    and ``get_topology_report()`` passes none. Without priming, this route would
    report whichever registry happened to be bound first — possibly a different
    one than ``/mesh/peers`` answers from.
    """
    seen: list[object] = []
    monkeypatch.setattr(
        mesh_routes, "get_registry", lambda *a, **_k: seen.append(a[0] if a else None)
    )
    import navig.mesh.router as mesh_router

    monkeypatch.setattr(mesh_router, "get_topology_report", lambda: _REPORT)

    app = web.Application()
    mesh_routes.register(app, SimpleNamespace(storage_dir="/tmp/navig-test-dir"))
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(DOCUMENTED_PATH)).status == 200

    assert seen == ["/tmp/navig-test-dir"], (
        "the handler did not prime get_registry with the gateway's storage_dir"
    )
