"""Style dials, the web-search switch, Fast answers and the personalization route.

Each switch on the OS Personalization page must change what the agent actually does;
these tests pin the mechanism at the point where it takes effect.
"""

from __future__ import annotations

import pytest

from navig.agent.conv import sources, style


class _FakeCM:
    """In-memory stand-in for ConfigManager: get / get_live / set_global."""

    def __init__(self, data: dict | None = None):
        self.data: dict = data or {}
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    get_live = get

    def set_global(self, key, value):
        self.writes.append((key, value))
        node = self.data
        *head, last = key.split(".")
        for part in head:
            node = node.setdefault(part, {})
        node[last] = value


@pytest.fixture
def cfg(monkeypatch):
    cm = _FakeCM()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: cm)
    return cm


class TestStyle:
    def test_defaults_add_nothing(self, cfg):
        assert style.read_style() == ()

    def test_invalid_levels_read_as_default(self, cfg):
        cfg.data["style"] = {"emoji": "LOTS", "warmth": None, "visual": "more"}
        assert style.read_style() == (("visual", "more"),)

    def test_rules_land_in_how_to_talk(self):
        from navig.agent.conv.soul import SoulLoader

        loader = SoulLoader()
        plain = dict(loader.system_prompt_sections("I am NAVIG."))
        styled = dict(
            loader.system_prompt_sections(
                "I am NAVIG.", style=(("emoji", "less"), ("headers_lists", "more"))
            )
        )
        assert "Never use emoji." in styled["## How to Talk"]
        assert "headers and bullet lists" in styled["## How to Talk"]
        assert "Never use emoji." not in plain["## How to Talk"]

    def test_default_style_is_byte_identical(self):
        from navig.agent.conv.soul import SoulLoader

        loader = SoulLoader()
        assert loader.build_system_prompt("I am NAVIG.", "", "") == loader.build_system_prompt(
            "I am NAVIG.", "", "", style=()
        )

    def test_minimal_prompt_honours_no_emoji(self):
        from navig.agent.conv.soul import SoulLoader

        assert "Never use emoji." in SoulLoader().build_minimal_prompt(
            style=(("emoji", "less"),)
        )


class TestWebSearchMode:
    @pytest.mark.parametrize("raw,mode", [(None, "auto"), ("ALWAYS", "always"), ("x", "auto")])
    def test_mode_normalizes(self, cfg, raw, mode):
        if raw is not None:
            cfg.data["ai"] = {"web_search": {"mode": raw}}
        assert sources.web_search_mode() == mode

    def test_always_adds_search(self):
        assert sources.apply_web_search_mode(["core"], "always") == ["core", "search"]

    def test_off_removes_web_toolsets_and_mixed_web_tools(self):
        assert sources.apply_web_search_mode(["core", "search", "browser"], "off") == ["core"]
        schemas = [
            {"function": {"name": n}} for n in ("bash_exec", "search", "web_fetch", "wiki_search")
        ]
        kept = [s["function"]["name"] for s in sources.drop_web_tools(schemas, "off")]
        assert kept == ["bash_exec", "wiki_search"]

    def test_auto_changes_nothing(self):
        assert sources.apply_web_search_mode(["core", "search"], "auto") == ["core", "search"]
        schemas = [{"function": {"name": "search"}}]
        assert sources.drop_web_tools(schemas, "auto") == schemas


class TestFastAnswersAndSources:
    def test_fast_answers_default_off_and_string_false(self, cfg):
        assert sources.fast_answers_enabled() is False
        cfg.data["ai"] = {"fast_answers": "false"}
        assert sources.fast_answers_enabled() is False
        cfg.data["ai"] = {"fast_answers": True}
        assert sources.fast_answers_enabled() is True

    def test_sources_default_on(self, cfg):
        assert sources.source_enabled("library") is True
        cfg.data["ai"] = {"sources": {"connectors": "off"}}
        assert sources.source_enabled("connectors") is False

    def test_simple_classification_is_used(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "navig.agent.routing_strategy.classify_request",
            lambda msgs, **kw: SimpleNamespace(tier="SIMPLE"),
        )
        assert sources.is_simple_request("what's the capital of Peru?") is True


class TestConfigGetLive:
    def test_get_live_rereads_when_the_file_changed(self, monkeypatch):
        from navig.config import ConfigManager

        cm = ConfigManager.__new__(ConfigManager)
        cm._global_config_loaded = True
        cm._global_config_mtime_ns = 1
        refreshed: list[int] = []
        monkeypatch.setattr(cm, "_global_config_file_mtime", lambda: 2, raising=False)
        monkeypatch.setattr(
            cm, "refresh_global_config", lambda: refreshed.append(1) or {}, raising=False
        )
        monkeypatch.setattr(cm, "get", lambda key, default=None: "fresh", raising=False)
        assert cm.get_live("memory.enabled") == "fresh"
        assert refreshed == [1]

    def test_get_live_skips_the_reread_when_unchanged(self, monkeypatch):
        from navig.config import ConfigManager

        cm = ConfigManager.__new__(ConfigManager)
        cm._global_config_loaded = True
        cm._global_config_mtime_ns = 5
        monkeypatch.setattr(cm, "_global_config_file_mtime", lambda: 5, raising=False)
        monkeypatch.setattr(
            cm, "refresh_global_config", lambda: pytest.fail("re-read"), raising=False
        )
        monkeypatch.setattr(cm, "get", lambda key, default=None: "cached", raising=False)
        assert cm.get_live("x") == "cached"


@pytest.mark.integration
class TestPersonalizationRoute:
    async def _client(self):
        pytest.importorskip("aiohttp")
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from navig.gateway.deck.routes import personalization as p

        app = web.Application()
        app.router.add_get("/api/deck/personalization", p.handle_personalization_get)
        app.router.add_post("/api/deck/personalization", p.handle_personalization_post)
        c = TestClient(TestServer(app))
        await c.start_server()
        return c

    async def test_get_reports_defaults(self, cfg):
        c = await self._client()
        try:
            body = await (await c.get("/api/deck/personalization")).json()
        finally:
            await c.close()
        assert body["memory"] == {"enabled": True}
        assert body["style"]["emoji"] == "default"
        assert body["web_search"] == {"mode": "auto"}
        assert body["fast_answers"] is False
        assert body["sources"] == {"library": True, "connectors": True}

    async def test_post_writes_each_leaf_and_reads_back(self, cfg):
        c = await self._client()
        try:
            r = await c.post(
                "/api/deck/personalization",
                json={
                    "memory": {"enabled": False},
                    "style": {"emoji": "less"},
                    "web_search": {"mode": "off"},
                    "fast_answers": True,
                    "sources": {"connectors": False},
                },
            )
            body = await r.json()
        finally:
            await c.close()
        assert r.status == 200
        assert ("memory.enabled", False) in cfg.writes
        assert ("style.emoji", "less") in cfg.writes
        assert body["web_search"]["mode"] == "off" and body["fast_answers"] is True
        assert body["sources"]["connectors"] is False and body["memory"]["enabled"] is False

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"nope": 1},
            {"memory": {"enabled": "false"}},
            {"style": {"emoji": "lots"}},
            {"style": {"sparkle": "more"}},
            {"web_search": {"mode": "sometimes"}},
            {"fast_answers": "yes"},
            {"sources": {"drive": True}},
            # one bad key poisons the whole request: nothing is written
            {"fast_answers": True, "style": {"emoji": "lots"}},
        ],
    )
    async def test_post_rejects_bad_input_and_writes_nothing(self, cfg, bad):
        c = await self._client()
        try:
            r = await c.post("/api/deck/personalization", json=bad)
        finally:
            await c.close()
        assert r.status == 400
        assert cfg.writes == []


def test_the_agent_loop_applies_every_switch():
    """Wiring guard: the helpers above are only worth something if the turn calls them."""
    import ast
    from pathlib import Path

    import navig.agent.conv.agent as agent_mod

    tree = ast.parse(Path(agent_mod.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agentic_turn"
    )
    called = {
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
    }
    for needed in ("apply_web_search_mode", "drop_web_tools", "web_search_mode",
                   "fast_answers_enabled", "is_simple_request"):
        assert needed in called, f"_run_agentic_turn no longer calls {needed}"
    prompt_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_build_system_prompt"
    )
    assert "read_style" in {
        n.func.id for n in ast.walk(prompt_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }

class TestPersonalizationContext:
    """GET /api/deck/personalization/context — what NAVIG OS chat injects."""

    def _patch(self, monkeypatch, *, memory_on=True, user_md="Answer briefly.", recall="- Likes tea"):
        from types import SimpleNamespace

        cm = _FakeCM({"style": {"emoji": "less"}, "memory": {"enabled": memory_on}})
        monkeypatch.setattr("navig.config.get_config_manager", lambda: cm)
        monkeypatch.setattr(
            "navig.workspace.WorkspaceManager",
            lambda: SimpleNamespace(get_file_content=lambda name: user_md),
        )
        calls: list[str] = []

        class _Retriever:
            def __init__(self, store):
                pass

            def retrieve(self, query, max_tokens=400):
                calls.append(query)
                return SimpleNamespace(formatted=recall)

        monkeypatch.setattr("navig.memory.fact_retriever.FactRetriever", _Retriever)
        monkeypatch.setattr("navig.memory.key_facts.get_key_fact_store", lambda: object())
        return calls

    def test_system_carries_style_and_instructions_memory_carries_recall(self, monkeypatch):
        from navig.gateway.deck.routes.personalization import _context

        calls = self._patch(monkeypatch)
        out = _context("what do I drink?", incognito=False)
        assert "Never use emoji." in out["system"]
        assert "Answer briefly." in out["system"]
        assert out["memory"].startswith("## What I remember") and "Likes tea" in out["memory"]
        assert calls == ["what do I drink?"]
        assert out["system_hash"] == _context("other", incognito=False)["system_hash"]

    def test_incognito_and_memory_off_recall_nothing(self, monkeypatch):
        from navig.gateway.deck.routes.personalization import _context

        calls = self._patch(monkeypatch)
        assert _context("q", incognito=True)["memory"] == ""
        self._patch(monkeypatch, memory_on="false")
        off = _context("q", incognito=False)
        assert off["memory"] == "" and off["memory_enabled"] is False
        assert calls == []

    def test_nothing_configured_gives_empty_strings(self, monkeypatch):
        from navig.gateway.deck.routes.personalization import _context

        self._patch(monkeypatch, user_md="", recall="")
        cm = _FakeCM()
        monkeypatch.setattr("navig.config.get_config_manager", lambda: cm)
        out = _context("q", incognito=False)
        assert out["system"] == "" and out["memory"] == ""