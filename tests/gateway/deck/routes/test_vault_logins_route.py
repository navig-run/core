"""Website-login routes: GET /vault/logins, POST /vault/logins/{capture,remove}.

Capture is the two-phase SSO flow the NAVIG OS Vault app drives: phase 1 opens
the sign-in page in a persistent profile ({ok:false, opened:true}), the human
signs in there, phase 2 stores session + IdP linkage ({ok:true}). The browser
work runs in a SUBPROCESS off the daemon loop — these tests mock the spawn and
pin the argv, the lock, and the response mapping.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes import vault as vault_routes
from navig.gateway.deck.routes.vault import (
    _login_json_from_output,
    handle_deck_vault_login_capture,
    handle_deck_vault_login_remove,
    handle_deck_vault_logins_list,
)

ROUTE_LIST = "/api/deck/vault/logins"
ROUTE_CAPTURE = "/api/deck/vault/logins/capture"
ROUTE_REMOVE = "/api/deck/vault/logins/remove"


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get(ROUTE_LIST, handle_deck_vault_logins_list)
    app.router.add_post(ROUTE_CAPTURE, handle_deck_vault_login_capture)
    app.router.add_post(ROUTE_REMOVE, handle_deck_vault_login_remove)
    return app


async def _post(path: str, body: dict | None = None):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(path, json=body if body is not None else {})
        return resp.status, await resp.json()


class _FakeProc:
    def __init__(self, out: bytes, rc: int = 0, err: bytes = b""):
        self._out = out
        self._err = err
        self.returncode = rc
        self.pid = 4242
        self.killed = False

    async def communicate(self):
        return (self._out, self._err)

    def kill(self):
        self.killed = True


def _mock_exec(monkeypatch, proc):
    captured: dict = {"args": ()}

    async def _fake(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)
    return captured


# ── list ─────────────────────────────────────────────────────────────────────


async def test_list_returns_non_secret_summaries_with_signin_method(monkeypatch):
    from navig.vault.logins import LoginInfo

    infos = [
        LoginInfo(domain="github.com", username="you", label="web/github.com/you",
                  id="1", auth_method="password", api_ref="github"),
        LoginInfo(domain="notion.so", username="you@gmail.com",
                  label="web/notion.so/you@gmail.com", id="2",
                  auth_method="sso", idp="google", api_ref=None),
    ]
    monkeypatch.setattr("navig.vault.list_logins", lambda: infos)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(ROUTE_LIST)
        data = await resp.json()
    assert resp.status == 200
    assert [l["domain"] for l in data["logins"]] == ["github.com", "notion.so"]
    assert data["logins"][0]["auth_method"] == "password"
    assert data["logins"][1]["auth_method"] == "sso" and data["logins"][1]["idp"] == "google"
    # the linked-API pointer rides along (a provider id, never a secret)
    assert data["logins"][0]["api_ref"] == "github"
    assert data["logins"][1]["api_ref"] is None
    # never leaks secrets: LoginInfo has no password field, and none appears here
    assert "password" not in data["logins"][1]


# ── capture ──────────────────────────────────────────────────────────────────


def test_capture_requires_a_domain():
    status, body = asyncio.run(_post(ROUTE_CAPTURE, {}))
    assert status == 400 and body["ok"] is False


def test_capture_phase1_opens_the_signin_page(monkeypatch):
    proc = _FakeProc(json.dumps({"ok": False, "opened": True, "domain": "example.com"}).encode())
    cap = _mock_exec(monkeypatch, proc)
    status, body = asyncio.run(_post(ROUTE_CAPTURE, {"domain": "https://Example.com/login"}))
    assert status == 200
    assert body["ok"] is False and body["opened"] is True and body["domain"] == "example.com"
    argv = cap["args"]
    assert "navig" in argv[0] and "capture" in argv
    assert "--capture-only" in argv and "--json" in argv
    assert "https://Example.com/login" in argv
    # lock released for the next phase
    assert vault_routes._capture_lock["running"] is False


def test_capture_phase2_maps_the_saved_result(monkeypatch):
    payload = {"ok": True, "domain": "notion.so", "username": "you@gmail.com",
               "idp": "google", "verified": True, "login_recorded": True}
    _mock_exec(monkeypatch, _FakeProc(json.dumps(payload).encode()))
    status, body = asyncio.run(
        _post(ROUTE_CAPTURE, {"domain": "notion.so", "idp_user": "you@gmail.com"})
    )
    assert status == 200
    assert body["ok"] is True and body["login_recorded"] is True
    assert body["idp"] == "google" and body["verified"] is True


def test_capture_passes_optional_flags_through(monkeypatch):
    cap = _mock_exec(monkeypatch, _FakeProc(b'{"ok": false, "opened": true}'))
    asyncio.run(
        _post(ROUTE_CAPTURE, {
            "domain": "x.com", "username": "u", "idp": "github",
            "idp_user": "u@x.dev", "url": "https://x.com/login",
        })
    )
    argv = list(cap["args"])
    for flag in ("--username", "--idp", "--idp-user", "--url"):
        assert flag in argv


def test_capture_409_while_another_capture_runs(monkeypatch):
    vault_routes._capture_lock["running"] = True
    try:
        status, body = asyncio.run(_post(ROUTE_CAPTURE, {"domain": "x.com"}))
    finally:
        vault_routes._capture_lock["running"] = False
    assert status == 409 and body["ok"] is False


def test_capture_unparseable_output_is_a_500_not_a_success(monkeypatch):
    _mock_exec(monkeypatch, _FakeProc(b"", err=b"boom: navig missing"))
    status, body = asyncio.run(_post(ROUTE_CAPTURE, {"domain": "x.com"}))
    assert status == 500 and body["ok"] is False
    assert "boom" in body["error"] or "capture failed" in body["error"]


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove_deletes_login_and_session(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr("navig.vault.remove_login", lambda d, u, **k: calls.setdefault("login", (d, u)) or True)
    monkeypatch.setattr(
        "navig.vault.sessions.remove_session", lambda d, u, **k: calls.setdefault("session", (d, u)) or True
    )
    status, body = asyncio.run(_post(ROUTE_REMOVE, {"domain": "github.com", "username": "you"}))
    assert status == 200 and body["ok"] is True and body["removed"] is True
    assert calls["login"] == ("github.com", "you")
    assert calls["session"] == ("github.com", "you")


def test_remove_requires_domain_and_username():
    status, body = asyncio.run(_post(ROUTE_REMOVE, {"domain": "x.com"}))
    assert status == 400 and body["ok"] is False


# ── output parsing ───────────────────────────────────────────────────────────


def test_json_from_output_plain_and_wrapped():
    assert _login_json_from_output(b'{"a": 1}')["a"] == 1
    assert _login_json_from_output(b'log\n{\n  "a": 2\n}\ntail')["a"] == 2
    assert _login_json_from_output(b"") is None
    assert _login_json_from_output(b"no json here") is None
