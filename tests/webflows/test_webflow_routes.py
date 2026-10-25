"""The /api/deck/webflows surface through the REAL router (register_deck_routes), plus the
deck auth rule the Dock relies on: only the paired extension origin is trusted."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck import register_deck_routes
from navig.webflows import SCHEMA, webflows_dir

ORIGIN = "http://127.0.0.1:8123"
FLOW = {
    "schema": SCHEMA,
    "name": "Route flow",
    "origins": [ORIGIN],
    "start_url": f"{ORIGIN}/order.html",
    "params": [{"name": "pw", "type": "secret"}],
    "steps": [
        {"action": "fill", "css": "#pw", "value": "{{pw}}", "sensitive": True},
        {"action": "click", "role": "button", "name": "Next"},
    ],
}


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> web.Application:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    assert webflows_dir() == tmp_path / "cfg" / "webflows"
    a = web.Application()
    register_deck_routes(a, require_auth=False)
    return a


async def test_lifecycle(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/deck/webflows", json={**FLOW, "id": "wf_someone-elses"})
        assert r.status == 200, await r.text()
        wf = (await r.json())["webflow"]
        assert wf["id"] == "wf_route-flow" and wf["revision"] == 1  # POST never takes an id

        rows = (await (await client.get("/api/deck/webflows")).json())["webflows"]
        assert [(x["id"], x["steps"]) for x in rows] == [("wf_route-flow", 2)]

        r = await client.put(f"/api/deck/webflows/{wf['id']}", json={**wf, "name": "Renamed"})
        assert (await r.json())["webflow"]["revision"] == 2

        got = await (await client.get(f"/api/deck/webflows/{wf['id']}")).json()
        assert got["webflow"]["name"] == "Renamed" and got["revisions"] == [1, 2]
        old = await (await client.get(f"/api/deck/webflows/{wf['id']}?revision=1")).json()
        assert old["webflow"]["name"] == "Route flow"

        r = await client.get(f"/api/deck/webflows/{wf['id']}/export")
        assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
        assert '"schema": "navig.webflow/1"' in await r.text()

        assert (await client.delete(f"/api/deck/webflows/{wf['id']}")).status == 200
        assert (await client.get(f"/api/deck/webflows/{wf['id']}")).status == 404


async def test_a_secret_literal_is_a_400_and_nothing_is_written(app: web.Application, tmp_path: Path) -> None:
    bad = {**FLOW, "steps": [{"action": "fill", "css": "#pw", "value": "hunter2", "sensitive": True}]}
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/deck/webflows", json=bad)
        assert r.status == 400
        body = await r.json()
        assert any("never a literal" in e for e in body["errors"])
    root = tmp_path / "cfg" / "webflows"
    assert not root.exists() or not any(root.iterdir())


async def test_bad_requests(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/api/deck/webflows", data="nope")).status == 400
        assert (await client.put("/api/deck/webflows/wf_missing", json=FLOW)).status == 404
        assert (await client.get("/api/deck/webflows/..%2Fetc")).status in (400, 404)
        assert (await client.get("/api/deck/webflows/wf_x?revision=abc")).status == 400


def test_only_the_paired_extension_origin_is_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Dock reaches these routes with its extension origin — never another extension's."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    from navig.dock import pairing

    ours, theirs = "a" * 32, "b" * 32
    pairing.pair(ours)
    assert pairing.extension_origin_trusted(f"chrome-extension://{ours}", "/api/deck/webflows")
    assert not pairing.extension_origin_trusted(f"chrome-extension://{theirs}", "/api/deck/webflows")
