"""Contract tests for the web-search control plane (routes/search.py).

    GET  /api/deck/search/status  — engine, key presence (booleans only), cascade
    POST /api/deck/search/config  — validate BEFORE write; answers read-back
    POST /api/deck/search/test    — live probe; reports the answering engine
    POST /api/deck/search         — Pi agent proxy; kill switch → 403

Config writes land in an isolated NAVIG_CONFIG_DIR; vault lookups are stubbed so
has_key is deterministic and no real key material is ever touched.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes.search import (
    handle_deck_search,
    handle_deck_search_config,
    handle_deck_search_status,
    handle_deck_search_test,
)

STATUS = "/api/deck/search/status"
CONFIG = "/api/deck/search/config"
TEST = "/api/deck/search/test"
PROXY = "/api/deck/search"


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get(STATUS, handle_deck_search_status)
    app.router.add_post(CONFIG, handle_deck_search_config)
    app.router.add_post(TEST, handle_deck_search_test)
    app.router.add_post(PROXY, handle_deck_search)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Fresh config dir + stubbed empty vault — deterministic key presence."""
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NAVIG_WEB_SEARCH_PROVIDER", raising=False)
    reset_config_manager()
    ConfigSingleton._instance = None

    class _EmptyVault:
        def get_secret(self, label: str) -> str:
            return ""

    monkeypatch.setattr("navig.vault.core.get_vault", lambda: _EmptyVault())
    monkeypatch.setattr("navig.vault.vault_exists", lambda: True)

    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


def _fake_search(success: bool = True, provider: str = "deepseek_search", **capture):
    """Stand-in for web_search capturing the kwargs dispatch actually received."""
    from navig.tools.web import SearchResult, WebSearchResult

    def _run(query=None, *, count=5, use_cache=True, **kwargs):
        capture["query"] = query
        capture["count"] = count
        capture["use_cache"] = use_cache
        if not success:
            return WebSearchResult(
                success=False, query=query, provider=provider, error="upstream boom"
            )
        return WebSearchResult(
            success=True,
            query=query,
            provider=provider,
            results=[SearchResult(title="T", url="https://u.example", snippet="S", age=None)],
        )

    return _run, capture


async def test_status_shape_and_no_key_material(isolated, monkeypatch):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get(STATUS)
        assert res.status == 200
        body = await res.json()

    assert body["ok"] is True
    data = body["data"]
    assert isinstance(data["enabled"], bool)
    assert data["provider"] == "auto"  # fresh config default
    ids = [p["id"] for p in data["providers"]]
    assert ids[0] == "deepseek_search"
    assert {"tavily", "brave", "serpapi", "mojeek", "duckduckgo", "firecrawl"} <= set(ids)
    for entry in data["providers"]:
        assert set(entry) == {"id", "label", "has_key"}
        assert isinstance(entry["has_key"], bool)
    assert data["cascade"][0] == "deepseek_search"
    # Stubbed vault is empty → nothing keyed.
    assert all(not p["has_key"] for p in data["providers"])


async def test_config_write_validates_then_writes_then_reads_back(isolated):
    from navig.config import get_config_manager

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(CONFIG, json={"provider": "deepseek_search"})
        assert res.status == 200
        body = await res.json()

    assert body["ok"] is True
    assert body["data"]["provider"] == "deepseek_search"
    assert get_config_manager().get("web.search.provider") == "deepseek_search"


async def test_config_alias_is_canonicalized_on_write(isolated):
    from navig.config import get_config_manager

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(CONFIG, json={"provider": "deepseek"})
        assert res.status == 200
        body = await res.json()

    assert body["data"]["provider"] == "deepseek_search"
    assert get_config_manager().get("web.search.provider") == "deepseek_search"


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "nonesuch"},       # unknown engine
        {"provider": ""},               # empty
        {"provider": 42},               # not a string
        {"wrong_field": "deepseek_search"},
        {},                             # nothing to set
        [],
    ],
)
async def test_config_rejects_bad_input_and_writes_nothing(isolated, body):
    from navig.config import get_config_manager

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(CONFIG, json=body)
        assert res.status == 400
        payload = await res.json()
        assert payload["ok"] is False
        assert payload["error"]

    assert get_config_manager().get("web.search.provider") is None


async def test_test_route_reports_answering_engine_with_cache_off(isolated, monkeypatch):
    _run, capture = _fake_search(provider="tavily")
    monkeypatch.setattr("navig.tools.web.web_search", _run)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(TEST, json={})
        assert res.status == 200
        body = await res.json()

    data = body["data"]
    assert data["ok"] is True
    assert data["provider_used"] == "tavily"   # who ACTUALLY answered
    assert data["result_count"] == 1
    assert data["latency_ms"] >= 0
    assert capture["use_cache"] is False        # latency must be real, not cached


async def test_test_route_disabled_reports_result_not_error(isolated, monkeypatch):
    """Kill switch off: HTTP 200 with ok:false so the button renders the reason.
    The string 'false' is exactly what `navig config set web.search.enabled false` stores."""
    from navig.config import get_config_manager

    get_config_manager().set_global("web.search.enabled", "false")
    _run, capture = _fake_search()
    monkeypatch.setattr("navig.tools.web.web_search", _run)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(TEST, json={})
        assert res.status == 200
        body = await res.json()

    assert body["data"]["ok"] is False
    assert "disabled" in body["data"]["error"]
    assert "query" not in capture  # no search attempted


async def test_proxy_success_envelope_carries_provider(isolated, monkeypatch):
    _run, capture = _fake_search(provider="deepseek_search")
    monkeypatch.setattr("navig.tools.web.web_search", _run)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(PROXY, json={"query": "what now", "count": 50})
        assert res.status == 200
        body = await res.json()

    data = body["data"]
    assert data["provider"] == "deepseek_search"
    assert data["query"] == "what now"
    assert data["results"] and data["results"][0]["url"] == "https://u.example"
    assert capture["count"] == 10          # clamped to the proxy contract (1-10)
    assert capture["use_cache"] is False


async def test_proxy_kill_switch_returns_403(isolated, monkeypatch):
    from navig.config import get_config_manager

    get_config_manager().set_global("web.search.enabled", "false")
    _run, capture = _fake_search()
    monkeypatch.setattr("navig.tools.web.web_search", _run)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(PROXY, json={"query": "hi"})
        assert res.status == 403
        body = await res.json()
        assert body["ok"] is False
        assert "disabled" in body["error"]
    assert "query" not in capture


@pytest.mark.parametrize(
    "body",
    [{"query": ""}, {"query": None}, {"noquery": True}, {}, "not-a-dict"],
)
async def test_proxy_requires_a_query(isolated, body):
    async with TestClient(TestServer(_app())) as client:
        res = await client.post(PROXY, json=body)
        assert res.status == 400
        payload = await res.json()
        assert payload["ok"] is False


async def test_proxy_upstream_failure_is_502_with_the_real_error(isolated, monkeypatch):
    _run, _cap = _fake_search(success=False)
    monkeypatch.setattr("navig.tools.web.web_search", _run)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post(PROXY, json={"query": "q"})
        assert res.status == 502
        body = await res.json()
        assert body["ok"] is False
        assert "upstream boom" in body["error"]
