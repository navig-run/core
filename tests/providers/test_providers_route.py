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
    yield
    conns_mod._STORE = None
    conns_mod._STORE_PATH = None


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
