"""/api/deck/memory/facts (list · PATCH · DELETE) and /api/deck/memory/summary.

The saved-memory manager behind the OS Personalization page. A real KeyFactStore on a
tmp db, driven through a real TestServer, so the thread hop and the secrets filter are
exercised for real.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def store(tmp_path):
    from navig.memory.key_facts import get_key_fact_store, reset_key_fact_store

    reset_key_fact_store()
    s = get_key_fact_store(db_path=tmp_path / "key_facts.db")
    yield s
    reset_key_fact_store()


async def _client():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import memory as mem

    app = web.Application()
    app.router.add_get("/api/deck/memory/facts", mem.handle_memory_list)
    app.router.add_patch("/api/deck/memory/facts/{fact_id}", mem.handle_memory_update)
    app.router.add_delete("/api/deck/memory/facts/{fact_id}", mem.handle_memory_delete)
    app.router.add_get("/api/deck/memory/summary", mem.handle_memory_summary)
    app.router.add_post("/api/deck/memory/summary", mem.handle_memory_summary)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


def _add(store, content, **kw):
    from navig.memory.key_facts import KeyFact

    return store.upsert(KeyFact(content=content, **kw))


async def test_list_returns_saved_memories_pinned_first(store):
    a = _add(store, "Uses Neovim")
    b = _add(store, "Has a cat called Miso")
    _add(store, "Maybe likes jazz", approved=None)
    store.set_pinned(b.id, True)
    c = await _client()
    try:
        body = await (await c.get("/api/deck/memory/facts")).json()
        assert body["total"] == 2
        assert [f["id"] for f in body["facts"]] == [b.id, a.id]
        assert body["facts"][0]["pinned"] is True
        body = await (await c.get("/api/deck/memory/facts?q=neovim&limit=999999")).json()
        assert [f["id"] for f in body["facts"]] == [a.id]
    finally:
        await c.close()


async def test_patch_edits_and_pins(store):
    f = _add(store, "Uses Vim")
    c = await _client()
    try:
        r = await c.patch(f"/api/deck/memory/facts/{f.id}", json={"content": "Uses Neovim"})
        assert r.status == 200
        assert (await r.json())["fact"]["content"] == "Uses Neovim"
        r = await c.patch(f"/api/deck/memory/facts/{f.id}", json={"pinned": True})
        assert (await r.json())["fact"]["pinned"] is True
        assert store.get(f.id).pinned is True
    finally:
        await c.close()


async def test_patch_redacts_a_secret_and_refuses_a_pure_one(store):
    f = _add(store, "Home router")
    c = await _client()
    try:
        r = await c.patch(
            f"/api/deck/memory/facts/{f.id}",
            json={"content": "Home router password is hunter2xyz!"},
        )
        fact = (await r.json())["fact"]
        assert fact["redacted"] is True and "hunter2xyz" not in fact["content"]
        r = await c.patch(f"/api/deck/memory/facts/{f.id}", json={"content": "password is x9y8z7"})
        assert r.status == 422
        assert "x9y8z7" not in (await r.text())
    finally:
        await c.close()


@pytest.mark.parametrize(
    "body",
    [{}, {"pinned": "yes"}, {"content": ""}, {"content": 5}, {"content": "x" * 3000}],
)
async def test_patch_rejects_bad_bodies(store, body):
    f = _add(store, "Uses Vim")
    c = await _client()
    try:
        r = await c.patch(f"/api/deck/memory/facts/{f.id}", json=body)
        assert r.status == 400
        assert store.get(f.id).content == "Uses Vim"
    finally:
        await c.close()


async def test_patch_and_delete_unknown_ids_are_404(store):
    c = await _client()
    try:
        assert (await c.patch("/api/deck/memory/facts/nope", json={"pinned": True})).status == 404
        assert (await c.delete("/api/deck/memory/facts/nope")).status == 404
    finally:
        await c.close()


async def test_delete_forgets(store):
    f = _add(store, "Uses Vim")
    c = await _client()
    try:
        assert (await c.delete(f"/api/deck/memory/facts/{f.id}")).status == 200
        assert (await (await c.get("/api/deck/memory/facts")).json())["total"] == 0
    finally:
        await c.close()


async def test_summary_get_is_cached_read_post_generates(store, monkeypatch):
    _add(store, "Prefers dark themes")

    async def _fake_llm(prompt: str) -> str:
        return "- You prefer dark themes."

    monkeypatch.setattr("navig.agent.memory_summary._default_llm", _fake_llm)
    c = await _client()
    try:
        before = await (await c.get("/api/deck/memory/summary")).json()
        assert before["summary"] == "" and before["stale"] is True
        made = await (await c.post("/api/deck/memory/summary")).json()
        assert made["summary"] == "- You prefer dark themes."
        after = await (await c.get("/api/deck/memory/summary")).json()
        assert after["stale"] is False and after["summary"] == made["summary"]
    finally:
        await c.close()


async def test_summary_failure_is_a_502_not_a_stale_success(store, monkeypatch):
    _add(store, "Prefers dark themes")

    async def _boom(prompt: str) -> str:
        raise ConnectionError("provider down")

    monkeypatch.setattr("navig.agent.memory_summary._default_llm", _boom)
    c = await _client()
    try:
        r = await c.post("/api/deck/memory/summary")
        assert r.status == 502
    finally:
        await c.close()
