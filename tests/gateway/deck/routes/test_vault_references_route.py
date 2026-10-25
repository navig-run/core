"""GET /api/deck/vault/references — the OS Vault app's "References" section.

The report is metadata only (files, keys, references, codes), the root comes only from
the project registry, and the route answers only this computer. Runs against a real
``Vault(tmp_path)`` patched in — never the developer's vault.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes.vault import handle_deck_vault_references

pytestmark = pytest.mark.integration

ROUTE = "/api/deck/vault/references"
SECRET = "tok-route-" + "z" * 24
PLAIN = "sk-proj-" + "R" * 40


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "shop"
    (root / "api").mkdir(parents=True)
    (root / ".env").write_text(f"TOKEN=vault://ci/token\nGONE=vault://nope/x\nOPENAI_API_KEY={PLAIN}\n",
                               encoding="utf-8")
    (root / "api" / "config.yaml").write_text("db:\n  password: vault://ci/token\n", encoding="utf-8")
    return root


@pytest.fixture
def wired(tmp_path, project):
    from navig_vault.core import Vault

    from navig.projects.model import ProjectNotFound

    v = Vault(tmp_path / "v")
    v.put("ci/token", json.dumps({"value": SECRET}).encode())

    def resolve(pid):
        if pid != "shop":
            raise ProjectNotFound(f"unknown project: {pid}")
        return "shop", project

    with patch("navig.gateway.deck.routes.vault._get_vault", return_value=v), \
            patch("navig.projects.model.resolve_project", side_effect=resolve):
        yield v
    v._store.close()


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get(ROUTE, handle_deck_vault_references)
    return app


async def _get(query: str, headers=None):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(f"{ROUTE}{query}", headers=headers or {})
        return resp.status, await resp.json()


async def test_a_registered_project_gets_a_metadata_only_report(wired):
    status, body = await _get("?project=shop")
    assert status == 200, body
    codes = {(f["file"], f.get("key"), f["code"]) for f in body["findings"]}
    assert (".env", "TOKEN", "RESOLVED") in codes
    assert (".env", "GONE", "UNRESOLVED") in codes
    assert (".env", "OPENAI_API_KEY", "PLAINTEXT") in codes
    assert ("api/config.yaml", "db.password", "RESOLVED") in codes, "subdirectories are checked"
    assert body["project"] == "shop" and body["checked_at"] and body["summary"]["resolved"] == 2
    raw = json.dumps(body)
    assert SECRET not in raw and PLAIN not in raw, "a value reached the response"


async def test_an_unknown_project_is_404(wired):
    status, _ = await _get("?project=nope")
    assert status == 404


async def test_a_missing_project_is_400(wired):
    status, _ = await _get("")
    assert status == 400


@pytest.mark.parametrize("headers", [
    {"X-Navig-Via": "lighthouse"},
    {"CF-Ray": "8a1b2c3d4e5f-AMS"},
    {"X-Forwarded-For": "203.0.113.7"},  # the OS app on another machine (NEURON → outpost)
])
async def test_a_remote_client_gets_the_same_metadata_only_report(wired, headers):
    # Deck auth (middleware) is the gate, as for the vault list; the route itself must
    # not lock out the OS app reaching the brain from another machine, and must still
    # return no value whoever asks.
    status, body = await _get("?project=shop", headers)
    assert status == 200 and body["findings"]
    assert SECRET not in json.dumps(body) and PLAIN not in json.dumps(body)


async def test_without_a_deck_credential_the_middleware_refuses_it(wired, monkeypatch):
    # The route has no local-only check of its own, so deck auth IS its gate: prove a
    # remote caller with no credential is stopped before the handler runs.
    from navig.gateway.deck import auth as deck_auth

    monkeypatch.setattr(deck_auth, "trusted_local_desktop", lambda request: False)
    monkeypatch.setattr(deck_auth, "_local_desktop_bypass", lambda request: False)
    monkeypatch.setitem(deck_auth._deck_config, "api_key", "the-deck-key")
    monkeypatch.setitem(deck_auth._deck_config, "telegram_only", False)
    monkeypatch.setitem(deck_auth._deck_config, "dev_mode", False)
    app = web.Application(middlewares=[deck_auth.deck_auth_middleware])
    app.router.add_get(ROUTE, handle_deck_vault_references)
    async with TestClient(TestServer(app)) as client:
        denied = await client.get(f"{ROUTE}?project=shop", headers={"X-Forwarded-For": "203.0.113.7"})
        assert denied.status == 401 and "findings" not in await denied.json()
        allowed = await client.get(f"{ROUTE}?project=shop", headers={
            "X-Forwarded-For": "203.0.113.7", "Authorization": "Bearer the-deck-key"})
        assert allowed.status == 200, await allowed.text()


async def test_a_path_instead_of_a_project_id_reaches_nothing(wired):
    status, _ = await _get("?project=C:/Windows")
    assert status == 404
