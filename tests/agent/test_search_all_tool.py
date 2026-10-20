"""search_all — one search across the user's sources, honest about every source.

Each source is stubbed; the tests pin the contract the model relies on: merged,
labelled rows; a status for EVERY source (answered, skipped-with-reason, timed out,
failed); the Personalization switches and memory/incognito gates.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.tools import search_all_tool as sa
from navig.memory import controls


class _Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    get_live = get


@pytest.fixture
def cfg(monkeypatch):
    c = _Cfg({})
    monkeypatch.setattr("navig.config.get_config_manager", lambda: c)
    return c


def _stub(rows, status="ok"):
    async def fn(query, limit):
        return [dict(r) for r in rows], status

    return fn


@pytest.fixture
def stubs(monkeypatch):
    monkeypatch.setattr(sa, "_connectors", _stub([{"source": "connector:gmail", "title": "Invoice #12", "snippet": "", "ref": "m1"}]))
    monkeypatch.setattr(sa, "_wiki", _stub([{"source": "wiki", "title": "Invoicing", "snippet": "", "ref": "w"}]))
    monkeypatch.setattr(sa, "_cabinet", _stub([], "locked — unlock the Cabinet to include it"))
    monkeypatch.setattr(sa, "_past_chats", _stub([{"source": "past-chat", "title": "user", "snippet": "invoice", "ref": "s"}]))
    monkeypatch.setattr(sa, "_telegram", _stub([]))
    monkeypatch.setattr(sa, "_recall", _stub([], "not installed"))
    monkeypatch.setattr(sa, "_web", _stub([{"source": "web", "title": "w", "snippet": "", "ref": "u"}]))


def _run(**kw):
    return asyncio.run(sa.run_search("invoice", **kw))


def test_merges_sources_and_reports_every_one(cfg, stubs):
    out = _run()
    assert {r["source"] for r in out["results"]} == {"connector:gmail", "wiki", "past-chat"}
    assert out["sources"]["cabinet"].startswith("locked")
    assert out["sources"]["recall"] == "not installed"
    assert out["sources"]["wiki"].startswith("ok")
    assert "web" not in out["sources"]  # not requested


def test_web_only_when_asked_and_not_switched_off(cfg, stubs):
    assert any(r["source"] == "web" for r in _run(include_web=True)["results"])
    cfg.data = {"ai": {"web_search": {"mode": "off"}}}
    out = _run(include_web=True)
    assert out["sources"]["web"].startswith("skipped")
    assert not any(r["source"] == "web" for r in out["results"])


def test_personalization_switches_skip_sources(cfg, stubs):
    cfg.data = {"ai": {"sources": {"connectors": False, "library": "false"}}}
    out = _run()
    assert out["results"] == []
    assert out["sources"]["connectors"].startswith("skipped")
    assert out["sources"]["wiki"] == "skipped: library off in Personalization"


def test_memory_off_and_incognito_skip_past_conversations(cfg, stubs):
    cfg.data = {"memory": {"enabled": False}}
    out = _run()
    assert out["sources"]["past-chats"] == "skipped: memory off"
    assert not any(r["source"] == "past-chat" for r in out["results"])
    cfg.data = {}

    async def _incog():
        with controls.incognito_scope(True):
            return await sa.run_search("invoice")

    out = asyncio.run(_incog())
    assert out["sources"]["telegram"] == "skipped: incognito chat"
    assert out["sources"]["wiki"].startswith("ok")  # the library itself is not memory


def test_a_slow_or_broken_source_is_reported_not_dropped(cfg, stubs, monkeypatch):
    async def slow(query, limit):
        await asyncio.sleep(5)
        return [], "ok"

    async def broken(query, limit):
        raise RuntimeError("boom")

    monkeypatch.setattr(sa, "SOURCE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(sa, "_wiki", slow)
    monkeypatch.setattr(sa, "_telegram", broken)
    out = _run()
    assert out["sources"]["wiki"].startswith("timeout")
    assert out["sources"]["telegram"] == "error: RuntimeError"
    assert any(r["source"] == "connector:gmail" for r in out["results"])


def test_tool_validates_query_and_clamps_limit(cfg, stubs):
    tool = sa.SearchAllTool()
    assert asyncio.run(tool.run({"query": "  "})).success is False
    res = asyncio.run(tool.run({"query": "invoice", "limit": 999}))
    assert res.success is True and res.output["count"] >= 1


def test_cabinet_source_maps_locked_and_missing(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("navig_cabinet.search")
    state = {"out": {"status": "locked", "items": []}}
    fake.search = lambda q, limit: state["out"]
    monkeypatch.setitem(sys.modules, "navig_cabinet.search", fake)
    monkeypatch.setitem(sys.modules, "navig_cabinet", types.ModuleType("navig_cabinet"))

    class _Reg:
        def is_enabled(self, name):
            return True

    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Reg())
    assert asyncio.run(sa._cabinet("x", 5))[1].startswith("locked")
    state["out"] = {"status": "missing", "items": []}
    assert asyncio.run(sa._cabinet("x", 5))[1] == "no cabinet yet"
    state["out"] = {"status": "ok", "items": [{"id": "c1", "title": "Passport", "snippet": "exp 2031"}]}
    rows, status = asyncio.run(sa._cabinet("x", 5))
    assert status == "ok" and rows[0]["ref"] == "cabinet:c1"


def test_recall_source_needs_an_index_then_maps_records(tmp_path, monkeypatch):
    """_recall feeds the model, so its read MUST go through query.search(for_model=True) —
    the privacy filter every MCP recall tool applies — never the raw store (which returns
    records backed by chats that may not leave the machine)."""
    db = tmp_path / "index.db"
    monkeypatch.setattr("navig.recall.paths.index_db", lambda: db)
    assert asyncio.run(sa._recall("x", 5)) == ([], "not enabled (navig recall enable)")
    db.write_bytes(b"")

    seen: dict = {}

    def fake_search(q, *, scope=None, rtype=None, status=None, raw=False, limit=15,
                    for_model=False, store=None):
        seen.update(q=q, limit=limit, for_model=for_model)
        return {
            "records": [
                {"id": "r1", "type": "decision", "title": "Use Postgres", "snip": "chose [Postgres]", "updated": "2026-10-01"}
            ],
            "turns": [],
        }

    monkeypatch.setattr("navig.recall.query.search", fake_search)
    rows, status = asyncio.run(sa._recall("postgres", 5))
    assert status == "ok"
    assert seen == {"q": "postgres", "limit": 5, "for_model": True}
    assert rows == [
        {"source": "recall:decision", "title": "Use Postgres", "snippet": "chose [Postgres]", "ref": "recall:r1", "ts": "2026-10-01"}
    ]