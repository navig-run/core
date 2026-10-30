"""A deck read that FAILS must not answer "empty".

`GET /api/deck/apps/reminders` returned `_ok([])` when the runtime store was missing
and again on ANY exception; `GET /api/deck/apps/calendar` fell through to `_ok([])`
whenever the calendar command failed. Both rendered exactly like a successful empty
list — "No reminders", "nothing scheduled" — so a broken Brain looked like a quiet day.

These tests pin the contract the envelope exists for: a read that could not happen
answers `ok: false` with a reason, and only a read that DID happen may answer `[]`.

HTTP 200 is deliberate. The Mini App's `request()` collapses any non-2xx status to the
bare string "HTTP 503", which would throw the reason away before it reached a screen.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import apps as apps_mod

    app = web.Application()
    app.router.add_get("/reminders", apps_mod.handle_deck_apps_reminders_get)
    app.router.add_get("/calendar", apps_mod.handle_deck_apps_calendar)
    return app


async def test_reminders_missing_store_is_an_error_not_an_empty_list(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import apps as apps_mod

    monkeypatch.setattr(apps_mod, "_get_runtime_store", lambda: None)

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/reminders")
        assert r.status == 200
        body = await r.json()

    assert body["ok"] is False, f"a missing store answered as a successful read: {body}"
    assert "not available" in body["error"]
    assert "data" not in body, "an error response must not also carry a payload"


async def test_reminders_read_exception_is_an_error_not_an_empty_list(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import apps as apps_mod

    class _ExplodingStore:
        def _read_all(self, *_args, **_kwargs):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(apps_mod, "_get_runtime_store", lambda: _ExplodingStore())

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/reminders")
        assert r.status == 200
        body = await r.json()

    assert body["ok"] is False, f"a failed query answered as a successful read: {body}"
    assert "could not read reminders" in body["error"]
    assert "database is locked" in body["error"], "the reason has to survive to the client"


async def test_reminders_success_still_returns_the_list_shape(monkeypatch):
    """Anti-vacuity: the honesty branch must not have swallowed the happy path."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import apps as apps_mod

    class _EmptyStore:
        def _read_all(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(apps_mod, "_get_runtime_store", lambda: _EmptyStore())

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/reminders")
        assert r.status == 200
        body = await r.json()

    assert body == {"ok": True, "data": []}, "an empty store is a real, successful empty list"


async def test_calendar_unavailable_is_an_error_not_an_empty_list(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    import navig.platform.navig_cli as navig_cli

    def _no_cli():
        raise RuntimeError("no navig launcher on this host")

    monkeypatch.setattr(navig_cli, "navig_launcher", _no_cli)

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/calendar")
        assert r.status == 200
        body = await r.json()

    assert body["ok"] is False, f"a broken calendar answered as a successful empty read: {body}"
    assert "calendar is not available" in body["error"]
