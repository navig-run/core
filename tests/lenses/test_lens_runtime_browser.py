"""The Lens page runtime, driven in a REAL headless Chromium.

Every host injects the same compiled script, so this is the behaviour test for all of
them: matching, @run-at, @noframes, the once-per-document guard, isolation between
Lenses, GM storage, and strict-CSP pages. Pages are served by request interception —
no network, no server.
"""

from __future__ import annotations

import importlib.util

import pytest

from navig.lenses.compile import compile_lens
from navig.lenses.manifest import parse_manifest


def _playwright_available() -> bool:
    """Installed AND inspectable.

    ``find_spec`` raises ``ValueError`` — not ``ImportError`` — when another test left a
    spec-less stub in ``sys.modules`` (``tests/browser/test_no_playwright_stub_pollution.py``
    documents that leak), and a bare call in the module-level ``pytestmark`` turned that
    into a COLLECTION error that aborted the whole run instead of a skip.
    """
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


pytestmark = pytest.mark.skipif(
    not _playwright_available(), reason="playwright not installed"
)

PAGES = {
    "https://example.com/": (
        "<html><head><script>window.__pageSawStart = !!window.__lensStart;</script></head>"
        "<body><p id='p'>hi</p><iframe src='https://example.com/frame'></iframe></body></html>"
    ),
    "https://example.com/frame": "<html><body>frame</body></html>",
    "https://other.test/": "<html><body>other</body></html>",
    "https://strict.test/": "<html><body><p id='p'>strict</p></body></html>",
}
CSP = {"https://strict.test/": "default-src 'none'; style-src 'self'; script-src 'self'"}


def _lens(name: str, body: str, *, extra: str = "", match: str = "*://example.com/*") -> str:
    src = (
        "// ==UserScript==\n"
        f"// @name {name}\n"
        f"// @match {match}\n"
        f"{extra}"
        "// ==/UserScript==\n"
        f"{body}\n"
    )
    return compile_lens(parse_manifest(src), src, host="playwright").code


@pytest.fixture
def browser():
    # Function-scoped on purpose: tests/conftest.py `pytest_runtest_setup` terminates
    # every child process before each test, so a module-scoped browser dies after the
    # first test and the next one hangs on a dead driver.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = None
        errors: list[str] = []
        # Playwright's bundled build first, then an installed Chrome / Edge (its own
        # throwaway profile, closed below) — the bundled build often lags the library.
        for channel in (None, "chrome", "msedge"):
            try:
                b = pw.chromium.launch(headless=True, channel=channel)
                break
            except Exception as exc:  # noqa: BLE001 — that binary isn't on this machine
                errors.append(f"{channel or 'bundled'}: {str(exc).splitlines()[0]}")
        if b is None:
            pytest.skip("no chromium available: " + " | ".join(errors))
        try:
            yield b
        finally:
            b.close()


def _page(browser, *scripts: str):
    ctx = browser.new_context()

    def serve(route):
        url = route.request.url
        headers = {"content-type": "text/html"}
        if url in CSP:
            headers["content-security-policy"] = CSP[url]
        route.fulfill(status=200, headers=headers, body=PAGES.get(url, "<html></html>"))

    ctx.route("**/*", serve)
    for code in scripts:
        ctx.add_init_script(code)
    return ctx, ctx.new_page()


def test_document_start_runs_before_page_scripts_and_only_on_matching_urls(browser) -> None:
    ctx, page = _page(
        browser,
        _lens("Start", "window.__lensStart = true;", extra="// @run-at document-start\n"),
    )
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__pageSawStart") is True
        page.goto("https://other.test/")
        assert page.evaluate("window.__lensStart") is None
    finally:
        ctx.close()


def test_document_end_sees_the_dom_and_idle_runs_after_it(browser) -> None:
    ctx, page = _page(
        browser,
        _lens("End", "window.__end = !!document.getElementById('p');", extra="// @run-at document-end\n"),
        _lens("Idle", "window.__idle = document.readyState;"),
    )
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__idle !== undefined")
        assert page.evaluate("window.__end") is True
        assert page.evaluate("window.__idle") in ("interactive", "complete")
    finally:
        ctx.close()


def test_noframes_skips_iframes_and_the_default_runs_in_them(browser) -> None:
    ctx, page = _page(
        browser,
        _lens("All", "document.documentElement.dataset.all = '1';", extra="// @run-at document-end\n"),
        _lens(
            "Top", "document.documentElement.dataset.top = '1';",
            extra="// @run-at document-end\n// @noframes\n",
        ),
    )
    try:
        page.goto("https://example.com/")
        frame = page.frame(url="https://example.com/frame")
        assert frame is not None
        frame.wait_for_load_state()
        page.wait_for_timeout(50)
        assert frame.evaluate("document.documentElement.dataset.all") == "1"
        assert frame.evaluate("document.documentElement.dataset.top") is None
        assert page.evaluate("document.documentElement.dataset.top") == "1"
    finally:
        ctx.close()


def test_the_same_lens_injected_twice_runs_once(browser) -> None:
    code = _lens("Once", "window.__runs = (window.__runs || 0) + 1;", extra="// @run-at document-start\n")
    ctx, page = _page(browser, code, code)  # e.g. an attached pane AND the agent worker
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__runs") == 1
    finally:
        ctx.close()


def test_a_throwing_lens_does_not_stop_the_next_one(browser) -> None:
    ctx, page = _page(
        browser,
        _lens("Boom", "throw new Error('boom');", extra="// @run-at document-start\n"),
        _lens("Fine", "window.__fine = true;", extra="// @run-at document-start\n"),
    )
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__fine") is True
    finally:
        ctx.close()


def test_gm_storage_persists_across_reloads_per_lens(browser) -> None:
    body = (
        "var n = GM_getValue('n', 0) + 1; GM_setValue('n', n); window.__n = n;"
        "window.__keys = GM_listValues();"
    )
    # @noframes: the page's same-origin iframe would otherwise run (and count) too.
    grants = "// @noframes\n// @grant GM_getValue\n// @grant GM_setValue\n// @grant GM_listValues\n"
    ctx, page = _page(browser, _lens("Counter", body, extra=grants + "// @run-at document-end\n"))
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__n") == 1
        page.reload()
        assert page.evaluate("window.__n") == 2
        assert page.evaluate("window.__keys") == ["n"]
    finally:
        ctx.close()


def test_ungranted_apis_are_undefined_so_feature_detection_works(browser) -> None:
    # [ungranted, granted-but-unimplemented, always provided, always provided]
    # GM_getResourceText stays in the second slot on purpose: it is grantable (so the
    # grant reaches the runtime) but has no implementation, which is the case feature
    # detection exists for. GM_xmlhttpRequest used to be the example and is now real.
    body = (
        "window.__types = [typeof GM_addStyle, typeof GM_getResourceText, typeof GM_info,"
        " typeof navig.toast];"
    )
    ctx, page = _page(
        browser,
        _lens("Bare", body, extra="// @grant GM_getResourceText\n// @run-at document-end\n"),
    )
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__types") == ["undefined", "undefined", "object", "function"]
    finally:
        ctx.close()


def test_lenses_and_gm_addstyle_work_under_a_strict_csp(browser) -> None:
    body = (
        "GM_addStyle('#p{color: rgb(1, 2, 3)}');"
        "window.__strict = true;"
    )
    ctx, page = _page(
        browser,
        _lens(
            "Strict", body, match="*://strict.test/*",
            extra="// @grant GM_addStyle\n// @run-at document-end\n",
        ),
    )
    try:
        page.goto("https://strict.test/")
        assert page.evaluate("window.__strict") is True
        color = page.evaluate("getComputedStyle(document.getElementById('p')).color")
        assert color == "rgb(1, 2, 3)"
    finally:
        ctx.close()


def test_a_let_polyfill_of_a_granted_name_is_not_a_syntax_error(browser) -> None:
    body = "let GM_addStyle = (c) => c; window.__poly = GM_addStyle('ok');"
    ctx, page = _page(browser, _lens("Poly", body, extra="// @grant GM_addStyle\n// @run-at document-end\n"))
    try:
        page.goto("https://example.com/")
        assert page.evaluate("window.__poly") == "ok"
    finally:
        ctx.close()


def test_menu_command_renders_a_launcher_that_runs_it(browser) -> None:
    body = "GM_registerMenuCommand('Do it', function () { window.__clicked = true; });"
    ctx, page = _page(
        browser,
        _lens("Menu", body, extra="// @grant GM_registerMenuCommand\n// @run-at document-end\n"),
    )
    try:
        page.goto("https://example.com/")
        pill = page.get_by_role("button", name="NAVIG Lens actions")
        assert pill.get_attribute("aria-expanded") == "false"
        pill.click()
        assert pill.get_attribute("aria-expanded") == "true"
        page.get_by_role("menuitem", name="Do it").click()
        assert page.evaluate("window.__clicked") is True
        assert pill.get_attribute("aria-expanded") == "false"
    finally:
        ctx.close()


def test_value_change_listener_sees_own_writes_and_another_tabs(browser) -> None:
    """The cross-tab signal a Lens uses to merge instead of overwrite.

    VK Toolkit's contacts save is guarded by this: without it a tab holding a stale
    copy writes over a friends import running elsewhere (measured for them: 5,020
    people became 534). `remote` is what tells a tab to skip re-merging its own write.
    """
    # The GM_* names are parameters of the Lens body, not page globals, so the driver
    # has to be exported from inside — which is also how a Lens itself would call it.
    body = (
        "window.__ev = []; GM_addValueChangeListener('contacts', function (k, o, n, r) {"
        " window.__ev.push({k: k, o: o, n: n, remote: r}); });"
        "var second = GM_addValueChangeListener('other', function () { window.__ev.push('other'); });"
        "window.__gone = GM_removeValueChangeListener(second);"
        "window.__set = function (k, v) { GM_setValue(k, v); };"
    )
    grants = (
        "// @noframes\n// @run-at document-end\n"
        "// @grant GM_addValueChangeListener\n// @grant GM_removeValueChangeListener\n"
        "// @grant GM_setValue\n"
    )
    ctx, page = _page(browser, _lens("Chatty", body, extra=grants))
    try:
        page.goto("https://example.com/")

        # A removed listener must stop firing.
        page.evaluate("window.__set('other', 1)")
        page.wait_for_timeout(30)
        assert page.evaluate("window.__ev") == []
        assert page.evaluate("window.__gone") is True

        # Our own write: remote === false.
        page.evaluate("window.__set('contacts', {a: 1})")
        page.wait_for_function("window.__ev.length === 1")
        assert page.evaluate("window.__ev[0]") == {"k": "contacts", "o": None, "n": {"a": 1}, "remote": False}

        # A second tab on the same origin: remote === true, and the new value arrives.
        page2 = ctx.new_page()
        page2.goto("https://example.com/")
        page2.evaluate("window.__set('contacts', {a: 2})")
        page.wait_for_function("window.__ev.length === 2")
        assert page.evaluate("window.__ev[1]") == {"k": "contacts", "o": {"a": 1}, "n": {"a": 2}, "remote": True}

        # Another Lens's key must not leak in — storage is namespaced per Lens.
        page2.evaluate("window.__set('elsewhere', 7)")
        page.wait_for_timeout(40)
        assert page.evaluate("window.__ev.length") == 2
    finally:
        ctx.close()


def test_gm_xmlhttp_request_answers_a_reachable_url(browser) -> None:
    body = (
        "window.__ok = null; window.__err = null;"
        "GM_xmlhttpRequest({ url: 'https://example.com/', method: 'GET',"
        "  onload: function (r) { window.__ok = {status: r.status, head: r.responseText.slice(0, 6)}; },"
        "  onerror: function (e) { window.__err = e.error || e.errorText || 'error'; },"
        "  ontimeout: function () { window.__err = 'timeout'; } });"
    )
    grants = "// @noframes\n// @run-at document-end\n// @grant GM_xmlhttpRequest\n"
    ctx, page = _page(browser, _lens("Xhr", body, extra=grants))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__ok !== null || window.__err !== null")
        assert page.evaluate("window.__err") is None
        assert page.evaluate("window.__ok") == {"status": 200, "head": "<html>"[:6]}
    finally:
        ctx.close()


def test_gm_xmlhttp_request_fails_honestly_when_the_page_cannot_fetch(browser) -> None:
    """No silent hang: a URL the page's own world is not allowed to read reports onerror.

    This is the ceiling for a page-context implementation (a privileged proxy would be
    forgeable by any script on the page), so the failure has to be legible — scripts
    feature-detect and fall back, but they must not hang waiting for a callback.
    """
    body = (
        "window.__state = 'pending';"
        "GM_xmlhttpRequest({ url: 'file:///C:/no-such-file/never.txt', method: 'GET',"
        "  onload: function () { window.__state = 'loaded'; },"
        "  onerror: function () { window.__state = 'error'; },"
        "  ontimeout: function () { window.__state = 'timeout'; } });"
    )
    grants = "// @noframes\n// @run-at document-end\n// @grant GM_xmlhttpRequest\n"
    ctx, page = _page(browser, _lens("XhrFail", body, extra=grants))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__state !== 'pending'", timeout=8000)
        assert page.evaluate("window.__state") in ("error", "timeout")
        assert page.evaluate("window.__state") != "loaded", "file:// must not be reported as a success"
    finally:
        ctx.close()


# ── navig.capture: present only with the grant AND a host bridge, never on window ──────

_CAPTURE_LENS_BODY = (
    "window.__capType = typeof navig.capture;"
    "if (typeof navig.capture === 'function') navig.capture('hello from the lens')"
    ".then(function (r) { window.__capResult = r; });"
)


def _with_bridge(code: str) -> str:
    """Wrap a compiled Lens the way the Chrome host does (apps/dock background/lenses.js):
    a closure variable, never a global. The fake `send` records what it was given."""
    return (
        "(function () {\n"
        "var __navigBridge = Object.freeze({ send: function (p) {"
        " document.documentElement.setAttribute('data-sent', JSON.stringify(p));"
        " return Promise.resolve({ ok: true, queued: false }); } });\n"
        f"{code}\n"
        "})();\n"
    )


def _capture_lens(*, granted: bool) -> str:
    src = (
        "// ==UserScript==\n// @name Capturer\n// @match *://example.com/*\n"
        + ("// @grant navig.capture\n" if granted else "")
        + "// ==/UserScript==\n" + _CAPTURE_LENS_BODY + "\n"
    )
    return compile_lens(parse_manifest(src), src, host="chrome").code


def test_capture_exists_with_the_grant_and_a_bridge_and_sends_through_it(browser) -> None:
    ctx, page = _page(browser, _with_bridge(_capture_lens(granted=True)))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__capResult !== undefined")
        assert page.evaluate("window.__capType") == "function"
        assert page.evaluate("window.__capResult") == {"ok": True, "queued": False}
        sent = page.evaluate("JSON.parse(document.documentElement.getAttribute('data-sent'))")
        assert sent["content"] == "hello from the lens" and sent["kind"] == "selection"
        assert sent["url"] == "https://example.com/"
        # The bridge is a closure variable: page JavaScript cannot see or call it.
        assert page.evaluate("typeof window.__navigBridge") == "undefined"
        assert page.evaluate("typeof window.navig") == "undefined"
    finally:
        ctx.close()


@pytest.mark.parametrize(("granted", "bridged"), [(True, False), (False, True)])
def test_capture_is_absent_without_the_grant_or_without_a_bridge(browser, granted, bridged) -> None:
    code = _capture_lens(granted=granted)
    ctx, page = _page(browser, _with_bridge(code) if bridged else code)
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__capType !== undefined")
        assert page.evaluate("window.__capType") == "undefined"
        assert page.evaluate("document.documentElement.getAttribute('data-sent')") is None
    finally:
        ctx.close()


# ── Bridged storage: values live with the host, never in the page's localStorage (R2) ──


def _storage_lens(body: str, *, grants: str, run_at: str = "document-end") -> tuple[str, str]:
    """A Lens compiled for Chrome (where a storage grant means a bridge); returns (id, code)."""
    src = (
        "// ==UserScript==\n// @name Keyholder\n// @match *://example.com/*\n// @noframes\n"
        f"// @run-at {run_at}\n{grants}// ==/UserScript==\n{body}\n"
    )
    manifest = parse_manifest(src)
    return manifest.id, compile_lens(manifest, src, host="chrome").code


def _with_storage_bridge(code: str, *, seed: dict | None = None, fail_load: bool = False, delay_ms: int = 0) -> str:
    """The Chrome host's storage bridge, faked: values in `window.__host` (standing in for
    the extension's storage, which the page cannot reach) — never in localStorage."""
    import json

    return (
        "(function () {\n"
        f"var host = window.__host = {{ values: {json.dumps(seed or {})}, writes: [], watchers: [] }};\n"
        "var __navigBridge = Object.freeze({ storage: Object.freeze({\n"
        "  load: function () { return new Promise(function (res) { setTimeout(function () {"
        f"    res({json.dumps(bool(fail_load))} ? {{ ok: false, error: 'host down' }}"
        "      : { ok: true, values: Object.assign({}, host.values) }); }, " + str(delay_ms) + "); }); },\n"
        "  write: function (k, raw, client) { host.writes.push([k, raw]);"
        "    if (raw === null) delete host.values[k]; else host.values[k] = raw;"
        "    return Promise.resolve({ ok: true }); },\n"
        "  watch: function (client, onChange) { host.watchers.push(onChange);"
        "    onChange({ values: Object.assign({}, host.values) }); return { close: function () {} }; }\n"
        "}) });\n"
        f"{code}\n"
        "})();\n"
    )


_PAGE_LENS_KEYS = (
    "Object.keys(localStorage).filter(function (k) { return k.indexOf('navig-lens:') === 0; })"
)


def test_a_bridged_lens_reads_and_writes_the_host_and_never_page_storage(browser) -> None:
    body = (
        "window.__key = GM_getValue('apiKey', 'none'); GM_setValue('model', 'deepseek');"
        "window.__list = GM_listValues().sort();"
    )
    grants = "// @grant GM_getValue\n// @grant GM_setValue\n// @grant GM_listValues\n"
    # document-start + a slow host: the Lens must still see its value, so it waits for it.
    _, code = _storage_lens(body, grants=grants, run_at="document-start")
    ctx, page = _page(browser, _with_storage_bridge(code, seed={"apiKey": '"sk-secret"'}, delay_ms=60))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__list !== undefined")
        assert page.evaluate("window.__key") == "sk-secret"
        assert page.evaluate("window.__list") == ["apiKey", "model"]
        assert page.evaluate("window.__host.values.model") == '"deepseek"'
        # The whole point: nothing the Lens holds is in the page's storage.
        assert page.evaluate(_PAGE_LENS_KEYS) == []
    finally:
        ctx.close()


def test_values_left_in_page_storage_move_to_the_host_and_are_scrubbed(browser) -> None:
    body = "window.__legacy = GM_getValue('legacy'); window.__api = GM_getValue('apiKey');"
    lens_id, code = _storage_lens(body, grants="// @grant GM_getValue\n")
    prefix = f"navig-lens:{lens_id}:"
    # What an older, unisolated runtime left behind on this origin. Top frame only: init
    # scripts also run in the page's same-origin iframe, which would plant them again
    # after the scrub (measured: the plant ran top, then frame).
    leftovers = (
        "if (window.top === window) {"
        f"localStorage.setItem('{prefix}legacy', JSON.stringify('kept'));"
        f"localStorage.setItem('{prefix}apiKey', JSON.stringify('stale'));"
        "localStorage.setItem('unrelated', 'page data'); }"
    )
    ctx, page = _page(browser, leftovers, _with_storage_bridge(code, seed={"apiKey": '"fresh"'}))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__api !== undefined")
        assert page.evaluate("window.__legacy") == "kept", "a value only the page had is not lost"
        assert page.evaluate("window.__api") == "fresh", "the protected copy wins"
        page.wait_for_function(f"{_PAGE_LENS_KEYS}.length === 0")
        assert page.evaluate("window.__host.values.legacy") == '"kept"'
        assert page.evaluate("localStorage.getItem('unrelated')") == "page data", "only our keys are touched"
    finally:
        ctx.close()


def test_a_failed_load_never_writes_defaults_over_the_users_values(browser) -> None:
    # The Lens computes from defaults when it cannot see the stored key; persisting that
    # would destroy the real key. It runs, in memory only, and the host gets no writes.
    body = "window.__key = GM_getValue('apiKey', 'unset'); GM_setValue('apiKey', 'unset'); window.__ran = true;"
    _, code = _storage_lens(body, grants="// @grant GM_getValue\n// @grant GM_setValue\n")
    ctx, page = _page(browser, _with_storage_bridge(code, seed={"apiKey": '"sk-real"'}, fail_load=True))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__ran === true")
        assert page.evaluate("window.__key") == "unset"
        page.wait_for_timeout(50)
        assert page.evaluate("window.__host.writes") == []
        assert page.evaluate("window.__host.values.apiKey") == '"sk-real"'
        assert page.evaluate(_PAGE_LENS_KEYS) == []
    finally:
        ctx.close()


def test_a_bridged_listener_hears_the_host_and_ignores_a_forged_storage_event(browser) -> None:
    body = (
        "window.__ev = []; GM_addValueChangeListener('apiKey', function (k, o, n, r) {"
        " window.__ev.push({ k: k, o: o, n: n, remote: r }); });"
        "window.__get = function () { return GM_getValue('apiKey'); };"
    )
    grants = "// @grant GM_getValue\n// @grant GM_addValueChangeListener\n"
    lens_id, code = _storage_lens(body, grants=grants)
    ctx, page = _page(browser, _with_storage_bridge(code, seed={"apiKey": '"old"'}))
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__host.watchers.length === 1")
        # Page JavaScript can dispatch a StorageEvent that a listener in any world receives.
        page.evaluate(
            "window.dispatchEvent(new StorageEvent('storage', "
            f"{{ key: 'navig-lens:{lens_id}:apiKey', oldValue: '\"old\"', newValue: '\"evil\"' }}))"
        )
        page.wait_for_timeout(40)
        assert page.evaluate("window.__ev") == []
        assert page.evaluate("window.__get()") == "old"
        # Another tab's change, as the host pushes it.
        page.evaluate("window.__host.watchers[0]({ key: 'apiKey', raw: '\"new\"' })")
        assert page.evaluate("window.__ev") == [{"k": "apiKey", "o": "old", "n": "new", "remote": True}]
        assert page.evaluate("window.__get()") == "new"
    finally:
        ctx.close()


@pytest.mark.parametrize(("bridged", "world"), [(True, "isolated"), (False, "page")])
def test_gm_info_says_which_world_the_lens_runs_in(browser, bridged, world) -> None:
    # The feature test for anything that needs page JavaScript: in "isolated" unsafeWindow
    # is the Lens's own world, so a page-world hack must switch itself off honestly.
    body = "window.__world = [GM_info.navig.world, navig.world];"
    _, code = _storage_lens(body, grants="// @grant GM_getValue\n")
    ctx, page = _page(browser, _with_storage_bridge(code) if bridged else code)
    try:
        page.goto("https://example.com/")
        page.wait_for_function("window.__world !== undefined")
        assert page.evaluate("window.__world") == [world, world]
    finally:
        ctx.close()
