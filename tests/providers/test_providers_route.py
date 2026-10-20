"""
Deck route tests for /api/deck/providers — exercises the handlers against the
real connection store (isolated via NAVIG_DATA_DIR). Uses keyless local templates
so no vault master-key setup is needed.
"""

from __future__ import annotations

import json

import pytest

import navig.providers.connections as conns_mod
from navig.gateway.deck.routes import providers as route


class FakeRequest:
    def __init__(self, *, query=None, body=None, match_info=None):
        self.query = query or {}
        self._body = body
        self.match_info = match_info or {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _json(resp):
    return resp.status, json.loads(resp.body)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    # reset the process-wide singleton so the env override takes effect
    conns_mod._STORE = None
    conns_mod._STORE_PATH = None
    from navig.providers.connect import clear_catalog_cache

    clear_catalog_cache()  # live catalogs are cached process-wide
    yield
    conns_mod._STORE = None
    conns_mod._STORE_PATH = None
    clear_catalog_cache()


async def test_list_returns_templates_and_empty_connections():
    status, data = _json(await route.handle_deck_providers_list(FakeRequest()))
    assert status == 200
    template_ids = {t["template_id"] for t in data["templates"]}
    assert {"openai-api", "ollama", "openai-compat"} <= template_ids
    assert data["connections"] == []
    assert data["default_connection_id"] is None


async def test_connect_local_then_list_default_delete():
    # connect a keyless local (ollama endpoint down → needs_reauth, but persisted)
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    assert status == 200
    conn = data["connection"]
    assert conn["template_id"] == "ollama"
    assert conn["secret_ref"] is None  # keyless → no secret stored
    cid = conn["connection_id"]

    # list now shows it as the default
    _, listed = _json(await route.handle_deck_providers_list(FakeRequest()))
    assert listed["default_connection_id"] == cid

    # set default (idempotent) ok
    status, _ = _json(await route.handle_deck_providers_default(
        FakeRequest(body={"connection_id": cid})
    ))
    assert status == 200

    # delete (safe-remove)
    status, out = _json(await route.handle_deck_providers_delete(
        FakeRequest(match_info={"connection_id": cid})
    ))
    assert status == 200 and out["ok"] is True
    _, after = _json(await route.handle_deck_providers_list(FakeRequest()))
    assert after["connections"] == []


async def test_connect_missing_template_id_is_400():
    status, data = _json(await route.handle_deck_providers_connect(FakeRequest(body={})))
    assert status == 400 and data["code"] == "bad_request"


async def test_connect_unknown_template_is_400():
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "nope"})
    ))
    assert status == 400 and data["code"] == "validation_error"


async def test_default_missing_connection_is_404():
    status, data = _json(await route.handle_deck_providers_default(
        FakeRequest(body={"connection_id": "ghost"})
    ))
    assert status == 404 and data["code"] == "not_found"


async def test_oauth_start_returns_handle_and_url():
    status, data = _json(await route.handle_deck_providers_oauth_start(
        FakeRequest(body={"template_id": "claude-max"})
    ))
    assert status == 200
    assert data["handle"] and data["auth_url"].startswith("https://claude.ai/oauth/authorize")
    # The pending flow is persisted in the store (NAVIG_DATA_DIR is isolated here),
    # single-use and pruned past expiry — no module-level state to clean up.


async def test_oauth_start_rejects_non_oauth_template():
    status, data = _json(await route.handle_deck_providers_oauth_start(
        FakeRequest(body={"template_id": "openai-api"})
    ))
    assert status == 400 and data["code"] == "validation_error"


async def test_oauth_complete_requires_handle_and_code():
    status, data = _json(await route.handle_deck_providers_oauth_complete(
        FakeRequest(body={"handle": "h"})
    ))
    assert status == 400 and data["code"] == "bad_request"


async def test_oauth_complete_unknown_handle_is_400():
    status, data = _json(await route.handle_deck_providers_oauth_complete(
        FakeRequest(body={"handle": "ghost", "code": "x"})
    ))
    assert status == 400 and data["code"] == "validation_error"


# ── GET /api/deck/providers/models — the model picker's live catalog ─────────

async def test_models_unknown_connection_is_404():
    status, data = _json(await route.handle_deck_providers_models(
        FakeRequest(query={"connection_id": "ghost"})
    ))
    assert status == 404


async def test_models_requires_an_id_when_no_default():
    status, data = _json(await route.handle_deck_providers_models(FakeRequest()))
    assert status == 400


async def test_models_defaults_to_the_default_connection():
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    assert status == 200
    cid = data["connection"]["connection_id"]

    status, out = _json(await route.handle_deck_providers_models(FakeRequest()))
    assert status == 200
    assert out["connection_id"] == cid
    assert isinstance(out["models"], list)


async def test_list_connection_models_merge_fallback_and_capability(monkeypatch):
    """Stored ids first (default stays first), live ids appended, deduped —
    and a driver that cannot discover (or throws) keeps the stored list whole."""
    import navig.providers.connect as connect_mod
    from navig.providers.connection_types import Capability
    from navig.providers.drivers.base import ModelInfo, ValidationResult

    class FakeDrv:
        def __init__(self, *, caps, live=None, boom=False):
            self.advertised_capabilities = caps
            self._live = live or []
            self._boom = boom

        async def validate(self, *, secret_ref, endpoint=None, model=None):
            return ValidationResult(
                ok=True, health="healthy",
                models=[ModelInfo(id="stored-1"), ModelInfo(id="stored-2")],
            )

        async def list_models(self, *, secret_ref, endpoint=None):
            if self._boom:
                raise RuntimeError("catalog unreachable")
            return [ModelInfo(id=m) for m in self._live]

    caps = {Capability.AUTH, Capability.INFERENCE, Capability.MODEL_DISCOVERY}

    # connect through a fake driver so the stored record carries models
    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: FakeDrv(caps=caps))
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    assert status == 200
    cid = data["connection"]["connection_id"]

    # live catalog merges behind the stored ids
    monkeypatch.setattr(
        connect_mod, "get_driver",
        lambda t, s: FakeDrv(caps=caps, live=["stored-1", "live-3", "live-4"]),
    )
    models = await connect_mod.list_connection_models(cid, refresh=True)
    assert models == ["stored-1", "stored-2", "live-3", "live-4"]

    # a failing catalog never empties the picker
    monkeypatch.setattr(
        connect_mod, "get_driver", lambda t, s: FakeDrv(caps=caps, boom=True),
    )
    assert await connect_mod.list_connection_models(cid, refresh=True) == ["stored-1", "stored-2"]

    # no MODEL_DISCOVERY capability → stored list, no live call attempted
    monkeypatch.setattr(
        connect_mod, "get_driver", lambda t, s: FakeDrv(caps={Capability.AUTH}),
    )
    assert await connect_mod.list_connection_models(cid, refresh=True) == ["stored-1", "stored-2"]


async def test_list_connection_models_drops_non_chat_and_sorts_discovered(monkeypatch):
    """Live catalogs list TTS/embedding/image ids; those never reach a chat picker.
    Discovered ids are sorted; stored ids keep their order and are never filtered."""
    import navig.providers.connect as connect_mod
    from navig.providers.connection_types import Capability
    from navig.providers.drivers.base import ModelInfo, ValidationResult

    caps = {Capability.AUTH, Capability.INFERENCE, Capability.MODEL_DISCOVERY}

    class FakeDrv:
        advertised_capabilities = caps

        def __init__(self, live):
            self._live = live

        async def validate(self, *, secret_ref, endpoint=None, model=None):
            # stored list deliberately holds an odd id: a user's explicit pick survives
            return ValidationResult(ok=True, health="healthy",
                                    models=[ModelInfo(id="zeta-chat"), ModelInfo(id="tts-1")])

        async def list_models(self, *, secret_ref, endpoint=None):
            return [ModelInfo(id=m) for m in self._live]

    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: FakeDrv([]))
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    assert status == 200
    cid = data["connection"]["connection_id"]

    live = ["whisper-1", "Beta-chat", "text-embedding-3-small", "alpha-chat", "gpt-image-1", "zeta-chat"]
    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: FakeDrv(live))
    models = await connect_mod.list_connection_models(cid, refresh=True)
    assert models == ["zeta-chat", "tts-1", "alpha-chat", "Beta-chat"]


def _catalog_fake_driver(*, live, boom=False, calls=None):
    from navig.providers.connection_types import Capability
    from navig.providers.drivers.base import ModelInfo, ValidationResult

    class FakeDrv:
        advertised_capabilities = {Capability.AUTH, Capability.INFERENCE, Capability.MODEL_DISCOVERY}

        async def validate(self, *, secret_ref, endpoint=None, model=None):
            return ValidationResult(ok=True, health="healthy",
                                    models=[ModelInfo(id="claude-opus-4-8"), ModelInfo(id="claude-haiku-4-5")])

        async def list_models(self, *, secret_ref, endpoint=None):
            if calls is not None:
                calls["n"] += 1
            if boom:
                raise RuntimeError("catalog down")
            return [ModelInfo(id=m) for m in live]

    return FakeDrv()


async def test_catalog_is_cached_refresh_bypasses_and_failures_are_not_cached(monkeypatch):
    """A provider's /v1/models costs 0.2-2.4 s and pickers ask on every switch:
    one live call per TTL, refresh=1 forces a new one, a failed call is retried."""
    import navig.providers.connect as connect_mod

    calls = {"n": 0}
    live = ["claude-sonnet-4-6", "tts-1"]
    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: _catalog_fake_driver(live=live, calls=calls))
    status, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    assert status == 200
    cid = data["connection"]["connection_id"]
    calls["n"] = 0

    first = await connect_mod.connection_catalog(cid)
    second = await connect_mod.connection_catalog(cid)
    assert calls["n"] == 1 and first["cached"] is False and second["cached"] is True
    assert second["models"] == ["claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"]

    await connect_mod.connection_catalog(cid, refresh=True)
    assert calls["n"] == 2

    connect_mod.clear_catalog_cache()
    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: _catalog_fake_driver(live=live, boom=True))
    failed = await connect_mod.connection_catalog(cid)
    assert failed["models"] == ["claude-opus-4-8", "claude-haiku-4-5"] and failed["cached"] is False
    assert cid not in connect_mod._LIVE_CATALOG, "a failure must not be cached"


async def test_models_route_returns_tags_and_intent(monkeypatch):
    import navig.providers.connect as connect_mod

    monkeypatch.setattr(connect_mod, "get_driver", lambda t, s: _catalog_fake_driver(live=["claude-sonnet-4-6"]))
    _, data = _json(await route.handle_deck_providers_connect(
        FakeRequest(body={"template_id": "ollama", "name": "Local"})
    ))
    cid = data["connection"]["connection_id"]

    status, out = _json(await route.handle_deck_providers_models(
        FakeRequest(query={"connection_id": cid, "refresh": "1"})
    ))
    assert status == 200
    assert out["intent"] == {"ask": "claude-haiku-4-5", "act": "claude-opus-4-8"}
    assert "fast" in out["tags"]["claude-haiku-4-5"]
    assert "reasoning" in out["tags"]["claude-opus-4-8"]
    assert "text" not in out["tags"]["claude-opus-4-8"], "text is implied for every model"
    assert out["cached"] is False
