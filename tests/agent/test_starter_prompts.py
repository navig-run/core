"""Chat starters come from the user's sources — and stay quiet when there are none."""

from __future__ import annotations

import pytest

from navig.agent.proactive import starters


@pytest.fixture
def sources(monkeypatch):
    state = {"pending": 0, "connected": []}
    monkeypatch.setattr(starters, "_pending_memory_count", lambda: state["pending"])
    monkeypatch.setattr(starters, "_connected_ids", lambda: list(state["connected"]))
    return state


def test_nothing_connected_means_no_starters(sources):
    assert starters.build_starters() == []


def test_pending_memories_lead_and_link_to_personalization(sources):
    sources.update(pending=2, connected=["gmail"])
    out = starters.build_starters()
    assert out[0]["id"] == "memory-review" and out[0]["action"] == "settings:personalization"
    assert out[0]["label"] == "Review 2 things NAVIG learned" and out[0]["count"] == 2
    assert out[1]["id"] == "connector-gmail" and out[1]["prompt"]


def test_capped_and_in_priority_order(sources):
    sources.update(connected=["youtube", "notion", "slack", "github", "gmail", "google_calendar"])
    out = starters.build_starters()
    assert len(out) == starters.MAX_STARTERS
    assert [s["source"] for s in out] == ["gmail", "google_calendar", "github", "slack"]


def test_unknown_connectors_are_ignored(sources):
    sources.update(connected=["supabase", "gcp_translate"])
    assert starters.build_starters() == []


@pytest.mark.integration
async def test_route_caches_and_fresh_bypasses(sources, monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import starters as route

    monkeypatch.setattr(route, "_cache", {"at": 0.0, "data": None})
    app = web.Application()
    app.router.add_get("/s", route.handle_starters)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        assert (await (await c.get("/s")).json())["starters"] == []
        sources.update(connected=["gmail"])
        assert (await (await c.get("/s")).json())["starters"] == []  # cached
        fresh = (await (await c.get("/s?fresh=1")).json())["starters"]
        assert [s["id"] for s in fresh] == ["connector-gmail"]
    finally:
        await c.close()
