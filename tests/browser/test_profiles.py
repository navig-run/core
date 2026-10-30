"""Stage A — the multi-profile system (registry, stable ports, active pointer, real detection)."""

from __future__ import annotations

import json

import pytest

from navig.browser import cdp_actions as A
from navig.browser import profiles as p

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # config_dir() reads NAVIG_CONFIG_DIR live → this isolates the registry.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # keep port allocation offline/deterministic (no live browsers)
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    # …and independent of what THIS machine has bound: allocate_port() also runs a real
    # bind() check (port_is_bindable), so any process holding a port in the band — another
    # session's debug browser on 9281 — shifted every allocation and failed the sequential
    # test on a busy box only. Tests that exercise the bind check stub it themselves.
    monkeypatch.setattr("navig.browser.targets.port_is_bindable", lambda *a, **k: True)
    return tmp_path


# ---------------------------------------------------------------------------
# Registry + stable ports
# ---------------------------------------------------------------------------


def test_create_stable_port_and_persist(cfg):
    prof = p.create_profile("cybesis", note="work")
    assert p.PROFILE_PORT_BASE <= prof.port < p.PROFILE_PORT_BASE + p.PROFILE_PORT_COUNT
    assert (cfg / "cdp-profiles.json").exists()
    # idempotent — re-create returns the SAME stable port
    assert p.create_profile("cybesis").port == prof.port


def test_distinct_sequential_ports(cfg):
    a, b, c = (p.create_profile(n) for n in ("a", "b", "c"))
    ports = [a.port, b.port, c.port]
    assert len(set(ports)) == 3
    assert b.port == a.port + 1 and c.port == b.port + 1


def test_allocate_skips_a_port_serving_cdp(cfg, monkeypatch):
    a = p.create_profile("a")
    # pretend a foreign browser now holds the next port
    busy = a.port + 1
    monkeypatch.setattr("navig.browser.targets.probe_port",
                        lambda port, timeout=0.3: object() if port == busy else None)
    b = p.create_profile("b")
    assert b.port != busy


# ---------------------------------------------------------------------------
# Active profile
# ---------------------------------------------------------------------------


def test_active_pointer(cfg):
    p.create_profile("a")                 # first profile → auto-active
    assert p.get_active() == "a"
    p.create_profile("b")                 # second does not steal active
    assert p.get_active() == "a"
    assert p.set_active("b")
    assert p.get_active() == "b"
    assert p.resolve_active(None).name == "b"       # falls back to active
    assert p.resolve_active("a").name == "a"          # explicit wins
    assert not p.set_active("ghost")


def test_sole_profile_used_without_explicit_active(cfg):
    # the exact live scenario: one profile, no explicit `use` → still resolves
    p.create_profile("cybesis")
    # even if the active pointer were somehow unset, a sole profile is used
    data = p._read()
    data["active"] = None
    p._write(data)
    assert p.get_active() is None
    assert p.resolve_active(None) is not None
    assert p.resolve_active(None).name == "cybesis"


def test_remove_clears_active(cfg):
    p.create_profile("a")
    p.set_active("a")
    assert p.remove_profile("a")
    assert p.get_active() is None
    assert p.get_profile("a") is None


# ---------------------------------------------------------------------------
# Real Chrome detection + real profile
# ---------------------------------------------------------------------------


def test_detect_real_chrome_profiles(cfg, tmp_path, monkeypatch):
    base = tmp_path / "ud"
    base.mkdir()
    (base / "Local State").write_text(json.dumps(
        {"profile": {"info_cache": {"Default": {"name": "Person 1"},
                                     "Profile 3": {"name": "cybesis"}}}}))
    monkeypatch.setattr(p, "real_user_data_dir", lambda app="chrome": base)
    got = p.detect_real_chrome_profiles()
    assert {r["name"] for r in got} == {"Person 1", "cybesis"}
    assert {r["directory"] for r in got} == {"Default", "Profile 3"}


def test_create_real_profile(cfg, tmp_path, monkeypatch):
    base = tmp_path / "ud"
    base.mkdir()
    monkeypatch.setattr(p, "real_user_data_dir", lambda app="chrome": base)
    prof = p.create_real_profile("realcy", "Profile 3", note="my real chrome")
    assert prof.real and prof.profile_directory == "Profile 3"
    assert prof.user_data_dir == str(base)


# ---------------------------------------------------------------------------
# cdp_actions: reuse-if-running + real preflight
# ---------------------------------------------------------------------------


def test_profile_open_reuses_without_launch(cfg, monkeypatch):
    prof = p.create_profile("cy")
    launched = {}
    monkeypatch.setattr("navig.browser.targets.probe_port",
                        lambda port, timeout=0.4: object() if port == prof.port else None)
    monkeypatch.setattr("navig.browser.targets.launch_with_cdp",
                        lambda *a, **k: launched.setdefault("called", True))
    r = A.profile_open("cy")
    assert r["ok"] and r["reused"] is True
    assert "called" not in launched  # never relaunched


def test_profile_open_real_preflight_refuses_when_running(cfg, tmp_path, monkeypatch):
    base = tmp_path / "ud"
    base.mkdir()
    monkeypatch.setattr(p, "real_user_data_dir", lambda app="chrome": base)
    p.create_real_profile("realcy", "Profile 3")
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    monkeypatch.setattr("navig.browser.targets.is_running", lambda app: True)
    r = A.profile_open("realcy")
    assert not r["ok"] and "quit" in r["error"].lower()


def test_profile_open_missing(cfg):
    r = A.profile_open("ghost")
    assert not r["ok"] and "no profile" in r["error"]


def test_profile_new_rejects_duplicate(cfg):
    A.profile_new("cy")
    dup = A.profile_new("cy")
    assert not dup["ok"] and "already exists" in dup["error"]


def test_corrupt_registry_is_backed_up_not_lost(cfg):
    p.registry_path().write_text("{ this is not valid json", encoding="utf-8")
    assert p.list_profiles() == []                      # degrades to empty, doesn't crash
    assert p.registry_path().with_name("cdp-profiles.json.corrupt").exists()  # preserved


def test_set_active_reports_write_failure(cfg, monkeypatch):
    p.create_profile("cy")
    monkeypatch.setattr(p, "_write", lambda data: False)  # simulate disk-full
    assert p.set_active("cy") is False                     # surfaced, not swallowed


def test_profile_new_surfaces_persist_failure(cfg, monkeypatch):
    monkeypatch.setattr(p, "_write", lambda data: False)
    r = A.profile_new("cy")
    assert not r["ok"] and "saved" in r["error"]


def test_set_default_account(cfg):
    p.create_profile("cy")
    assert p.get_profile("cy").default_account is None
    assert p.set_default_account("cy", "me@gmail.com")
    assert p.get_profile("cy").default_account == "me@gmail.com"
    assert not p.set_default_account("ghost", "x@y.com")


def test_profile_new_binds_gmail(cfg):
    r = A.profile_new("work", gmail="work@company.com")
    assert r["ok"]
    assert p.get_profile("work").default_account == "work@company.com"


def test_resolve_port_prefers_running_active_profile(cfg, monkeypatch):
    from navig.commands.cdp import _resolve_port

    prof = p.create_profile("cy")
    p.set_active("cy")
    # active profile RUNNING → an OMITTED port uses its stable port (the default path this
    # test names). Contract: 4f4d1ee4be on main, "an explicit --port is an address — only an
    # omitted one follows the profile"; plugin-side pins live in
    # plugins/navig-browser/tests/test_resolve_port.py.
    monkeypatch.setattr("navig.browser.targets.probe_port",
                        lambda port, timeout=0.3: object() if port == prof.port else None)
    assert _resolve_port(None, None) == prof.port
    # ... and a literal 9222 is an ADDRESS, not "the caller said nothing". The old expectation
    # here was `_resolve_port(None, 9222) == prof.port` (9280): 9222-as-default rewrote a
    # pinned `-p 9222` run to whatever browser the active profile had open — the measured bug
    # 4f4d1ee4be removed (the pixel harness drove the operator's browser, not its own).
    assert _resolve_port(None, 9222) == 9222
    # an explicit non-default port is always respected
    assert _resolve_port(None, 9250) == 9250
    # active profile NOT running → keep 9222 (don't attach to a dead port)
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    assert _resolve_port(None, None) == 9222
    assert _resolve_port(None, 9222) == 9222


def test_profile_open_applies_the_saved_profile_proxy(cfg, monkeypatch):
    """`cdp profile proxy NAME …` must reach the launch, without the credentials."""
    p.create_profile("px")
    assert p.set_profile_proxy("px", "http://user:secret@proxy.example:8080")
    seen: dict = {}

    def fake_launch(*a, **k):
        seen["extra"] = k.get("extra_args")
        return None  # launch "fails" — only the args matter here

    monkeypatch.setattr("navig.browser.targets.is_running", lambda app: False)
    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", fake_launch)
    A.profile_open("px", headless=True, context="script")
    assert "--proxy-server=http://proxy.example:8080" in seen["extra"]
    assert not any("secret" in a for a in seen["extra"])


def test_profile_open_without_a_proxy_adds_no_proxy_switch(cfg, monkeypatch):
    p.create_profile("np")
    seen: dict = {}
    monkeypatch.setattr("navig.browser.targets.is_running", lambda app: False)
    monkeypatch.setattr("navig.browser.targets.launch_with_cdp",
                        lambda *a, **k: seen.setdefault("extra", k.get("extra_args")) and None)
    A.profile_open("np", headless=True, context="script")
    assert not any(a.startswith("--proxy-server") for a in seen["extra"])


def test_profile_new_reports_a_locked_registry_instead_of_crashing(cfg, monkeypatch):
    """create_profile() returns None on a registry lock; if the lock clears before the
    follow-up read, get_profile() finds a profile and profile_new used to read None.port."""
    existing = p.Profile(name="cy", port=9281, user_data_dir="x")
    calls = {"n": 0}

    def get_profile(name):
        calls["n"] += 1
        return None if calls["n"] == 1 else existing  # absent first, present after the lock

    monkeypatch.setattr(p, "get_profile", get_profile)
    monkeypatch.setattr(p, "create_profile", lambda *a, **k: None)
    r = A.profile_new("cy")
    assert r["ok"] is False and "busy" in r["error"]


def test_persona_import_fails_when_the_profile_cannot_be_registered(cfg, monkeypatch, tmp_path):
    import types

    from navig_browser import persona as _persona
    from navig_browser.commands import cdp as cdp_cmd
    from typer.testing import CliRunner

    capsule = tmp_path / "x.navigpersona"
    capsule.write_bytes(b"capsule")
    per = types.SimpleNamespace(profile="ghost", proxy=None)
    monkeypatch.setattr(_persona, "import_capsule", lambda blob, passphrase=None: (per, None))
    monkeypatch.setattr(p, "create_profile", lambda *a, **k: None)  # registry locked

    res = CliRunner().invoke(cdp_cmd.profile_app, ["import", str(capsule)])
    assert res.exit_code == 1, res.output
    assert "busy" in res.output and "Imported persona" not in res.output
