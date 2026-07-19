"""Stage 3 — reusable JSON interception + read-only guard."""

from __future__ import annotations

from navig.browser import intercept as it

# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, url, status=200, headers=None, body=None, raise_json=False):
        self.url = url
        self.status = status
        self.headers = headers or {"content-type": "application/json"}
        self._body = body
        self._raise = raise_json

    async def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._body


class FakePage:
    def __init__(self):
        self._handlers = {}
        self.routes = []

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self._handlers.get(event, []).remove(handler)

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def emit_response(self, response):
        for h in list(self._handlers.get("response", [])):
            await h(response)


class FakeRoute:
    def __init__(self, url, method):
        self.request = type("Req", (), {"url": url, "method": method})()
        self.aborted = False
        self.continued = False

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


# ── JSONCollector ─────────────────────────────────────────────────────────────

async def test_collector_captures_only_matching_json():
    page = FakePage()
    col = it.JSONCollector(page, ["/api/comment/list"]).start()
    await page.emit_response(FakeResponse("https://x/api/comment/list/?c=1", body={"comments": [1]}))
    await page.emit_response(FakeResponse("https://x/other/endpoint", body={"nope": 1}))
    await page.emit_response(FakeResponse("https://x/api/comment/list/?c=2", raise_json=True))
    bodies = col.json_bodies()
    assert bodies == [{"comments": [1]}]
    assert col.items[0]["status"] == 200


async def test_collector_stop_detaches():
    page = FakePage()
    col = it.JSONCollector(page, ["/x"]).start()
    col.stop()
    await page.emit_response(FakeResponse("https://h/x", body={"a": 1}))
    assert col.json_bodies() == []


# ── read-only guard ───────────────────────────────────────────────────────────

def test_is_mutation_blocks_engagement_posts():
    assert it._is_mutation("https://t/aweme/v1/commit/item/digg/", "POST", ())
    assert it._is_mutation("https://t/api/comment/publish/", "POST", ())
    assert it._is_mutation("https://t/x/follow/user", "POST", ())


def test_is_mutation_allows_get_and_data_posts():
    assert not it._is_mutation("https://t/api/comment/list/", "GET", ())
    # a POST that fetches data (not an engagement marker) is allowed through
    assert not it._is_mutation("https://t/api/item/detail/", "POST", ())


def test_is_mutation_extra_markers():
    assert it._is_mutation("https://t/custom/react", "POST", ("/custom/react",))


async def test_read_only_guard_aborts_mutations_passes_reads():
    page = FakePage()
    await it.install_read_only_guard(page)
    _, handler = page.routes[0]

    like = FakeRoute("https://t/aweme/v1/commit/item/digg/", "POST")
    await handler(like)
    assert like.aborted and not like.continued

    read = FakeRoute("https://t/api/comment/list/", "GET")
    await handler(read)
    assert read.continued and not read.aborted


async def test_read_only_guard_is_idempotent():
    # Reinstalling on the same page must NOT stack duplicate routes (shared-controller reuse).
    page = FakePage()
    await it.install_read_only_guard(page)
    await it.install_read_only_guard(page)
    await it.install_read_only_guard(page)
    assert len(page.routes) == 1


# ── capture_json orchestration ────────────────────────────────────────────────

async def test_capture_json_runs_trigger_and_returns_bodies():
    page = FakePage()
    scrolls = {"n": 0}

    async def trigger():
        await page.emit_response(FakeResponse("https://t/api/comment/list/?p=1", body={"comments": ["a"]}))

    async def scroller():
        scrolls["n"] += 1
        await page.emit_response(FakeResponse("https://t/api/comment/list/?p=2", body={"comments": ["b"]}))

    bodies = await it.capture_json(
        page, ["/comment/list/"], trigger=trigger, scroller=scroller,
        rounds=2, settle_ms=0, read_only=True,
    )
    assert {"comments": ["a"]} in bodies
    assert {"comments": ["b"]} in bodies
    assert scrolls["n"] == 2
    assert len(page.routes) == 1  # read-only guard installed
