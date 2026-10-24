"""Read-only deck endpoints routes: list, show, filters, 404/503, no mutations."""

from __future__ import annotations

import pytest

from navig.endpoints.client import HomeBridge
from navig.endpoints.model import validate_endpoint
from navig.endpoints.store import EndpointStore
from tests.endpoints.fixtures import FakeClock, FakeHome, projection_now

pytestmark = pytest.mark.integration


def _store_for(home: FakeHome) -> EndpointStore:
    return EndpointStore(
        HomeBridge(runner=home.runner),
        clock=FakeClock(),
        wall_clock=projection_now,
    )


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import endpoints as route

    app = web.Application()
    app.router.add_get("/api/deck/endpoints", route.handle_deck_endpoints_list)
    app.router.add_get("/api/deck/endpoints/{endpoint_id}", route.handle_deck_endpoints_show)
    return app


@pytest.fixture()
def wired(monkeypatch):
    home = FakeHome()
    store = _store_for(home)
    monkeypatch.setattr(
        "navig.gateway.deck.routes.endpoints._store", lambda: store)
    return home, store


async def test_list_returns_every_endpoint_across_two_providers(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/deck/endpoints")
        assert response.status == 200
        body = await response.json()
    assert body["ok"] is True
    assert body["count"] == 2
    providers = {endpoint["id"].split(":", 1)[0] for endpoint in body["endpoints"]}
    assert providers == {"govee", "ha"}
    assert body["unmapped"] == {}
    assert sorted(row["provider"] for row in body["providers"]) == ["govee", "ha", "tuya"]
    for endpoint in body["endpoints"]:
        assert validate_endpoint(endpoint) == []
        assert endpoint["status"]["online"] is True


async def test_list_filters_by_provider_and_room(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        only_ha = await (await client.get("/api/deck/endpoints?provider=ha")).json()
        only_office = await (await client.get("/api/deck/endpoints?room=office")).json()
        padded = await (await client.get("/api/deck/endpoints?provider=%20ha%20")).json()
    assert only_ha["count"] == 1
    assert only_ha["endpoints"][0]["id"] == "ha:living-room-ac"
    assert only_office["count"] == 1
    assert only_office["endpoints"][0]["id"] == "govee:desk-lamp"
    assert padded["count"] == 1, "filters are whitespace-stripped"


async def test_a_bogus_filter_is_a_400(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        too_long = await client.get("/api/deck/endpoints?provider=" + "x" * 65)
        assert too_long.status == 400
        body = await too_long.json()
        assert body["ok"] is False
        assert "too long" in body["error"]


async def test_show_returns_one_endpoint(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/deck/endpoints/govee:desk-lamp")
        assert response.status == 200
        body = await response.json()
    assert body["ok"] is True
    endpoint = body["endpoint"]
    assert endpoint["id"] == "govee:desk-lamp"
    assert validate_endpoint(endpoint) == []
    types = {capability["type"] for capability in endpoint["capabilities"]}
    assert "devices.capabilities.on_off" in types


async def test_show_unknown_is_404_with_the_list_hint(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/deck/endpoints/govee:nope")
        assert response.status == 404
        body = await response.json()
    assert body["ok"] is False
    assert "navig endpoints list" in body["hint"]


async def test_a_missing_plugin_is_503_with_the_install_hint(monkeypatch) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    home = FakeHome()
    home.fail["list"] = (2, "no such command: home")
    monkeypatch.setattr(
        "navig.gateway.deck.routes.endpoints._store",
        lambda: _store_for(home),
    )
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/deck/endpoints")
        assert response.status == 503
        body = await response.json()
    assert body["ok"] is False
    assert "navig plugin install navig-home" in body["hint"]


async def test_an_unreadable_status_is_503_on_show(monkeypatch) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    home = FakeHome()
    home.fail["status"] = (2, "inventory unreadable")
    monkeypatch.setattr(
        "navig.gateway.deck.routes.endpoints._store",
        lambda: _store_for(home),
    )
    async with TestClient(TestServer(_app())) as client:
        response = await client.get("/api/deck/endpoints/govee:desk-lamp")
        assert response.status == 503
        body = await response.json()
    assert body["ok"] is False
    assert "navig home status" in body["hint"]


async def test_the_route_surface_has_no_mutation_verb(wired) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        for method, path in (
            ("post", "/api/deck/endpoints"),
            ("put", "/api/deck/endpoints/govee:desk-lamp"),
            ("delete", "/api/deck/endpoints/govee:desk-lamp"),
        ):
            response = await getattr(client, method)(path)
            assert response.status == 405, (method, path)
