"""
tests/net/test_ssrf_wire.py
───────────────────────────
The SSRF guard wired into config (`policy_from_config`) and the `browser_fetch`
agent tool — the first production call sites (previously the guard protected
nothing).

No network I/O: literal IPs (169.254.x, 127.0.0.1) resolve without DNS, and
`get_config_manager` is monkeypatched.
"""
from __future__ import annotations

import pytest

from navig.net.ssrf import SsrfBlockedError, check_url, policy_from_config


class _FakeCM:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    def get_global_config(self) -> dict:
        return self._cfg


def _patch_config(monkeypatch, cfg: dict) -> None:
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM(cfg))


# ── policy_from_config ──────────────────────────────────────────────────────


def test_policy_default_blocks_private(monkeypatch):
    _patch_config(monkeypatch, {})
    policy = policy_from_config()
    assert policy.allow_private_network is False
    with pytest.raises(SsrfBlockedError):
        check_url("http://127.0.0.1:8765/", policy)


def test_policy_string_false_stays_secure(monkeypatch):
    # The bool("false") footgun: a stored "false" must NOT enable private —
    # otherwise being explicit about staying secure would *loosen* the guard.
    _patch_config(monkeypatch, {"net": {"ssrf": {"allow_private_network": "false"}}})
    assert policy_from_config().allow_private_network is False


def test_policy_enable_private_and_allowlist(monkeypatch):
    _patch_config(
        monkeypatch,
        {"net": {"ssrf": {
            "allow_private_network": "true",
            "allowed_domains": ["internal.example", " ", "svc.local"],
        }}},
    )
    policy = policy_from_config()
    assert policy.allow_private_network is True
    assert policy.allowed_domains == ("internal.example", "svc.local")  # blanks dropped
    check_url("http://127.0.0.1:8765/", policy)  # allow_private → no raise


def test_policy_broken_config_is_secure(monkeypatch):
    def boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("navig.config.get_config_manager", boom)
    assert policy_from_config().allow_private_network is False


# ── browser_fetch gate (the agent's arbitrary-URL fetcher) ──────────────────


async def test_browser_fetch_blocks_cloud_metadata(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    result = await BrowserFetchTool().run({"url": "http://169.254.169.254/latest/meta-data/"})
    assert result.success is False
    assert "SSRF guard" in (result.error or "")


async def test_browser_fetch_blocks_localhost_daemon(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    # bare host (no scheme) → tool prepends https:// → still resolves to loopback
    result = await BrowserFetchTool().run({"url": "127.0.0.1:8765/api/deck/exec"})
    assert result.success is False
    assert "private/internal" in (result.error or "")


async def test_browser_fetch_rejects_empty_url(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    result = await BrowserFetchTool().run({"url": "   "})
    assert result.success is False
    assert "url arg required" in (result.error or "")


# ── stage-2 Playwright route guard ──────────────────────────────────────────
# The entry check and stage-1 safe_fetch only cover the httpx path. Stage 2
# navigates from scratch, following redirects / meta-refresh / JS location= and
# loading subresources — none of which passed that check. These cover that hop.
# Literal IPs only: they resolve without DNS, so there is still no network I/O.


class _FakeRoute:
    """Stands in for a Playwright Route (handler takes `route`, reads route.request)."""

    def __init__(self, url: str) -> None:
        self.request = type("_Req", (), {"url": url})()
        self.action: str | None = None

    async def continue_(self) -> None:
        self.action = "continue"

    async def abort(self) -> None:
        self.action = "abort"


class _FakePage:
    def __init__(self) -> None:
        self.handler = None
        self.pattern = None

    async def route(self, pattern, handler) -> None:
        self.pattern = pattern
        self.handler = handler


async def _guarded_page(policy):
    from navig.tools.browser_fetch import _install_ssrf_route_guard

    page = _FakePage()
    await _install_ssrf_route_guard(page, policy)
    assert page.handler is not None, "guard did not register a route handler"
    return page


async def _verdict(page, url: str) -> str:
    route = _FakeRoute(url)
    await page.handler(route)
    return route.action


async def test_route_guard_aborts_cloud_metadata(monkeypatch):
    """A page that redirects/fetches to the cloud metadata endpoint is cut off — this
    is the exfiltration path stage 1 could not see."""
    _patch_config(monkeypatch, {})
    page = await _guarded_page(policy_from_config())
    assert await _verdict(page, "http://169.254.169.254/latest/meta-data/") == "abort"


async def test_route_guard_aborts_loopback_daemon(monkeypatch):
    _patch_config(monkeypatch, {})
    page = await _guarded_page(policy_from_config())
    assert await _verdict(page, "http://127.0.0.1:8765/api/deck/exec") == "abort"


async def test_route_guard_allows_public_host(monkeypatch):
    """The guard must not break ordinary pages — public addresses pass through."""
    _patch_config(monkeypatch, {})
    page = await _guarded_page(policy_from_config())
    assert await _verdict(page, "http://93.184.216.34/index.html") == "continue"


async def test_route_guard_passes_through_non_http_schemes(monkeypatch):
    """data:/blob:/about: never leave the process — blocking them would break
    rendering for no security gain."""
    _patch_config(monkeypatch, {})
    page = await _guarded_page(policy_from_config())
    assert await _verdict(page, "data:image/png;base64,iVBORw0KGgo=") == "continue"
    assert await _verdict(page, "about:blank") == "continue"


async def test_route_guard_caches_verdict_per_host(monkeypatch):
    """A page hits the same host dozens of times; DNS resolution is blocking, so the
    verdict is resolved once per host, not per request."""
    _patch_config(monkeypatch, {})
    # The route-guard now lives in navig.browser.ssrf_guard (shared with the agent
    # browser tool); it looks up check_url as a module global there.
    import navig.browser.ssrf_guard as sg

    calls = {"n": 0}
    real = sg.check_url

    def _counting(url, policy=None):
        calls["n"] += 1
        return real(url, policy)

    monkeypatch.setattr(sg, "check_url", _counting)

    page = await _guarded_page(policy_from_config())
    for path in ("/a", "/b", "/c"):
        assert await _verdict(page, f"http://93.184.216.34{path}") == "continue"
    assert calls["n"] == 1  # one check per host, not per request


async def test_route_guard_blocked_host_stays_blocked_when_cached(monkeypatch):
    """The cache must not become a bypass: a blocked host stays blocked on repeats."""
    _patch_config(monkeypatch, {})
    page = await _guarded_page(policy_from_config())
    for path in ("/a", "/b"):
        assert await _verdict(page, f"http://169.254.169.254{path}") == "abort"


async def test_route_guard_honors_allow_private_network(monkeypatch):
    """Opting into private fetches must relax the browser guard too — otherwise the
    documented escape hatch would silently only half-work."""
    _patch_config(monkeypatch, {"net": {"ssrf": {"allow_private_network": "true"}}})
    page = await _guarded_page(policy_from_config())
    assert await _verdict(page, "http://127.0.0.1:8765/api/deck/exec") == "continue"


async def test_browser_fetch_installs_guard_before_navigating(monkeypatch):
    """The guard is worthless if armed after the first navigation — assert ordering,
    and that the browser is always torn down."""
    _patch_config(monkeypatch, {})
    import navig.tools.browser_fetch as bf

    calls: list[str] = []

    class _Page:
        async def route(self, pattern, handler):
            calls.append("route")

        async def goto(self, url, **kw):
            calls.append("goto")

        async def content(self):
            return "<html>rendered</html>"

        async def screenshot(self, **kw):
            raise RuntimeError("no screenshot in tests")

    class _Controller:
        def __init__(self, config=None):
            self._page = _Page()

        async def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

    monkeypatch.setattr("navig.browser.controller.BrowserController", _Controller)
    monkeypatch.setattr("navig.browser.controller.BrowserConfig", lambda **kw: None)

    html, method, shot = await bf._browser_fetch(
        "https://example.test/", None, policy_from_config()
    )

    assert html == "<html>rendered</html>"
    assert method == "playwright"
    assert shot is None  # screenshot failure is non-fatal
    assert calls.index("route") < calls.index("goto")  # armed BEFORE navigation
    assert calls[-1] == "stop"  # browser always torn down


# ── ics_calendar (user-configured feed URL, followed with redirects) ────────


async def test_ics_calendar_url_blocked(monkeypatch):
    # Import BEFORE monkeypatching: navig.agent.proactive.__init__ instantiates a
    # module-level ProactiveEngine() that reads the real config manager on import,
    # so the fake CM must not be active yet.
    from navig.agent.proactive.ics_calendar import ICSCalendarProvider

    _patch_config(monkeypatch, {})
    provider = ICSCalendarProvider(url="http://169.254.169.254/cal.ics")
    # safe_fetch validates before any network I/O → blocked → graceful None
    assert await provider._fetch_ics() is None


# ── web_fetch (shared sync fetcher: agent web tool, MCP tool, board items) ──


def test_web_fetch_blocked_by_ssrf(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.web import REQUESTS_AVAILABLE, web_fetch

    if not REQUESTS_AVAILABLE:
        import pytest

        pytest.skip("requests not installed")
    result = web_fetch("http://169.254.169.254/latest/meta-data/")
    assert result.success is False
    assert "SSRF guard" in (result.error or "")


def test_web_fetch_blocks_localhost(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.web import REQUESTS_AVAILABLE, web_fetch

    if not REQUESTS_AVAILABLE:
        import pytest

        pytest.skip("requests not installed")
    result = web_fetch("http://127.0.0.1:8765/api/deck/exec")
    assert result.success is False
    assert "private/internal" in (result.error or "")


# ── api_pack web.api.get_json / post_json (agent-controlled "any endpoint") ──


def test_api_get_json_blocks_ssrf(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.domains.api_pack import _api_get_json

    result = _api_get_json("http://169.254.169.254/latest/meta-data/")
    assert result["status"] == "error"
    assert "SSRF" in result.get("error", "")


def test_api_post_json_blocks_ssrf(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.domains.api_pack import _api_post_json

    result = _api_post_json("http://127.0.0.1:8765/api/deck/exec", body={"x": 1})
    assert result["status"] == "error"
    assert "SSRF" in result.get("error", "")
