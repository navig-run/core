"""Contract tests for GET/POST /api/deck/llm/fallbacks (Settings → Fallbacks).

Validation happens BEFORE any write (a bad body changes nothing), and a good
write answers with the status read back from disk — the page renders what was
stored, not what it sent.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes.llm_fallbacks import (
    handle_deck_llm_fallbacks_get,
    handle_deck_llm_fallbacks_post,
)
from navig.llm import fallback_policy as fp
from navig.llm import model_fallback as mf
from navig.llm import model_health as mh

NEMO = "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b"
PATH = "/api/deck/llm/fallbacks"


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get(PATH, handle_deck_llm_fallbacks_get)
    app.router.add_post(PATH, handle_deck_llm_fallbacks_post)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    import navig.llm.router as router_mod
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(router_mod, "_router_instance", None)
    monkeypatch.setattr("navig.llm.router._check_ollama_models", lambda *a, **k: {})
    monkeypatch.setattr("navig.providers.inference.list_provider_connections", lambda p: [])
    mh.reset_for_tests()
    mf.reset_for_tests()
    mf._sink = lambda *a: None
    fp.reset_cooldowns()
    yield tmp_path
    mh.reset_for_tests()
    mf.reset_for_tests()
    reset_config_manager()
    ConfigSingleton._instance = None


async def _post(client, body):
    res = await client.post(PATH, json=body)
    return res.status, await res.json()


async def test_get_shape(isolated):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.note_fallback("big_tasks", "anthropic:claude-opus-4-8", NEMO, "auth")
    async with TestClient(TestServer(_app())) as client:
        res = await client.get(PATH)
        assert res.status == 200
        body = await res.json()
    assert body["ok"] is True
    modes = {m["mode"]: m for m in body["modes"]}
    assert set(modes) == {"big_tasks", "coding", "research", "small_talk", "summarize"}
    for m in modes.values():
        assert {"primary", "health", "configured_fallback", "effective_fallback", "never"} <= set(m)
    assert body["policy"] == {"auto": True, "prefer": [], "never_from": [], "never_to": []}
    assert "anthropic:claude-opus-4-8" in body["degraded"]
    assert body["history"][0]["to"] == NEMO
    assert [c["spec"] for c in body["candidates"]] == [NEMO]


@pytest.mark.parametrize("body", [
    {"policy": {"auto": "yes"}},                       # not a bool
    {"policy": {"never_from": ["codng"]}},             # unknown mode
    {"policy": {"prefer": ["no-colon"]}},              # not provider:model
    {"policy": {"never_to": ["bad provider!"]}},
    {"policy": {"surprise": 1}},                       # unknown field
    {"mode": "codng", "fallback": NEMO},
    {"mode": "big_tasks", "fallback": "nocolon"},
    {"mode": "big_tasks", "never": "true"},
    {"mode": "big_tasks"},
    {"fallback": NEMO},                                # mode missing
    {},
    [],
])
async def test_post_rejects_bad_input_and_writes_nothing(isolated, body):
    async with TestClient(TestServer(_app())) as client:
        res = await client.post(PATH, json=body)
        assert res.status == 400
        assert (await res.json())["ok"] is False
    assert mf.load_policy() == mf.FallbackPolicy()
    from navig.config import get_config_manager

    assert get_config_manager().get("llm_router.llm_modes") is None


async def test_post_policy_and_mode_read_back(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, {"policy": {"auto": False, "prefer": [NEMO],
                                                       "never_to": ["groq"]}})
        assert status == 200 and body["ok"] is True
        assert body["policy"] == {"auto": False, "prefer": [NEMO], "never_from": [],
                                  "never_to": ["groq"]}

        status, body = await _post(client, {"mode": "coding", "never": True})
        assert status == 200
        assert body["policy"]["never_from"] == ["coding"]
        assert {m["mode"]: m for m in body["modes"]}["coding"]["never"] is True

        status, body = await _post(client, {"mode": "big_tasks", "fallback": NEMO})
        assert status == 200
        big = {m["mode"]: m for m in body["modes"]}["big_tasks"]
        assert big["configured_fallback"] == NEMO

        status, body = await _post(client, {"mode": "big_tasks", "fallback": ""})
        assert status == 200
        big = {m["mode"]: m for m in body["modes"]}["big_tasks"]
        assert big["configured_fallback"] is None

    # Survives a full in-memory drop (the daemon-restart proof).
    from navig.config import get_config_manager, reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    reset_config_manager()
    ConfigSingleton._instance = None
    pol = mf.load_policy()
    assert pol.auto is False and pol.never_from == ["coding"] and pol.never_to == ["groq"]
    assert get_config_manager().get("llm_router.llm_modes")["big_tasks"]["fallback_model"] == ""
