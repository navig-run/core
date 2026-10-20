"""Phase C gateway: /spaces/{id}/relations (GET/POST) and /spaces/{id}/context.

What this pins end-to-end through the real router + handlers:
- GET relations returns the shared graph payload (parent/links/children/flags);
- unknown id → 404, bad op → 400, unregistered link target → 400, cycle → 400;
- POST link/parent mutate the manifest and the next GET reflects them; parent:null clears;
- GET context returns snapshot-shaped linked context with provenance, focus echo,
  the deny list enforced (a vault file never appears), and 404 for unknown ids.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import catalog as cat

    app = web.Application()
    app.router.add_get("/spaces/{id}/relations", cat.handle_deck_space_relations)
    app.router.add_post("/spaces/{id}/relations", cat.handle_deck_space_relations_update)
    app.router.add_get("/spaces/{id}/context", cat.handle_deck_space_linked_context)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def _mk_space(root, sid: str) -> "object":
    p = root / "spaces" / f"{sid}-space"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    from navig.spaces import registry

    registry.register(p, id=sid, name=sid.title(), source="external", enabled=True)
    return p


async def test_unknown_space_is_404_on_all_three(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        assert (await client.get("/spaces/nope/relations")).status == 404
        assert (await client.post("/spaces/nope/relations", json={"op": "link", "space": "x"})).status == 404
        assert (await client.get("/spaces/nope/context")).status == 404


async def test_relations_round_trip_through_post(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    _mk_space(isolated, "alpha")
    _mk_space(isolated, "beta")

    async with TestClient(TestServer(_app())) as client:
        empty = await (await client.get("/spaces/alpha/relations")).json()
        assert empty["ok"] and empty["data"]["parent"] is None and empty["data"]["links"] == []

        linked = await (
            await client.post(
                "/spaces/alpha/relations",
                json={"op": "link", "space": "beta", "include": ["wiki/**"], "exclude": ["vault/**"]},
            )
        ).json()
        assert linked["ok"]
        assert linked["data"]["links"][0]["id"] == "beta"
        assert linked["data"]["links"][0]["include"] == ["wiki/**"]

        parented = await (
            await client.post("/spaces/alpha/relations", json={"op": "parent", "space": "beta"})
        ).json()
        assert parented["data"]["parent"]["id"] == "beta"
        assert parented["data"]["children"] == []  # alpha hangs UNDER beta, not the reverse
        # The graph is inbound-aware: BETA's payload must now list alpha as its child.
        beta_graph = await (await client.get("/spaces/beta/relations")).json()
        assert beta_graph["data"]["children"] == ["alpha"]

        cleared = await (
            await client.post("/spaces/alpha/relations", json={"op": "parent", "space": None})
        ).json()
        assert cleared["data"]["parent"] is None


async def test_write_rules_are_enforced_server_side(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    _mk_space(isolated, "alpha")
    _mk_space(isolated, "beta")
    _mk_space(isolated, "gamma")

    async with TestClient(TestServer(_app())) as client:
        bad_op = await client.post("/spaces/alpha/relations", json={"op": "explode"})
        assert bad_op.status == 400
        assert "op must be one of" in (await bad_op.json())["error"]

        unreg = await client.post("/spaces/alpha/relations", json={"op": "link", "space": "ghost"})
        assert unreg.status == 400
        assert "not a registered space" in (await unreg.json())["error"]

        self_link = await client.post("/spaces/alpha/relations", json={"op": "link", "space": "alpha"})
        assert self_link.status == 400
        assert "itself" in (await self_link.json())["error"]

        # alpha -> beta, then gamma's parent = alpha would be fine; close a cycle:
        await client.post("/spaces/alpha/relations", json={"op": "parent", "space": "beta"})
        await client.post("/spaces/beta/relations", json={"op": "parent", "space": "gamma"})
        cycle = await client.post("/spaces/gamma/relations", json={"op": "parent", "space": "alpha"})
        assert cycle.status == 400
        assert "cycle" in (await cycle.json())["error"]


async def test_context_returns_snapshot_shape_with_provenance_and_deny(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    _mk_space(isolated, "alpha")
    beta = _mk_space(isolated, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True, exist_ok=True)
    (beta / ".navig" / "wiki" / "guide.md").write_text("# guide\nshared knowledge", encoding="utf-8")
    (beta / "vault").mkdir(exist_ok=True)
    (beta / "vault" / "keys.md").write_text("secret material", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        await client.post(
            "/spaces/alpha/relations", json={"op": "link", "space": "beta", "include": ["**"]}
        )
        resp = await client.get("/spaces/alpha/context?query=deployment")
        assert resp.status == 200
        body = await resp.json()
        snap = body["data"]["snapshot"]
        assert body["data"]["focus"] == "deployment"
        assert snap["space"] == "alpha"
        assert snap["parent_cycle"] is False
        rels = [i["rel"] for i in snap["items"]]
        assert ".navig/wiki/guide.md" in rels
        assert all("vault" not in r for r in rels), f"deny list leaked: {rels}"
        item = next(i for i in snap["items"] if i["rel"] == ".navig/wiki/guide.md")
        for key in ("space", "path", "rel", "mtime"):  # provenance contract
            assert key in item and item[key] != ""
        assert item["space"] == "beta" and item["source"] == "link"
