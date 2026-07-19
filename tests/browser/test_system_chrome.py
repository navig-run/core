"""Real system-Chrome CDP engine — launch args (no automation flag) + PID-safe cleanup."""

from __future__ import annotations

from navig.browser.system_chrome import SystemChromeController


def test_build_args_debug_port_profile_and_quiet_flags():
    c = SystemChromeController(user_data_dir="/tmp/navig_x")
    args = c._build_args("chrome.exe", 9222)
    assert args[0] == "chrome.exe"
    assert "--remote-debugging-port=9222" in args
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert "--no-first-run" in args  # CHROMIUM_QUIET_ARGS applied
    assert "--disable-search-engine-choice-screen" in args


def test_build_args_has_no_automation_flag():
    """THE point of this engine: no --enable-automation → navigator.webdriver stays false, so it
    looks like a normal browser (verified live: TikTok's login region check passes)."""
    args = SystemChromeController(user_data_dir="/tmp/x")._build_args("chrome.exe", 9222)
    assert not any("enable-automation" in a for a in args)
    assert not any(a.startswith("--headless") for a in args)  # headful by default (human login)


def test_build_args_headless_and_proxy():
    c = SystemChromeController(user_data_dir="/tmp/x", headless=True, proxy="http://user:pass@h:8080")
    args = c._build_args("chrome.exe", 9333)
    assert "--headless=new" in args
    assert any(a.startswith("--proxy-server=") for a in args)


def test_resolve_exe_picks_first_available(monkeypatch):
    from navig.browser import targets as t

    monkeypatch.setattr(t, "resolve_executable",
                        lambda app: "/path/msedge" if app == "edge" else None)
    app, exe = SystemChromeController()._resolve_exe()
    assert app == "edge" and exe == "/path/msedge"


def test_resolve_exe_honours_explicit_app(monkeypatch):
    from navig.browser import targets as t

    seen = []
    monkeypatch.setattr(t, "resolve_executable",
                        lambda app: seen.append(app) or ("/c/brave" if app == "brave" else None))
    app, exe = SystemChromeController(app="brave")._resolve_exe()
    assert app == "brave" and exe == "/c/brave"
    assert seen == ["brave"]  # only the explicit app was probed


def test_terminate_proc_kills_only_its_own_pid():
    """Safety: cleanup must kill ONLY the launched process handle — never a name-based
    `chrome.exe` sweep (that would close the user's real browser)."""
    c = SystemChromeController()
    calls = {"terminate": 0, "kill": 0}

    class _P:
        def terminate(self):
            calls["terminate"] += 1

        def wait(self, timeout=None):
            return 0

        def kill(self):
            calls["kill"] += 1

    c._proc = _P()
    c._terminate_proc()
    assert calls["terminate"] == 1 and calls["kill"] == 0
    assert c._proc is None  # handle cleared


def test_terminate_proc_noop_when_nothing_launched():
    c = SystemChromeController()
    c._terminate_proc()  # must not raise when no process was started
    assert c._proc is None


def test_router_selects_chrome_engine():
    from navig.browser.router import get_browser

    b = get_browser(engine="chrome")
    assert type(b).__name__ == "SystemChromeController"


# ── capture_existing_cookies (App-Bound-Encryption-safe session capture) ────────────

def test_default_browser_profile_none_when_absent(monkeypatch):
    import navig.browser.system_chrome as sc

    monkeypatch.setattr(sc, "_browser_userdata_roots", lambda app: [])
    assert sc.default_browser_profile("chrome") is None


def test_copy_min_profile_copies_only_essentials(tmp_path):
    import navig.browser.system_chrome as sc

    src = tmp_path / "src"
    (src / "Default/Network").mkdir(parents=True)
    (src / "Local State").write_text("ls")
    (src / "Default/Network/Cookies").write_bytes(b"ck")
    (src / "Default/Preferences").write_text("pr")
    (src / "Default/History").write_text("SHOULD NOT be copied")  # private, excluded
    dst = tmp_path / "dst"
    sc._copy_min_profile(src, dst)
    assert (dst / "Local State").read_text() == "ls"
    assert (dst / "Default/Network/Cookies").read_bytes() == b"ck"
    assert (dst / "Default/Preferences").exists()
    assert not (dst / "Default/History").exists()  # only the essentials are copied


async def test_capture_existing_cookies_no_profile(monkeypatch):
    import navig.browser.system_chrome as sc

    monkeypatch.setattr(sc, "default_browser_profile", lambda app: None)
    assert await sc.capture_existing_cookies("chrome", "tiktok") == []


async def test_capture_existing_cookies_filters_to_host(monkeypatch, tmp_path):
    import navig.browser.system_chrome as sc

    prof = tmp_path / "UserData"
    (prof / "Default/Network").mkdir(parents=True)
    (prof / "Local State").write_text("{}")
    (prof / "Default/Network/Cookies").write_bytes(b"db")
    monkeypatch.setattr(sc, "default_browser_profile", lambda app: prof)

    class _Ctx:
        async def cookies(self):
            return [{"name": "sessionid", "value": "a", "domain": ".tiktok.com"},
                    {"name": "other", "value": "b", "domain": ".example.com"}]

    class _Ctl:
        def __init__(self, **k):
            pass

        @property
        def context(self):
            return _Ctx()

        async def start(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setattr(sc, "SystemChromeController", _Ctl)
    out = await sc.capture_existing_cookies("chrome", "tiktok")
    assert [c["name"] for c in out] == ["sessionid"]  # only the tiktok.com cookie leaves


async def test_capture_existing_cookies_swallows_errors(monkeypatch, tmp_path):
    import navig.browser.system_chrome as sc

    prof = tmp_path / "UserData"
    prof.mkdir()
    monkeypatch.setattr(sc, "default_browser_profile", lambda app: prof)

    class _Boom:
        def __init__(self, **k):
            pass

        async def start(self):
            raise sc.SystemChromeUnavailable("no chrome")

    monkeypatch.setattr(sc, "SystemChromeController", _Boom)
    assert await sc.capture_existing_cookies("chrome", "tiktok") == []  # never raises
