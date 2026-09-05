"""/api/deck/context + /api/deck/context/files — served off the event loop.

The overview and file-list handlers read the memory bank via synchronous SQLite
(``mgr.list_files()`` / ``mgr.get_stats()``) and format a potentially large index. Those now run
in ``asyncio.to_thread`` so a big memory bank can't stall the gateway (the same off-loop treatment
handle_deck_spaces got). MemoryStorage keeps a per-thread connection (threading.local,
check_same_thread=False, WAL), so a worker-thread read is safe. Driving the handlers through a real
TestServer here exercises that thread hop end-to-end.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_FILES = [
    {"file_path": "notes/a.md", "chunk_count": 3, "total_tokens": 100,
     "indexed_at": "2026-01-03", "file_hash": "aaaa1111bbbbcccc"},
    {"file_path": "src/b.py", "chunk_count": 5, "total_tokens": 200,
     "indexed_at": "2026-01-05", "file_hash": "cccc2222ddddeeee"},
    {"file_path": "cfg/c.yaml", "chunk_count": 1, "total_tokens": 50,
     "indexed_at": "2026-01-01", "file_hash": "eeee3333ffff0000"},
]
_STATS = {
    "file_count": 3, "chunk_count": 9, "total_tokens": 350,
    "database_size_mb": 1.5, "embedded_chunks": 9, "embeddings_enabled": True,
    "embedding_model": "all-MiniLM-L6-v2", "memory_dir": "/x/mem",
}


class _FakeMgr:
    def list_files(self):
        return [dict(f) for f in _FILES]

    def get_stats(self):
        return dict(_STATS)


def _app(mgr):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import context as context_mod

    app = web.Application()
    app.router.add_get("/context", context_mod.handle_deck_context)
    app.router.add_get("/context/files", context_mod.handle_deck_context_files)
    return app, context_mod


async def _client(mgr, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    app, context_mod = _app(mgr)
    monkeypatch.setattr(context_mod, "_get_manager", lambda: mgr)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_context_overview_when_no_manager(monkeypatch):
    client = await _client(None, monkeypatch)
    try:
        r = await client.get("/context")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["ready"] is False
        assert data["file_count"] == 0 and data["recent"] == []
    finally:
        await client.close()


async def test_context_overview_aggregates_and_sorts(monkeypatch):
    client = await _client(_FakeMgr(), monkeypatch)
    try:
        r = await client.get("/context")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["ready"] is True
        assert data["file_count"] == 3
        assert data["chunk_count"] == 9
        assert data["total_tokens"] == 350
        # one file per source bucket
        assert data["by_source"] == {"notes": 1, "code": 1, "config": 1}
        # recent sorted by indexed_at DESC
        assert [f["path"] for f in data["recent"]] == ["src/b.py", "notes/a.md", "cfg/c.yaml"]
        assert data["stats"]["database_size_mb"] == 1.5
        assert data["stats"]["embeddings_enabled"] is True
        assert "ts" in data
        # file_hash is truncated to 12 chars by _format_file
        assert all(len(f["file_hash"]) <= 12 for f in data["recent"])
    finally:
        await client.close()


async def test_context_files_pagination(monkeypatch):
    client = await _client(_FakeMgr(), monkeypatch)
    try:
        r = await client.get("/context/files?offset=0&limit=2")
        data = (await r.json())["data"]
        assert data["total"] == 3
        assert len(data["files"]) == 2
        assert data["next_offset"] == 2
        # last page
        r2 = await client.get("/context/files?offset=2&limit=2")
        data2 = (await r2.json())["data"]
        assert len(data2["files"]) == 1
        assert data2["next_offset"] is None
    finally:
        await client.close()


async def test_context_files_source_and_search_filter(monkeypatch):
    client = await _client(_FakeMgr(), monkeypatch)
    try:
        r = await client.get("/context/files?source=code")
        data = (await r.json())["data"]
        assert data["total"] == 1
        assert data["files"][0]["path"] == "src/b.py"

        r2 = await client.get("/context/files?q=c.yaml")
        data2 = (await r2.json())["data"]
        assert data2["total"] == 1
        assert data2["files"][0]["path"] == "cfg/c.yaml"
    finally:
        await client.close()


async def test_context_files_bad_params_is_400(monkeypatch):
    client = await _client(_FakeMgr(), monkeypatch)
    try:
        r = await client.get("/context/files?offset=abc")
        assert r.status == 400
        body = await r.json()
        assert body["ok"] is False
    finally:
        await client.close()


async def test_context_files_when_no_manager(monkeypatch):
    client = await _client(None, monkeypatch)
    try:
        r = await client.get("/context/files")
        data = (await r.json())["data"]
        assert data == {"files": [], "total": 0, "next_offset": None}
    finally:
        await client.close()
