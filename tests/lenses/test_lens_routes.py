"""The /api/deck/lenses surface, through the REAL router (register_deck_routes).

A handler that is never registered is a 404 with no error anywhere, and a dynamic
/lenses/{lens_id} registered first would swallow /lenses/compiled — so these tests go
through the same router the gateway mounts, not the handlers directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck import register_deck_routes
from navig.lenses import lenses_dir

SRC = (
    "// ==UserScript==\n"
    "// @name Route Lens\n"
    "// @match *://example.com/*\n"
    "// ==/UserScript==\n"
    "window.__route = 1;\n"
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> web.Application:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    assert lenses_dir() == tmp_path / "cfg" / "lenses"
    monkeypatch.setattr(
        "navig.license.entitlement.live_caps_and_rank", lambda: ([], 0), raising=True
    )
    a = web.Application()
    register_deck_routes(a, require_auth=False)
    return a


async def test_full_lifecycle(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/deck/lenses", json={"source": SRC})
        assert r.status == 200, await r.text()
        lens = (await r.json())["lens"]
        lens_id = lens["id"]
        assert lens["enabled"] is True and lens["origin"] == "user"

        listed = await (await client.get("/api/deck/lenses")).json()
        assert [x["id"] for x in listed["lenses"]] == [lens_id]

        r = await client.get("/api/deck/lenses/compiled?host=chrome")
        assert r.status == 200
        etag = r.headers["ETag"]
        body = await r.json()
        assert [x["id"] for x in body["lenses"]] == [lens_id]
        assert body["lenses"][0]["matches"] == ["*://example.com/*"]
        assert body["lenses"][0]["chromeRunAt"] == "document_idle"

        r = await client.get("/api/deck/lenses/compiled?host=chrome", headers={"If-None-Match": etag})
        assert r.status == 304

        r = await client.patch(f"/api/deck/lenses/{lens_id}", json={"enabled": False})
        assert (await r.json())["lens"]["enabled"] is False
        body = await (await client.get("/api/deck/lenses/compiled?host=os")).json()
        assert body["lenses"] == []

        got = await (await client.get(f"/api/deck/lenses/{lens_id}")).json()
        assert got["lens"]["source"] == SRC

        assert (await client.delete(f"/api/deck/lenses/{lens_id}")).status == 200
        assert (await client.get(f"/api/deck/lenses/{lens_id}")).status == 404


async def test_agent_origin_is_stored_disabled(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        r = await client.post(
            "/api/deck/lenses", json={"source": SRC, "origin": "agent", "enabled": True}
        )
        lens = (await r.json())["lens"]
        assert lens["enabled"] is False and lens["origin"] == "agent"


async def test_preview_parses_without_writing(app: web.Application) -> None:
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/deck/lenses/preview", json={"source": SRC})
        body = await r.json()
        assert body["manifest"]["name"] == "Route Lens"
        assert (await (await client.get("/api/deck/lenses")).json())["lenses"] == []


@pytest.mark.parametrize(
    ("method", "path", "payload", "status"),
    [
        ("post", "/api/deck/lenses", {"source": "alert(1)"}, 400),
        ("post", "/api/deck/lenses", {}, 400),
        ("post", "/api/deck/lenses", {"url": "http://example.com/x.user.js"}, 400),
        ("post", "/api/deck/lenses", {"url": "https://127.0.0.1/x.user.js"}, 400),
        ("post", "/api/deck/lenses", {"source": SRC, "origin": "root"}, 400),
        ("post", "/api/deck/lenses/preview", {"source": "nope"}, 400),
        ("patch", "/api/deck/lenses/missing", {"enabled": True}, 404),
        ("patch", "/api/deck/lenses/missing", {"enabled": "yes"}, 400),
        ("delete", "/api/deck/lenses/missing", None, 404),
        ("get", "/api/deck/lenses/compiled?host=evil", None, 400),
    ],
)
async def test_bad_requests_are_refused_with_a_reason(
    app: web.Application, method: str, path: str, payload, status: int
) -> None:
    async with TestClient(TestServer(app)) as client:
        kwargs = {"json": payload} if payload is not None else {}
        r = await getattr(client, method)(path, **kwargs)
        assert r.status == status, await r.text()
        assert (await r.json())["ok"] is False


async def test_review_flow_through_the_real_router(app: web.Application, monkeypatch) -> None:
    """An agent proposal notifies, lists as waiting, shows its diff, and is approved."""
    sent: list[tuple[str, str, dict]] = []

    async def fake_dispatch(type_key, title, body="", **kw):  # noqa: ANN001
        sent.append((type_key, title, kw.get("data") or {}))
        return {"ok": True}

    monkeypatch.setattr("navig.notify.dispatch", fake_dispatch)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/deck/lenses", json={"source": SRC, "origin": "agent"})
        lens = (await r.json())["lens"]
        assert lens["enabled"] is False and lens["review"] == "proposed"
        assert lens["needs_review"] is True

        waiting = await (await client.get("/api/deck/lenses/reviews")).json()
        assert [x["id"] for x in waiting["lenses"]] == [lens["id"]]

        details = await (await client.get(f"/api/deck/lenses/{lens['id']}/review")).json()
        assert details["ok"] and details["kind"] == "proposed"
        assert "+window.__route = 1;" in details["diff"]

        r = await client.post(f"/api/deck/lenses/{lens['id']}/review", json={"action": "approve"})
        assert (await r.json())["lens"]["enabled"] is True
        assert (await (await client.get("/api/deck/lenses/reviews")).json())["lenses"] == []

        r = await client.post(f"/api/deck/lenses/{lens['id']}/review", json={"action": "approve"})
        assert r.status == 404  # nothing is waiting any more
        r = await client.post(f"/api/deck/lenses/{lens['id']}/review", json={"action": "nope"})
        assert r.status == 400
    assert [(t, title) for t, title, _ in sent] == [("lens_review", "Lens proposed")]
    assert sent[0][2] == {"lenses": [lens["id"]]}


async def test_check_updates_route_stages_and_reports(app: web.Application, monkeypatch) -> None:
    newer = SRC.replace("==/UserScript==", "@version 2.0.0\n// ==/UserScript==")
    monkeypatch.setattr("navig.lenses.store.fetch_text", lambda url: newer)
    monkeypatch.setattr("navig.notify.dispatch", _no_dispatch)
    async with TestClient(TestServer(app)) as client:
        url = "https://lenses.example/route.user.js"
        r = await client.post("/api/deck/lenses", json={"source": SRC, "url": url})
        lens_id = (await r.json())["lens"]["id"]
        body = await (await client.post("/api/deck/lenses/check-updates", json={})).json()
        assert [row["status"] for row in body["results"]] == ["staged"]
        r = await client.post(f"/api/deck/lenses/{lens_id}/review", json={"action": "reject"})
        kept = (await r.json())["lens"]
        assert kept["pending"] is None and kept["enabled"] is True


async def _no_dispatch(*a, **k):  # noqa: ANN002, ANN003
    return {"ok": True}
