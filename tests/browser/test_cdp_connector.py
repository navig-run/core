"""
Unit tests for the CDP connector core: target discovery/launch, session manager,
and the runtime loop. No real browser is launched — network + subprocess + bridge
are all mocked. Real browser/Electron paths are exercised manually (see docs).
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from navig.browser import cdp_runtime
from navig.browser import targets as t

pytestmark = pytest.mark.unit


# ────────────────────────── loopback guard ──────────────────────────


@pytest.mark.parametrize(
    "url,ok",
    [
        ("http://127.0.0.1:9222", True),
        ("http://localhost:9222/json", True),
        ("http://example.com:9222", False),
        ("http://8.8.8.8:9222", False),
        ("ftp://127.0.0.1:9222", False),
        ("not a url", False),
    ],
)
def test_is_loopback_endpoint(url, ok):
    assert t.is_loopback_endpoint(url) is ok


# ────────────────────────── discovery ──────────────────────────


def test_probe_port_parses_version_and_tabs(monkeypatch):
    def fake_get(url, timeout=1.5):
        if url.endswith("/json/version"):
            return {"Browser": "Chrome/125.0"}
        if url.endswith("/json/list"):
            return [
                {"id": "A", "title": "Tab A", "url": "https://a", "type": "page",
                 "webSocketDebuggerUrl": "ws://x"},
            ]
        return None

    monkeypatch.setattr(t, "_http_get_json", fake_get)
    target = t.probe_port(9222)
    assert target is not None
    assert target.browser == "Chrome/125.0"
    assert target.port == 9222
    assert len(target.tabs) == 1
    assert target.tabs[0].title == "Tab A"
    assert target.to_dict()["tabs"][0]["url"] == "https://a"


def test_probe_port_none_when_closed(monkeypatch):
    monkeypatch.setattr(t, "_http_get_json", lambda url, timeout=1.5: None)
    assert t.probe_port(9999) is None


def test_discover_targets_collects_live(monkeypatch):
    def fake_probe(port, timeout=1.5):
        return t.CDPTarget(port=port, browser="Electron/28", endpoint=f"http://127.0.0.1:{port}") \
            if port == 9223 else None

    monkeypatch.setattr(t, "probe_port", fake_probe)
    found = t.discover_targets((9222, 9223, 9224))
    assert [x.port for x in found] == [9223]


# ────────────────────────── classification ──────────────────────────


@pytest.mark.parametrize(
    "browser,tab_type,kind",
    [
        ("Chrome/125.0", "page", "browser"),
        ("Electron/28.0", "page", "browser"),
        ("Edg/120", "page", "browser"),
        ("HeadlessChrome/120", "page", "browser"),
        ("node.js/v20.0.0", "node", "node"),
        ("wrangler/v4.104.0", "node", "node"),
        ("mystery/1", "other", "other"),
    ],
)
def test_classify_kind(browser, tab_type, kind):
    tab = t.CDPTab(id="1", title="", url="", type=tab_type, ws_url="")
    assert t.classify_kind(browser, [tab]) == kind


def test_probe_port_classifies_node_inspector(monkeypatch):
    def fake_get(url, timeout=1.5):
        if url.endswith("/json/version"):
            return {"Browser": "wrangler/v4.104.0"}
        if url.endswith("/json/list"):
            return [{"id": "n", "title": "Worker", "url": "", "type": "node",
                     "webSocketDebuggerUrl": "ws://x"}]
        return None

    monkeypatch.setattr(t, "_http_get_json", fake_get)
    target = t.probe_port(9229)
    assert target is not None
    assert target.kind == "node"
    assert target.attachable is False


def test_attachable_targets_filters_non_browser(monkeypatch):
    browser = t.CDPTarget(port=9222, browser="Chrome/1", endpoint="e", kind="browser")
    node = t.CDPTarget(port=9229, browser="wrangler", endpoint="e", kind="node")
    monkeypatch.setattr(t, "discover_targets", lambda ports=None: [browser, node])
    assert [x.port for x in t.attachable_targets()] == [9222]


def test_list_page_targets_filters_and_indexes(monkeypatch):
    def fake_get(url, timeout=1.5):
        return [
            {"id": "1", "title": "A", "url": "https://a", "type": "page"},
            {"id": "bg", "title": "SW", "url": "chrome://x", "type": "service_worker"},
            {"id": "2", "title": "B", "url": "https://b", "type": "page"},
        ]

    monkeypatch.setattr(t, "_http_get_json", fake_get)
    pages = t.list_page_targets(9222)
    assert [p["index"] for p in pages] == [0, 1]
    assert [p["id"] for p in pages] == ["1", "2"]  # service_worker filtered out


def test_browser_gets_default_profile_electron_does_not(monkeypatch, tmp_path):
    calls = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        raise RuntimeError("stop after capturing args")

    monkeypatch.setattr(t, "resolve_executable", lambda app: str(tmp_path / "x.exe"))
    (tmp_path / "x.exe").write_text("x")
    monkeypatch.setattr(t, "default_debug_profile_dir", lambda app: f"/profiles/{app}")
    monkeypatch.setattr(t.subprocess, "Popen", fake_popen)

    # Browser → gets a --user-data-dir automatically.
    t.launch_with_cdp("chrome", port=9222, wait=False)
    assert any("--user-data-dir=/profiles/chrome" in a for a in calls["args"])

    # Electron app → no injected user-data-dir (keeps its logged-in profile).
    calls.clear()
    t.launch_with_cdp("discord", port=9223, wait=False)
    assert not any("--user-data-dir" in a for a in calls["args"])


# ────────────────────────── executable resolution ──────────────────────────


def test_known_app_ids_includes_electron_apps():
    ids = t.known_app_ids()
    for app in ("chrome", "discord", "notion"):
        assert app in ids


def test_resolve_executable_glob_newest_wins(monkeypatch, tmp_path):
    old = tmp_path / "app-1.0.0"
    new = tmp_path / "app-1.0.9"
    old.mkdir()
    new.mkdir()
    (old / "Discord.exe").write_text("x")
    (new / "Discord.exe").write_text("x")
    monkeypatch.setattr(t, "sys", type("S", (), {"platform": "win32"}))
    monkeypatch.setitem(
        t.KNOWN_APPS, "discord", {"win32": [str(tmp_path / "app-*" / "Discord.exe")]}
    )
    resolved = t.resolve_executable("discord")
    assert resolved is not None
    assert resolved.endswith("app-1.0.9\\Discord.exe") or resolved.endswith("app-1.0.9/Discord.exe")


def test_resolve_executable_unknown_returns_none():
    assert t.resolve_executable("does-not-exist") is None


# ────────────────────────── launch action ──────────────────────────


def test_launch_noop_when_port_already_serving(monkeypatch):
    from navig.browser import cdp_actions

    existing = t.CDPTarget(port=9222, browser="Chrome/1", endpoint="http://127.0.0.1:9222")
    monkeypatch.setattr(t, "probe_port", lambda port, timeout=1.5: existing)
    result = cdp_actions.launch("chrome", port=9222)
    assert result["ok"] is True
    assert result["relaunched"] is False


def test_launch_reports_running_app_needs_force(monkeypatch):
    from navig.browser import cdp_actions

    monkeypatch.setattr(t, "probe_port", lambda port, timeout=1.5: None)
    monkeypatch.setattr(t, "launch_with_cdp", lambda *a, **k: None)
    monkeypatch.setattr(t, "is_running", lambda app: True)
    result = cdp_actions.launch("discord", port=9222, force_restart=False)
    assert result["ok"] is False
    assert "force_restart" in result["error"]


# ────────────────────────── launched registry / stop / new ──────────────────────────


def test_launched_registry_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
    t.record_launched(9222, 4242, "chrome", "/prof")
    reg = t.get_launched()
    assert reg["9222"]["pid"] == 4242
    assert reg["9222"]["app"] == "chrome"
    t.remove_launched(9222)
    assert t.get_launched() == {}


def test_stop_launched_terminates_tracked_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
    killed = {}

    def fake_terminate(pid):
        killed["pid"] = pid
        return True

    monkeypatch.setattr(t, "_terminate_pid", fake_terminate)
    # Hermetic: stop_launched sweeps the machine for processes on the port and then PROBES the
    # port to prove it closed. Unmocked, this test read real process/network state — on a machine
    # with anything serving 9223 it failed, and the sweep enumerated the operator's own browsers.
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *a, **k: [])
    monkeypatch.setattr(t, "probe_port", lambda *a, **k: None)
    # This test is about the WIRING (registry -> terminate -> probe -> entry removed), not about
    # PID identity. Left unmocked, the identity guard would resolve pid 555 against the REAL
    # process table — flaky, and exactly the "arbitrary pid in a test" hazard the guard exists to
    # stop. Its own behaviour is covered in tests/browser/test_cdp_stop_attribution.py.
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *a, **k: True)
    t.record_launched(9223, 555, "chrome", None)
    res = t.stop_launched(9223)
    assert res["ok"] is True and killed["pid"] == 555
    assert t.get_launched() == {}  # entry removed


def test_stop_launched_unknown_port(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
    res = t.stop_launched(9999)
    assert res["ok"] is False


def test_find_free_port_skips_live_and_bound(monkeypatch):
    # 9222 has a live CDP target; 9223 is free.
    # ⚠ This one still performs a REAL bind for 9223..9226, so WHICH path satisfies it
    # depends on the host: on a machine where those ports are usable it returns one of
    # them, and on a machine that reserves the range it comes back through the
    # os-assigned fallback. Both are correct; the two tests below pin each path
    # deterministically, because this one alone cannot tell you which ran.
    monkeypatch.setattr(
        t, "probe_port",
        lambda port, timeout=0.4: t.CDPTarget(port=port, browser="C", endpoint="e") if port == 9222 else None,
    )
    port = t.find_free_port(start=9222, count=5)
    assert port is not None and port != 9222


def test_an_exhausted_window_falls_back_to_an_os_assigned_port(monkeypatch):
    """The window is a preference, not the search space.

    Windows reserves port ranges (Hyper-V / WSL / Docker) and `bind()` inside one raises
    PermissionError(13) even with nothing listening. Measured on the operator's machine:
    9181-9280 is reserved, which swallows the entire default 9222..9271 window, so
    `find_free_port()` returned None and `navig cdp new` reported "no free debug port
    available" on a host with tens of thousands of free ports.

    Simulated here by making every port in the window look occupied, which drives the
    loop to exhaustion without depending on the host's own reservations.
    """
    monkeypatch.setattr(
        t, "probe_port",
        lambda port, timeout=0.4: t.CDPTarget(port=port, browser="C", endpoint="e"),
    )
    port = t.find_free_port(start=9222, count=5)

    assert port is not None, "a machine with free ports must never report none available"
    assert not (9222 <= port < 9227), f"{port} came from the window that was exhausted"


def test_the_port_it_returns_can_actually_be_bound(monkeypatch):
    """Anti-vacuity: returning *a number* is worthless if the browser cannot bind it.

    This is the property the caller depends on — `cdp new` hands the value straight to
    Chrome as `--remote-debugging-port`.
    """
    monkeypatch.setattr(
        t, "probe_port",
        lambda port, timeout=0.4: t.CDPTarget(port=port, browser="C", endpoint="e"),
    )
    port = t.find_free_port(start=9222, count=5)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # raises if the port is unusable


def test_new_allocates_port_and_profile(monkeypatch):
    from navig.browser import cdp_actions

    monkeypatch.setattr(t, "find_free_port", lambda start=9222, count=50: 9231)
    monkeypatch.setattr(t, "new_session_profile_dir", lambda name=None: "/prof/throwaway")
    captured = {}

    def fake_launch(app, port=9222, *, user_data_dir=None, **k):
        captured.update(app=app, port=port, user_data_dir=user_data_dir)
        return t.CDPTarget(port=port, browser="Chrome/1", endpoint=f"http://127.0.0.1:{port}")

    monkeypatch.setattr(t, "launch_with_cdp", fake_launch)
    result = cdp_actions.new(app="chrome")
    assert result["ok"] is True
    assert result["port"] == 9231
    assert result["profile"] == "/prof/throwaway"
    assert captured["user_data_dir"] == "/prof/throwaway"


# ────────────────────────── runtime loop ──────────────────────────


def test_cdp_runtime_runs_coroutine():
    async def add():
        await asyncio.sleep(0)
        return 40 + 2

    assert cdp_runtime.run(add()) == 42


# ────────────────────────── session manager ──────────────────────────


class _FakeBridge:
    def __init__(self, debug_port=9222, tab_index=0):
        self.debug_port = debug_port
        self._page = None
        self.stopped = False

    async def start(self):
        self._page = object()  # simulate a live attach

    async def stop(self):
        self._page = None
        self.stopped = True


def test_session_manager_reuses_and_releases(monkeypatch):
    from navig.browser import session_manager as sm

    monkeypatch.setattr(sm, "CDPBridge", _FakeBridge)
    mgr = sm.CDPSessionManager()

    async def scenario():
        b1 = await mgr.get(9222)
        b2 = await mgr.get(9222)
        assert b1 is b2  # reused
        assert mgr.active_ports() == [9222]
        await mgr.release(9222)
        assert mgr.active_ports() == []
        assert b1.stopped is True

    cdp_runtime.run(scenario())


def test_session_manager_sweep_idle(monkeypatch):
    from navig.browser import session_manager as sm

    monkeypatch.setattr(sm, "CDPBridge", _FakeBridge)
    mgr = sm.CDPSessionManager()

    async def scenario():
        await mgr.get(9222)
        # Pretend it was last used well past the idle timeout.
        evicted = await mgr.sweep_idle(now=1e12)
        assert evicted == 1
        assert mgr.active_ports() == []

    cdp_runtime.run(scenario())


# ────────────────────────── multi-context tab enumeration ──────────────────────────


class _FakePage:
    def __init__(self, url):
        self.url = url

    async def title(self):
        return f"title:{self.url}"


class _FakeCtx:
    def __init__(self, pages):
        self.pages = pages


def test_cdpbridge_all_pages_spans_contexts():
    from navig.browser.cdp_bridge import CDPBridge

    bridge = CDPBridge(debug_port=9222)
    p1, p2, p3 = _FakePage("about:blank"), _FakePage("https://a"), _FakePage("https://b")
    bridge._browser = type("B", (), {"contexts": [_FakeCtx([p1, p2]), _FakeCtx([p3])]})()

    pages = bridge._all_pages()
    assert [p.url for p in pages] == ["about:blank", "https://a", "https://b"]

    async def _list():
        return await bridge.list_tabs()

    tabs = cdp_runtime.run(_list())
    assert [tab["index"] for tab in tabs] == [0, 1, 2]
    assert tabs[2]["url"] == "https://b"


def test_cdpbridge_switch_to_by_url_and_index():
    from navig.browser.cdp_bridge import CDPBridge

    class _P:
        def __init__(self, url):
            self.url = url
            self.context = None

        async def bring_to_front(self):
            return None

    bridge = CDPBridge(debug_port=9222)
    pa, pb = _P("https://github.com/x"), _P("https://mail.google.com")
    bridge._browser = type("B", (), {"contexts": [type("C", (), {"pages": [pa, pb]})()]})()

    async def _run_switch():
        by_url = await bridge.switch_to(url="google")
        assert by_url["ok"] is True and by_url["index"] == 1
        assert bridge._page is pb
        by_idx = await bridge.switch_to(index=0)
        assert by_idx["ok"] is True and bridge._page is pa
        miss = await bridge.switch_to(url="nope")
        assert miss["ok"] is False

    cdp_runtime.run(_run_switch())


# ---------------------------------------------------------------------------
# `cdp stop` / `cdp detach` with no --port: which browser is meant?
# ---------------------------------------------------------------------------

class TestDefaultSessionPort:
    """A literal 9222 default stopped addressing the real session.

    `find_free_port()` used to hand out 9222 almost always, so "no --port" meaning 9222
    was right by accident. Once the 9222+ window can be RESERVED by Windows, the session
    lives on an OS-assigned port and the old default named a browser that was never
    launched — `stop` answered "unknown port" for the only browser running.
    """

    def test_one_launched_browser_is_unambiguous(self, monkeypatch, tmp_path):
        monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
        t.record_launched(7245, 111, "chrome", None)

        from navig.browser import cdp_actions
        assert cdp_actions._default_session_port() == 7245

    def test_no_launched_browsers_keeps_the_old_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")

        from navig.browser import cdp_actions
        assert cdp_actions._default_session_port() == 9222

    def test_several_including_9222_keeps_the_old_default(self, monkeypatch, tmp_path):
        # Backward compatibility: this case behaved sensibly before and still does.
        monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
        t.record_launched(9222, 111, "chrome", None)
        t.record_launched(7245, 222, "chrome", None)

        from navig.browser import cdp_actions
        assert cdp_actions._default_session_port() == 9222

    def test_several_without_9222_refuses_to_guess(self, monkeypatch, tmp_path):
        """Closing the wrong browser is not recoverable — list them instead."""
        monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
        t.record_launched(7245, 111, "chrome", None)
        t.record_launched(8310, 222, "chrome", None)

        from navig.browser import cdp_actions
        assert cdp_actions._default_session_port() is None

        res = cdp_actions.stop()
        assert res["ok"] is False
        assert res["ports"] == [7245, 8310]
        assert "--port" in res["error"] and "--all" in res["error"]

    def test_detach_with_no_port_targets_the_launched_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr(t, "_launched_registry_path", lambda: tmp_path / "launched.json")
        t.record_launched(7245, 111, "chrome", None)

        from navig.browser import cdp_actions
        released = []
        monkeypatch.setattr(cdp_actions, "_rt", lambda coro: released.append(coro) or None,
                            raising=False)

        res = cdp_actions.detach()
        assert res["ok"] is True
        assert res["detached"] == 7245, "detach must follow the same resolution as stop"
