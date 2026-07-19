"""Firefox engine — a non-CDP browser tier (Playwright Firefox / Camoufox)."""

from __future__ import annotations

from navig.browser import firefox as fx

# ── pure helpers ──────────────────────────────────────────────────────────────

def test_looks_like_missing_binary():
    assert fx._looks_like_missing_binary(RuntimeError("Executable doesn't exist"))
    assert fx._looks_like_missing_binary(RuntimeError("please run playwright install firefox"))
    assert not fx._looks_like_missing_binary(RuntimeError("some unrelated crash"))


def test_to_pw_proxy():
    assert fx._to_pw_proxy(None) is None
    p = fx._to_pw_proxy("http://user:pass@host:8080")
    assert isinstance(p, dict) and p.get("server")


def test_engine_name_defaults():
    assert fx.FirefoxController().engine_name == "firefox"
    assert fx.FirefoxController(engine="camoufox").engine_name == "camoufox"
    assert fx.FirefoxController(engine="anything-else").engine_name == "firefox"


# ── fakes for the Playwright / Camoufox launch surface ─────────────────────────

class _FakePage:
    async def goto(self, *a, **k):
        return None


class _FakeContext:
    def __init__(self):
        self.pages = []
        self._cookies = []
        self.closed = False

    async def new_page(self):
        p = _FakePage()
        self.pages.append(p)
        return p

    async def cookies(self):
        return self._cookies

    async def storage_state(self):
        return {"cookies": self._cookies, "origins": []}

    async def close(self):
        self.closed = True


class _FakeFirefox:
    def __init__(self, ctx, fail_first=False):
        self._ctx = ctx
        self._fail_first = fail_first
        self.calls = 0

    async def launch_persistent_context(self, udd, **kw):
        self.calls += 1
        if self._fail_first and self.calls == 1:
            raise RuntimeError("Executable doesn't exist — run playwright install firefox")
        return self._ctx


class _FakePW:
    def __init__(self, ctx, fail_first=False):
        self.firefox = _FakeFirefox(ctx, fail_first)

    async def stop(self):
        return None


def _install_fake_playwright(monkeypatch, ctx, fail_first=False):
    pw = _FakePW(ctx, fail_first)

    class _CM:
        async def start(self):
            return pw

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _CM())
    return pw


# ── Playwright Firefox path ────────────────────────────────────────────────────

async def test_firefox_start_launches_persistent_context(monkeypatch):
    ctx = _FakeContext()
    _install_fake_playwright(monkeypatch, ctx)
    c = fx.FirefoxController(engine="firefox", headless=True)
    await c.start()
    assert c.engine_name == "firefox"
    assert c.context is ctx and c.is_running
    await c.stop()
    assert ctx.closed and not c.is_running


async def test_firefox_autoinstalls_binary_then_retries(monkeypatch):
    ctx = _FakeContext()
    pw = _install_fake_playwright(monkeypatch, ctx, fail_first=True)
    provisioned = {"n": 0}
    monkeypatch.setattr(fx, "ensure_playwright_firefox",
                        lambda: provisioned.__setitem__("n", 1))
    c = fx.FirefoxController(engine="firefox")
    await c.start()
    assert provisioned["n"] == 1          # auto-provisioned the missing binary
    assert pw.firefox.calls == 2          # and retried the launch
    assert c.context is ctx
    await c.stop()


# ── Camoufox path ──────────────────────────────────────────────────────────────

def _install_fake_camoufox(monkeypatch, yielded):
    class _CM:
        async def __aenter__(self):
            return yielded

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("camoufox.async_api.AsyncCamoufox", lambda **k: _CM())
    monkeypatch.setattr(fx, "ensure_camoufox", lambda: True)  # binary present (hermetic)


async def test_camoufox_uses_context_directly(monkeypatch):
    ctx = _FakeContext()  # a BrowserContext (no new_context) → used as-is
    _install_fake_camoufox(monkeypatch, ctx)
    c = fx.FirefoxController(engine="camoufox")
    await c.start()
    assert c.engine_name == "camoufox" and c.context is ctx
    await c.stop()


async def test_camoufox_makes_context_from_browser_no_viewport(monkeypatch):
    ctx = _FakeContext()
    seen = {}

    class _FakeBrowser:
        async def new_context(self, **k):
            seen.update(k)
            return ctx

    _install_fake_camoufox(monkeypatch, _FakeBrowser())
    c = fx.FirefoxController(engine="camoufox")
    await c.start()
    assert c.context is ctx  # created from the Browser via new_context()
    assert seen.get("no_viewport") is True  # avoids Camoufox's setDefaultViewport protocol error
    await c.stop()


async def test_camoufox_requires_binary(monkeypatch):
    # If the binary can't be provisioned, start() raises a clear FirefoxUnavailable.
    monkeypatch.setattr("camoufox.async_api.AsyncCamoufox", lambda **k: object())
    monkeypatch.setattr(fx, "ensure_camoufox", lambda: False)
    c = fx.FirefoxController(engine="camoufox")
    try:
        await c.start()
        raised = False
    except fx.FirefoxUnavailable:
        raised = True
    assert raised


# ── engine selection + binary provisioning helpers ─────────────────────────────

def test_best_login_engine_prefers_camoufox(monkeypatch):
    monkeypatch.setattr(fx, "camoufox_available", lambda: True)
    assert fx.best_login_engine() == "camoufox"


def test_best_login_engine_falls_back_to_firefox(monkeypatch):
    monkeypatch.setattr(fx, "camoufox_available", lambda: False)
    assert fx.best_login_engine() == "firefox"


def test_camoufox_binary_available_true(monkeypatch):
    monkeypatch.setattr("camoufox.pkgman.installed_verstr", lambda: "135.0")
    assert fx.camoufox_binary_available() is True


def test_camoufox_binary_available_false_when_not_fetched(monkeypatch):
    def boom():
        raise RuntimeError("not installed")

    monkeypatch.setattr("camoufox.pkgman.installed_verstr", boom)
    assert fx.camoufox_binary_available() is False


def test_ensure_camoufox_skips_fetch_when_present(monkeypatch):
    import subprocess

    monkeypatch.setattr(fx, "camoufox_available", lambda: True)
    monkeypatch.setattr(fx, "camoufox_binary_available", lambda: True)
    called = {"fetch": False}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.__setitem__("fetch", True))
    assert fx.ensure_camoufox() is True
    assert called["fetch"] is False  # already provisioned → no download


def test_ensure_camoufox_false_without_lib(monkeypatch):
    monkeypatch.setattr(fx, "camoufox_available", lambda: False)
    assert fx.ensure_camoufox() is False


# ── router dispatch ────────────────────────────────────────────────────────────

def test_router_selects_firefox_engine():
    from navig.browser.router import get_browser

    b = get_browser(engine="firefox")
    assert type(b).__name__ == "FirefoxController" and b.engine_name == "firefox"


def test_router_selects_camoufox_engine():
    from navig.browser.router import get_browser

    b = get_browser(engine="camoufox")
    assert type(b).__name__ == "FirefoxController" and b.engine_name == "camoufox"
