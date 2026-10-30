"""A webflow ``secret`` param filled from the NAVIG vault — schema rules and the release route.

The route is the only way a decrypted value leaves the vault for a webflow, so every refusal
is a test: wrong caller, a param the stored flow does not tie to the vault, a page outside the
flow's sites, plain http, another domain, a missing login. Real router, real vault (temp dir).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck import register_deck_routes
from navig.webflows import SCHEMA, validate_webflow

SITE = "https://shop.com"
EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
PASSWORD = "vault-pw-123"


def flow(**param_extra) -> dict:
    return {
        "schema": SCHEMA, "name": "Vault flow", "origins": [SITE], "start_url": f"{SITE}/login",
        "params": [{"name": "pw", "type": "secret", **param_extra}],
        "steps": [{"action": "fill", "css": "#pw", "value": "{{pw}}", "sensitive": True}],
    }


# ── schema ───────────────────────────────────────────────────────────────────


def test_a_secret_param_may_name_a_vault_login_of_its_own_site():
    v = validate_webflow(flow(vault={"domain": "Shop.Com", "username": "ada"}))
    assert v.ok, v.errors
    assert v.doc["params"][0]["vault"] == {"domain": "shop.com", "username": "ada"}
    sub = validate_webflow(flow(vault={"domain": "accounts.shop.com", "username": "ada"}))
    assert sub.ok, "a subdomain of the flow's site binds (same registrable domain)"


def test_a_vault_login_for_another_site_is_refused():
    v = validate_webflow(flow(vault={"domain": "bank.example", "username": "ada"}))
    assert not v.ok and "does not belong to this flow's sites" in v.errors[0]


def test_only_a_secret_param_can_come_from_the_vault():
    doc = flow(vault={"domain": "shop.com", "username": "ada"})
    doc["params"][0]["type"] = "string"
    doc["steps"][0].pop("sensitive")
    v = validate_webflow(doc)
    assert not v.ok and "only a secret param" in " ".join(v.errors)


def test_a_vault_ref_never_carries_a_value():
    v = validate_webflow(flow(vault={"domain": "shop.com", "username": "ada", "password": PASSWORD}))
    assert v.ok and PASSWORD not in str(v.doc)


# ── the release route ────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import navig_vault.core as core

    from navig.vault import WebLogin, add_login
    from navig.vault.core import Vault

    v = Vault(vault_dir=tmp_path / "vault")
    monkeypatch.setattr(core, "_vault", v)  # the route resolves through get_vault()
    add_login(WebLogin(domain="shop.com", username="ada", password=PASSWORD, url=f"{SITE}/login"), vault=v)
    yield v
    v.store().close()


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vault) -> web.Application:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    a = web.Application()
    register_deck_routes(a, require_auth=False)
    return a


async def _client(app) -> TestClient:
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def _saved(c, **param_extra) -> str:
    r = await c.post("/api/deck/webflows", json=flow(**param_extra))
    assert r.status == 200, await r.text()
    return (await r.json())["webflow"]["id"]


async def test_the_dock_gets_the_password_for_its_fill_step_and_the_read_is_audited(app, vault):
    c = await _client(app)
    try:
        wf = await _saved(c, vault={"domain": "shop.com", "username": "ada"})
        r = await c.post(f"/api/deck/webflows/{wf}/secret", json={"param": "pw", "page_url": f"{SITE}/login"}, headers=EXT)
        assert r.status == 200
        assert r.headers["Cache-Control"] == "no-store"
        assert (await r.json())["value"] == PASSWORD
        stored = (await (await c.get(f"/api/deck/webflows/{wf}")).json())["webflow"]
        assert PASSWORD not in str(stored), "the flow keeps only the reference"
    finally:
        await c.close()
    actors = {e.get("actor") for e in vault.get_audit_log(limit=20) if isinstance(e, dict)} | {
        getattr(e, "actor", None) for e in vault.get_audit_log(limit=20) if not isinstance(e, dict)
    }
    assert f"dock:webflow:{wf}" in actors


@pytest.mark.parametrize(
    ("headers", "body", "status", "category"),
    [
        ({}, {"param": "pw", "page_url": f"{SITE}/login"}, 403, "denied"),                         # no Origin: an agent / the OS app
        ({"Origin": "https://shop.com"}, {"param": "pw", "page_url": f"{SITE}/login"}, 401, "denied"),  # a website: the deck guard
        (EXT, {"param": "pw", "page_url": "http://shop.com/login"}, 403, "denied"),             # plain http
        (EXT, {"param": "pw", "page_url": "https://evil.example/login"}, 403, "denied"),            # outside the flow
        (EXT, {"param": "nope", "page_url": f"{SITE}/login"}, 403, "denied"),                       # not a vault param
    ],
)
async def test_every_other_request_is_refused_and_releases_nothing(app, headers, body, status, category):
    c = await _client(app)
    try:
        wf = await _saved(c, vault={"domain": "shop.com", "username": "ada"})
        r = await c.post(f"/api/deck/webflows/{wf}/secret", json=body, headers=headers)
        data = await r.json()
        assert r.status == status
        # A website Origin is refused even earlier, by the deck's own origin guard (no category).
        assert data.get("category", category) == category
        assert PASSWORD not in str(data)
    finally:
        await c.close()


async def test_a_secret_without_a_vault_ref_is_never_released(app):
    c = await _client(app)
    try:
        wf = await _saved(c)  # typed at each run
        r = await c.post(f"/api/deck/webflows/{wf}/secret", json={"param": "pw", "page_url": f"{SITE}/login"}, headers=EXT)
        assert r.status == 403
    finally:
        await c.close()


async def test_a_missing_login_is_no_credential(app):
    c = await _client(app)
    try:
        wf = await _saved(c, vault={"domain": "shop.com", "username": "grace"})
        r = await c.post(f"/api/deck/webflows/{wf}/secret", json={"param": "pw", "page_url": f"{SITE}/login"}, headers=EXT)
        data = await r.json()
        assert r.status == 404 and data["category"] == "no_credential"
        assert "navig vault login add shop.com -u grace" in data["error"]
    finally:
        await c.close()


# ── the headless path (a goal / a terminal): the same rules, in-process ─────────


async def test_the_headless_provider_releases_only_on_an_https_page_of_the_flow(vault):
    from navig.browser.webflow_runner import StepFailed
    from navig.webflows import validate_webflow
    from navig.webflows.headless import secret_provider

    doc = {**validate_webflow(flow(vault={"domain": "shop.com", "username": "ada"})).doc, "id": "wf_vault-flow"}
    provide = secret_provider(doc, actor="goal")
    assert await provide("pw", f"{SITE}/login") == PASSWORD
    for url, why in [("http://shop.com/login", "denied"), ("https://evil.com/login", "denied")]:
        with pytest.raises(StepFailed) as exc:
            await provide("pw", url)
        assert exc.value.category == why and PASSWORD not in str(exc.value)
    missing = {**doc, "params": [{**doc["params"][0], "vault": {"domain": "shop.com", "username": "grace"}}]}
    with pytest.raises(StepFailed) as exc:
        await secret_provider(missing, actor="goal")("pw", f"{SITE}/login")
    assert exc.value.category == "no_credential"
    actors = {e.get("actor") for e in vault.get_audit_log(limit=20)}
    assert "goal:webflow:wf_vault-flow" in actors


def test_a_vault_secret_is_only_deferred_with_allow_vault():
    from navig.webflows import WebflowError, validate_webflow
    from navig.webflows.headless import resolve_run_values

    doc = validate_webflow(flow(vault={"domain": "shop.com", "username": "ada"})).doc
    with pytest.raises(WebflowError):
        resolve_run_values(doc, {}, allow_vault=False)
    assert resolve_run_values(doc, {}, allow_vault=True) == ({}, {"pw"})
