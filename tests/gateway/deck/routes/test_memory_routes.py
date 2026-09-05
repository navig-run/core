"""/api/memory/* (and the /api/deck/memory/* mirror) — served off the event loop, clamped, escaped.

Every handler drove KeyFactStore (synchronous SQLite) directly in the async request path — including
the WRITES (approve/reject/approve_all) and the bulk import_json, which loops dup-lookup + upsert +
FTS insert per fact. Those now run in ``asyncio.to_thread``; the store keeps a per-thread connection
(threading.local, check_same_thread=False) and serializes writes behind its own _write_lock, so
cross-thread use is safe. These tests drive a REAL store through a real TestServer, so the thread hop
is genuinely exercised rather than mocked away.

Also covers: ?limit= is clamped (it was unbounded — a caller could pull the whole store), and the
built-in review page escapes every interpolated field.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def store(tmp_path):
    """A real KeyFactStore on a tmp db, installed as the singleton the handlers resolve."""
    from navig.memory.key_facts import get_key_fact_store, reset_key_fact_store

    reset_key_fact_store()
    s = get_key_fact_store(db_path=tmp_path / "key_facts.db")
    yield s
    reset_key_fact_store()


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import memory as mem

    app = web.Application()
    app.router.add_get("/api/memory/facts/pending", mem.handle_memory_pending)
    app.router.add_post("/api/memory/facts/{fact_id}/approve", mem.handle_memory_approve)
    app.router.add_post("/api/memory/facts/{fact_id}/reject", mem.handle_memory_reject)
    app.router.add_get("/api/memory/facts/export", mem.handle_memory_export)
    app.router.add_post("/api/memory/facts/import", mem.handle_memory_import)
    app.router.add_get("/memory/review", mem.handle_memory_review_page)
    return app


async def _client():
    from aiohttp.test_utils import TestClient, TestServer

    c = TestClient(TestServer(_app()))
    await c.start_server()
    return c


def _seed(store, n=3):
    from navig.memory.key_facts import KeyFact

    ids = []
    for i in range(n):
        f = KeyFact(content=f"fact number {i}", category="preference", approved=None)
        store.upsert(f)
        ids.append(f.id)
    return ids


async def test_pending_returns_facts_through_thread_hop(store):
    """A real cross-thread SQLite read — the core safety claim of the off-loop change."""
    _seed(store, 3)
    c = await _client()
    try:
        r = await c.get("/api/memory/facts/pending")
        assert r.status == 200
        body = await r.json()
        assert body["count"] == 3
        assert len(body["facts"]) == 3
        assert {f["category"] for f in body["facts"]} == {"preference"}
    finally:
        await c.close()


@pytest.mark.parametrize(
    ("qs", "expected"),
    [
        ("?limit=99999999", 500),  # clamped to _MAX_PENDING_LIMIT (was unbounded)
        ("?limit=-5", 1),          # clamped up to 1 (a negative limit reached SQL before)
        ("?limit=abc", 200),       # unparseable → documented default
        ("?limit=50", 50),         # in-range passes through
        ("", 200),                 # absent → default
    ],
)
async def test_pending_limit_is_clamped(store, monkeypatch, qs, expected):
    captured = {}

    def _spy(limit=200):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(store, "get_pending", _spy)
    c = await _client()
    try:
        r = await c.get(f"/api/memory/facts/pending{qs}")
        assert r.status == 200
        assert captured["limit"] == expected
    finally:
        await c.close()


async def test_approve_and_reject_write_through_thread(store):
    ids = _seed(store, 3)
    c = await _client()
    try:
        r = await c.post(f"/api/memory/facts/{ids[0]}/approve")
        assert r.status == 200 and (await r.json())["ok"] is True

        r = await c.post(f"/api/memory/facts/{ids[1]}/reject", json={"reason": "nope"})
        assert r.status == 200 and (await r.json())["ok"] is True

        # one approved + one rejected → a single fact still pending
        left = await (await c.get("/api/memory/facts/pending")).json()
        assert left["count"] == 1

        # unknown id → 404, not a 500
        r = await c.post("/api/memory/facts/does-not-exist/approve")
        assert r.status == 404
    finally:
        await c.close()


async def test_approve_all_pending(store):
    _seed(store, 4)
    c = await _client()
    try:
        r = await c.post("/api/memory/facts/all/approve")
        assert r.status == 200
        assert (await r.json())["approved"] == 4
        assert (await (await c.get("/api/memory/facts/pending")).json())["count"] == 0
    finally:
        await c.close()


async def test_export_json_and_markdown(store):
    _seed(store, 2)
    c = await _client()
    try:
        r = await c.get("/api/memory/facts/export?format=md")
        assert r.status == 200
        assert r.content_type == "text/markdown"

        r = await c.get("/api/memory/facts/export?all=1")
        assert r.status == 200
        assert "fact number 0" in await r.text()
    finally:
        await c.close()


async def test_import_valid_and_invalid(store):
    c = await _client()
    try:
        r = await c.post(
            "/api/memory/facts/import",
            data='{"facts":[{"content":"imported one","category":"technical"}]}',
        )
        assert r.status == 200
        assert (await r.json())["added"] == 1

        r = await c.post("/api/memory/facts/import", data="not json at all")
        assert r.status == 400
        assert "error" in await r.json()
    finally:
        await c.close()


async def test_review_page_escapes_every_field(store):
    """content was escaped but category/id were interpolated raw — and no inline onclick remains."""
    c = await _client()
    try:
        html = await (await c.get("/memory/review")).text()
        assert "escapeHtml(f.content)" in html
        assert "escapeHtml(f.category)" in html
        assert "escapeHtml(f.id)" in html
        assert 'onclick="act(' not in html  # replaced by delegated data-act handling
        assert "encodeURIComponent(id)" in html
    finally:
        await c.close()
