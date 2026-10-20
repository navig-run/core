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


# ── capture --from-chrome / discover ─────────────────────────────────────────


def _fake_import(monkeypatch, per_profile: dict[str, list[dict]], discovered=()):
    """Stub the real-browser seams: discovery and the per-profile cookie capture."""
    calls = _fake_browser(monkeypatch, {"cookies": [], "origins": []})
    calls["captured_profiles"] = []

    async def _capture(app, host, headless=True, profile="Default"):
        calls["captured_profiles"].append(profile)
        return per_profile.get(profile, [])

    class _Site:
        def __init__(self, domain, profile):
            self.domain, self.profile, self.saved = domain, profile, False
            self.auth_cookie_names = ["session"]

    monkeypatch.setattr(
        "navig_browser.system_chrome.capture_existing_cookies", _capture
    )
    monkeypatch.setattr(
        "navig_browser.signed_in_sites.discover_signed_in_sites",
        lambda *a, **k: [_Site(d, p) for d, p in discovered],
    )
    return calls


def test_from_chrome_imports_only_the_sites_own_cookies(monkeypatch):
    cookies = [
        {"name": "user_session", "value": "x", "domain": ".github.com", "path": "/"},
        {"name": "_octo", "value": "y", "domain": "github.com", "path": "/"},
        # a look-alike whose name merely CONTAINS the site — must be dropped
        {"name": "sess", "value": "z", "domain": ".evilgithub.com", "path": "/"},
    ]
    calls = _fake_import(
        monkeypatch, {"Profile 4": cookies}, discovered=[("github.com", "Profile 4")]
    )
    res = runner.invoke(
        vault_app, ["login", "capture", "https://github.com/x", "--from-chrome", "--json"]
    )
    assert res.exit_code == 0, res.output
    out = _json_out(res)
    assert out["ok"] is True and out["domain"] == "github.com"
    assert out["source"] == "chrome:Profile 4" and out["cookies"] == 2
    assert out["verified"] is None               # imported, never claimed verified
    saved = calls["save_session"][0]
    assert {c["domain"] for c in saved["state"]["cookies"]} == {".github.com", "github.com"}
    # the profile discovery pointed at is tried first; Default only as a fallback
    assert calls["captured_profiles"] == ["Profile 4"]


def test_from_chrome_falls_back_to_default_profile(monkeypatch):
    calls = _fake_import(
        monkeypatch,
        {"Default": [{"name": "sid", "value": "v", "domain": ".notion.so", "path": "/"}]},
    )
    res = runner.invoke(vault_app, ["login", "capture", "notion.so", "--from-chrome", "--json"])
    assert res.exit_code == 0, res.output
    assert _json_out(res)["source"] == "chrome:Default"
    assert calls["captured_profiles"] == ["Default"]


def test_from_chrome_with_no_signin_fails_loudly_and_saves_nothing(monkeypatch):
    calls = _fake_import(monkeypatch, {})
    res = runner.invoke(vault_app, ["login", "capture", "example.com", "--from-chrome", "--json"])
    assert res.exit_code == 1, res.output
    out = _json_out(res)
    assert out["ok"] is False and "navig vault login capture example.com" in out["error"]
    assert calls["save_session"] == [] and calls["add_sso_login"] == []


def test_from_chrome_explicit_profile_is_the_only_one_tried(monkeypatch):
    calls = _fake_import(monkeypatch, {}, discovered=[("x.com", "Default")])
    res = runner.invoke(
        vault_app,
        ["login", "capture", "x.com", "--from-chrome", "--profile", "Profile 7", "--json"],
    )
    assert res.exit_code == 1
    assert calls["captured_profiles"] == ["Profile 7"]


def test_discover_lists_unsaved_sites_with_the_next_step(monkeypatch):
    class _Site:
        def __init__(self, domain, profile, saved):
            self.domain, self.profile, self.saved = domain, profile, saved
            self.auth_cookie_names = ["session", "token"]

    monkeypatch.setattr(
        "navig_browser.signed_in_sites.discover_signed_in_sites",
        lambda *a, **k: [_Site("notion.so", "Profile 4", False), _Site("github.com", "Default", True)],
    )
    res = runner.invoke(vault_app, ["login", "discover", "--json"])
    assert res.exit_code == 0, res.output
    rows = json.loads(res.output[res.output.index("["):])
    assert [r["domain"] for r in rows] == ["notion.so"]   # saved hidden by default
    assert rows[0] == {"domain": "notion.so", "profile": "Profile 4", "saved": False, "signals": 2}

    res_all = runner.invoke(vault_app, ["login", "discover", "--all", "--json"])
    rows_all = json.loads(res_all.output[res_all.output.index("["):])
    assert {r["domain"] for r in rows_all} == {"notion.so", "github.com"}

    table = runner.invoke(vault_app, ["login", "discover"])
    assert table.exit_code == 0, table.output
    assert "--from-chrome" in table.output and "heuristic" in table.output


def test_json_output_survives_a_narrow_terminal(monkeypatch):
    """`--json` must parse even when a value is longer than the terminal is wide.

    Measured live: `navig vault login discover --json` piped at 80 columns broke a long
    value with a newline INSIDE the JSON string — Rich wraps at the console width. The
    capture button in NAVIG OS parses this output, so one long account name broke it.
    """
    monkeypatch.setenv("COLUMNS", "40")

    class _Site:
        domain = "a-very-long-subdomain-name-that-wraps.example-with-a-long-name.com"
        profile = "Profile 4"
        saved = False
        auth_cookie_names = ["session"]

    monkeypatch.setattr(
        "navig_browser.signed_in_sites.discover_signed_in_sites", lambda *a, **k: [_Site()]
    )
    res = runner.invoke(vault_app, ["login", "discover", "--json"])
    assert res.exit_code == 0, res.output
    rows = json.loads(res.output[res.output.index("["):])
    assert rows[0]["domain"] == _Site.domain
