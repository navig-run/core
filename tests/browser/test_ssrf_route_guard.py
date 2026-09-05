"""SSRF route-guard for the agent's browser (navig.browser.ssrf_guard) + its wiring.

The agent navigates a real browser to model-chosen URLs. Validating only the entry URL
misses redirects / ``<meta refresh>`` / JS / subresources, so a public URL that bounces to
169.254.169.254 or the local daemon would be rendered and read back to the model. The guard
re-validates every in-page request. These tests need no real browser: literal IPs resolve
without DNS and the route handler is driven with a fake route object.
"""

from __future__ import annotations

from navig.browser.ssrf_guard import install_ssrf_route_guard, ssrf_verdict


class _FakeCM:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    def get_global_config(self) -> dict:
        return self._cfg


def _policy(monkeypatch, cfg: dict | None = None):
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM(cfg or {}))
    from navig.net.ssrf import policy_from_config

    return policy_from_config()


# ── ssrf_verdict (sync core) ────────────────────────────────────────────────


def test_verdict_non_network_schemes_allowed(monkeypatch):
    policy = _policy(monkeypatch)
    assert ssrf_verdict("data:image/png;base64,iVBORw0KGgo=", policy) is True
    assert ssrf_verdict("about:blank", policy) is True
    assert ssrf_verdict("blob:https://x/abc", policy) is True


def test_verdict_cloud_metadata_and_loopback_blocked(monkeypatch):
    policy = _policy(monkeypatch)
    assert ssrf_verdict("http://169.254.169.254/latest/meta-data/", policy) is False
    assert ssrf_verdict("http://127.0.0.1:8765/api/deck/exec", policy) is False


def test_verdict_public_host_allowed(monkeypatch):
    assert ssrf_verdict("http://93.184.216.34/index.html", _policy(monkeypatch)) is True


def test_verdict_private_allowed_only_when_configured(monkeypatch):
    blocked = _policy(monkeypatch)
    assert ssrf_verdict("http://127.0.0.1:8765/", blocked) is False
    allowed = _policy(monkeypatch, {"net": {"ssrf": {"allow_private_network": True}}})
    assert ssrf_verdict("http://127.0.0.1:8765/", allowed) is True


# ── install_ssrf_route_guard on a page- or context-like target ──────────────


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("_R", (), {"url": url})()
        self.action: str | None = None

    async def continue_(self) -> None:
        self.action = "continue"

    async def abort(self) -> None:
        self.action = "abort"


class _FakeTarget:
    """Stands in for a Playwright BrowserContext OR Page — both expose ``.route``."""

    def __init__(self) -> None:
        self.handler = None

    async def route(self, pattern: str, handler) -> None:
        self.handler = handler


async def _guarded(monkeypatch, cfg: dict | None = None) -> _FakeTarget:
    target = _FakeTarget()
    await install_ssrf_route_guard(target, _policy(monkeypatch, cfg))
    assert target.handler is not None, "guard did not register a route handler"
    return target


async def _verdict(target: _FakeTarget, url: str) -> str | None:
    route = _FakeRoute(url)
    await target.handler(route)
    return route.action


async def test_guard_aborts_internal_targets(monkeypatch):
    target = await _guarded(monkeypatch)
    assert await _verdict(target, "http://169.254.169.254/latest/") == "abort"
    assert await _verdict(target, "http://127.0.0.1:8765/api") == "abort"


async def test_guard_allows_public_and_non_network(monkeypatch):
    target = await _guarded(monkeypatch)
    assert await _verdict(target, "http://93.184.216.34/") == "continue"
    assert await _verdict(target, "data:text/plain,hello") == "continue"
    assert await _verdict(target, "about:blank") == "continue"


# ── _guard_controller wiring (agent session factory) ────────────────────────


async def test_guard_controller_installs_on_context(monkeypatch):
    from navig.agent.tools.browser_session import _guard_controller

    _policy(monkeypatch)  # patch config so policy_from_config works

    class _Ctrl:
        def __init__(self, ctx, page):
            self._context = ctx
            self._page = page

    ctx = _FakeTarget()
    await _guard_controller(_Ctrl(ctx, _FakeTarget()))
    assert ctx.handler is not None, "guard must install on the context (covers all tabs)"


async def test_guard_controller_falls_back_to_page(monkeypatch):
    from navig.agent.tools.browser_session import _guard_controller

    _policy(monkeypatch)

    class _Ctrl:
        def __init__(self):
            self._context = None
            self._page = _FakeTarget()

    ctrl = _Ctrl()
    await _guard_controller(ctrl)
    assert ctrl._page.handler is not None


async def test_guard_controller_never_raises_without_target(monkeypatch):
    from navig.agent.tools.browser_session import _guard_controller

    _policy(monkeypatch)

    class _Ctrl:
        _context = None
        _page = None

    await _guard_controller(_Ctrl())  # best-effort: must not raise


# ── _open_controller wires the guard on EVERY path (regression lock) ─────────
# The security value of the guard is only realised if _open_controller installs it
# on whatever controller it hands back. These lock that: a NEW controller branch (or
# an early return) that forgets `_guard_controller` makes the matching case fail.


class _FakeController:
    """Stand-in for BrowserController / StealthController / CDPBridge (started)."""

    def __init__(self, *args, **kwargs) -> None:
        self._context = _FakeTarget()  # records `.route(...)` via `.handler`
        self._page = _FakeTarget()
        self.started = False

    async def start(self) -> None:
        self.started = True


async def test_open_controller_guards_headless(monkeypatch):
    _policy(monkeypatch)
    monkeypatch.setattr("navig.browser.controller.BrowserController", _FakeController)
    monkeypatch.setattr("navig.browser.controller.BrowserConfig", lambda **k: object())
    from navig.agent.tools.browser_session import _open_controller

    ctrl = await _open_controller(stealth=False)
    assert ctrl.started
    assert ctrl._context.handler is not None, "headless controller must get the SSRF route-guard"


async def test_open_controller_guards_stealth(monkeypatch):
    _policy(monkeypatch)
    # Fake both the stealth controller and the tier-1 fallback so the assertion holds
    # whichever branch runs.
    monkeypatch.setattr("navig.browser.controller.BrowserController", _FakeController)
    monkeypatch.setattr("navig.browser.controller.BrowserConfig", lambda **k: object())
    try:
        monkeypatch.setattr("navig.browser.stealth.StealthController", _FakeController)
    except Exception:  # noqa: BLE001 — stealth module optional; fallback is already faked
        pass
    from navig.agent.tools.browser_session import _open_controller

    ctrl = await _open_controller(stealth=True)
    assert ctrl._context.handler is not None, "stealth controller must get the SSRF route-guard"


async def test_open_controller_guards_cdp_desktop_pane(monkeypatch):
    _policy(monkeypatch)
    monkeypatch.setattr("navig.browser.controller.BrowserController", _FakeController)
    monkeypatch.setattr("navig.browser.controller.BrowserConfig", lambda **k: object())
    monkeypatch.setattr("navig.browser.cdp_bridge.CDPBridge", _FakeController)
    from navig.agent.tools.browser_session import _open_controller

    ctrl = await _open_controller(stealth=False, cdp_url="http://127.0.0.1:9222")
    assert ctrl._context.handler is not None, "desktop-pane (CDP) controller must get the guard"
