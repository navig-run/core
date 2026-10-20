"""The event stream keeps the desktop exception under `deck.telegram_only`.

With the deck locked to the Telegram Mini App, `/api/events` demanded initData from
EVERY caller — including the desktop app on this machine, which the REST deck routes
trust. Its event bridge got 401 on each reconnect and saw no live event at all (the
Microsoft Store sale ring was how it surfaced: Telegram rang, the app stayed silent).

The exception must stay exactly as narrow as on the REST surface: genuinely local,
no relay / tunnel headers, no website Origin, not in dev_mode.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from navig.gateway.deck import auth
from navig.gateway.routes.events import handle_api_events


@pytest.fixture
def telegram_only(monkeypatch):
    monkeypatch.setitem(auth._deck_config, "telegram_only", True)
    monkeypatch.setitem(auth._deck_config, "dev_mode", False)
    monkeypatch.setitem(auth._deck_config, "bot_token", "123:abc")


async def _status(headers: dict[str, str] | None = None) -> int:
    """Open /api/events on a real loopback server and return the status it answers."""
    app = web.Application()
    app.router.add_get("/api/events", handle_api_events)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{server.port}/api/events",
                headers={"Accept": "text/event-stream", **(headers or {})},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status  # headers are enough: the body is an endless stream
    finally:
        await server.close()


async def test_the_local_desktop_app_gets_the_stream(telegram_only):
    # The OS app's server proxy: plain loopback, no Origin, no forwarding headers.
    assert await _status() == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Navig-Via": "lighthouse"},                               # relayed in by the uplink
        {"CF-Ray": "8a1b2c", "CF-Connecting-IP": "203.0.113.9"},     # through the tunnel
        {"Origin": "https://evil.example"},                          # a website's fetch to localhost
    ],
)
async def test_remote_or_web_callers_still_need_initdata(telegram_only, headers):
    assert await _status(headers) == 401


async def test_dev_mode_keeps_its_stricter_rule(telegram_only, monkeypatch):
    # dev_mode deliberately skips the loopback bypass everywhere; the stream agrees.
    monkeypatch.setitem(auth._deck_config, "dev_mode", True)
    assert await _status() == 401


async def test_without_the_lockdown_the_stream_is_open(monkeypatch):
    monkeypatch.setitem(auth._deck_config, "telegram_only", False)
    assert await _status() == 200
