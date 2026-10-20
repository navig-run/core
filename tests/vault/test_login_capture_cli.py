"""`navig vault login capture` — the SSO capture orchestration, browser faked.

The command's job is to sequence: open a persistent profile → let a human sign
in → export the session → infer the IdP → store both records. The browser is
faked at the `navig_browser` seam; everything else (host normalisation, IdP
inference, the record-keeping contract) runs for real, including:

  * session saved under the SAME account label as the login record;
  * `add_sso_login` refusing (ValueError) downgrades to "kept password login"
    instead of crashing — the session is still an upgrade.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

pytest.importorskip("navig_browser")

from navig_browser import cdp_actions, profiles  # noqa: E402

from navig.commands.vault import vault_app  # noqa: E402

runner = CliRunner()


class _Bridge:
    def __init__(self, state, shared):
        self._state = state
        self._shared = shared

    async def export_storage_state(self):
        return self._state

    async def get_url(self):
        # "" → the origin guard is skipped (page unreadable); a real URL must be
        # on the target's registrable domain or nothing is saved.
        return self._shared.get("page_url", "")


def _fake_browser(monkeypatch, state, *, signed_in=True):
    # detect_api_ref reads the vault's provider index — patch it so tests never
    # touch the real vault; set calls["api_ref_result"] to simulate a hit.
    calls = {"save_session": [], "add_sso_login": [], "page_url": "",
             "detect_api_ref": [], "api_ref_result": None}

    monkeypatch.setattr(profiles, "get_profile", lambda name: None)
    monkeypatch.setattr(cdp_actions, "profile_new", lambda name, **kw: {"ok": True, "name": name})
    monkeypatch.setattr(
        cdp_actions, "profile_open", lambda name, **kw: {"ok": True, "port": 9333, "reused": False}
    )

    async def _noop(*a, **k):
        return {}

    monkeypatch.setattr(cdp_actions, "navigate", _noop)
    monkeypatch.setattr(cdp_actions, "login", _noop)

    monkeypatch.setattr(
        "navig_browser.cdp_runtime.run", lambda coro, **kw: asyncio.run(coro)
    )

    class _Manager:
        async def get(self, port, tab):
            return _Bridge(state, calls)

    monkeypatch.setattr(
        "navig_browser.session_manager.get_session_manager", lambda: _Manager()
    )

    async def _is_logged_in(_bridge):
        return signed_in

    monkeypatch.setattr("navig_browser.autofill._is_logged_in", _is_logged_in)

    import navig_vault.sessions as vault_sessions

    def _save(domain, storage_state, *, username=None, vault=None, **kw):
        calls["save_session"].append({"domain": domain, "username": username, "state": storage_state})
        return "session-id"

    def _add(domain, username=None, *, idp=None, idp_subject=None, url=None,
             api_ref=None, vault=None, **kw):
        calls["add_sso_login"].append(
            {"domain": domain, "username": username, "idp": idp,
             "idp_subject": idp_subject, "url": url, "api_ref": api_ref}
        )
        return "login-id"

    def _detect(host, **_kw):
        calls["detect_api_ref"].append(host)
        return calls["api_ref_result"]

    # save_session is looked up on the module at call time; add_sso_login was
    # bound into the navig.vault package namespace at import time — patch the
    # attribute the command's `from navig.vault import …` actually reads.
    monkeypatch.setattr(vault_sessions, "save_session", _save)
    monkeypatch.setattr("navig.vault.add_sso_login", _add)
    monkeypatch.setattr("navig.vault.detect_api_ref", _detect)
    return calls


def _json_out(res) -> dict:
    """The command prints info/prompt lines before the JSON — parse from the brace."""
    return json.loads(res.output[res.output.index("{"):])


def test_capture_saves_session_and_infers_idp(monkeypatch):
    state = {"cookies": [{"host": "accounts.google.com", "name": "SID"}], "origins": []}
    calls = _fake_browser(monkeypatch, state)

    res = runner.invoke(
        vault_app,
        ["login", "capture", "https://Notion.so/app",
         "--idp-user", "you@gmail.com", "--json"],
        input="\n",
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["ok"] is True
    assert out["domain"] == "notion.so"          # host normalised
    assert out["idp"] == "google"                # INFERRED from the session cookies
    assert out["verified"] is True
    assert out["login_recorded"] is True
    assert out["profile"] == "navig-login-notion.so"

    # session + login share one account label (idp_user defaults the account)
    assert calls["save_session"][0]["domain"] == "notion.so"
    assert calls["save_session"][0]["username"] == "you@gmail.com"
    assert calls["add_sso_login"][0]["username"] == "you@gmail.com"
    assert calls["add_sso_login"][0]["idp"] == "google"
    assert calls["add_sso_login"][0]["idp_subject"] == "you@gmail.com"


def test_capture_only_opens_the_signin_page_when_not_signed_in(monkeypatch):
    state = {"cookies": [], "origins": []}
    calls = _fake_browser(monkeypatch, state, signed_in=False)

    res = runner.invoke(
        vault_app,
        ["login", "capture", "example.com", "--capture-only", "--json"],
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out == {"ok": False, "opened": True, "domain": "example.com"}
    assert calls["save_session"] == []            # nothing saved on phase 1
    assert calls["add_sso_login"] == []


def test_capture_only_saves_when_signed_in(monkeypatch):
    state = {"cookies": [{"host": "accounts.google.com", "name": "SID"}], "origins": []}
    calls = _fake_browser(monkeypatch, state, signed_in=True)

    res = runner.invoke(
        vault_app,
        ["login", "capture", "notion.so", "--capture-only", "--json"],
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["ok"] is True and out["verified"] is True
    assert out["idp"] == "google"
    assert len(calls["save_session"]) == 1


def test_capture_only_refuses_a_page_on_the_wrong_domain(monkeypatch):
    """Confirm pressed (or a probe run) while parked on the IdP must save nothing —
    accounts.google.com cookies under a notion.so label is a credential leak."""
    state = {"cookies": [{"host": "accounts.google.com", "name": "SID"}], "origins": []}
    calls = _fake_browser(monkeypatch, state, signed_in=True)
    calls["page_url"] = "https://accounts.google.com/signin"

    res = runner.invoke(
        vault_app,
        ["login", "capture", "notion.so", "--capture-only", "--json"],
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out == {"ok": False, "opened": True, "domain": "notion.so"}
    assert calls["save_session"] == []


def test_interactive_capture_refuses_the_wrong_domain_with_exit_1(monkeypatch):
    state = {"cookies": [], "origins": []}
    calls = _fake_browser(monkeypatch, state, signed_in=True)
    calls["page_url"] = "https://accounts.google.com/signin"

    res = runner.invoke(
        vault_app,
        ["login", "capture", "notion.so", "--json"],
        input="\n",
    )
    assert res.exit_code == 1, res.output
    assert calls["save_session"] == []
    assert "nothing was saved" in res.output


def test_capture_links_a_detected_api_credential(monkeypatch):
    state = {"cookies": [], "origins": []}
    calls = _fake_browser(monkeypatch, state)
    calls["api_ref_result"] = "github"

    res = runner.invoke(
        vault_app,
        ["login", "capture", "github.com", "-u", "you", "--capture-only", "--json"],
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["api_ref"] == "github"
    assert calls["detect_api_ref"] == ["github.com"]
    assert calls["add_sso_login"][0]["api_ref"] == "github"


def test_capture_without_a_matching_credential_links_nothing(monkeypatch):
    state = {"cookies": [], "origins": []}
    calls = _fake_browser(monkeypatch, state)  # api_ref_result stays None

    res = runner.invoke(
        vault_app,
        ["login", "capture", "example.com", "--capture-only", "--json"],
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["api_ref"] is None
    assert calls["add_sso_login"][0]["api_ref"] is None


def test_capture_keeps_an_existing_password_login(monkeypatch):
    state = {"cookies": [], "origins": []}
    calls = _fake_browser(monkeypatch, state)

    def _refuse(*a, **k):
        raise ValueError("a password login already exists")

    monkeypatch.setattr("navig.vault.add_sso_login", _refuse)

    res = runner.invoke(
        vault_app,
        ["login", "capture", "example.com", "-u", "you", "--idp", "GitHub", "--json"],
        input="\n",
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["login_recorded"] is False        # refused, not crashed
    assert out["idp"] == "github"                # --idp wins over inference
    assert out["ok"] is True
    assert len(calls["save_session"]) == 1       # the session still landed
