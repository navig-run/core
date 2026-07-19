"""The deck API must not depend on Telegram, and /cloud/status must be complete.

Two fixes the desktop Operator onboarding depends on:

A2 — The deck is the operator's LOCAL data + control plane (the desktop OS app
     reaches it over loopback). It used to mount only when a Telegram bot_token
     was already configured, so a fresh install exposed ZERO /api/deck/* routes —
     and the OS had no way to configure anything, including the token itself
     (`POST /api/deck/vault` is a deck route). A perfect chicken-and-egg.

A3 — `/api/deck/cloud/status` was blind to Lighthouse mode and to the deployed
     Deck URL, so nothing could report reachability without scraping CLI stdout.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck import register_deck_routes


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Per-test config isolation (ConfigSingleton caches paths at first touch)."""
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


# ── A2: the deck mounts with NO Telegram bot token ───────────────────────────


async def test_deck_status_reachable_without_bot_token(isolated_config):
    """A fresh install (no Telegram at all) must still serve the deck on loopback.

    aiohttp's TestClient connects from 127.0.0.1, which is exactly the desktop
    OS app's path (`_local_desktop_bypass`), so a 200 here proves the OS can
    drive setup before any Telegram token exists.
    """
    app = web.Application()
    register_deck_routes(app, bot_token="")  # ← the fresh-install case

    async with TestClient(TestServer(app)) as client:
        res = await client.get("/api/deck/status")
        assert res.status == 200, "deck must serve loopback with no Telegram token"
        body = await res.json()
        # Flat status body — presence of the shape is enough here.
        assert isinstance(body, dict)


async def test_deck_vault_route_exists_without_bot_token(isolated_config):
    """The route that STORES the Telegram token must not itself require one.

    This is the chicken-and-egg the old gate created: you needed a bot token to
    reach the endpoint you use to set the bot token.
    """
    app = web.Application()
    register_deck_routes(app, bot_token="")

    paths = {
        r.resource.canonical
        for r in app.router.routes()
        if r.resource is not None
    }
    assert "/api/deck/vault" in paths
    assert "/api/deck/cloud/status" in paths


# ── A3: /cloud/status reports reachability + the deployed deck ────────────────


async def test_cloud_status_exposes_reachability_and_deck_url(isolated_config):
    """One read must answer: how is the brain reached, and is the Deck published?"""
    from navig.core import Config

    cfg = Config()
    cfg.set("cloud.mode", "lighthouse", scope="global")
    cfg.set("cloud.lighthouse_url", "https://edge.example.workers.dev", scope="global")
    cfg.set("deck.public_url", "https://deck.example.workers.dev", scope="global")
    cfg.save(scope="global")

    app = web.Application()
    register_deck_routes(app, bot_token="")

    async with TestClient(TestServer(app)) as client:
        res = await client.get("/api/deck/cloud/status")
        assert res.status == 200
        body = await res.json()

    assert body["cloud_mode"] == "lighthouse"
    assert body["lighthouse_url"] == "https://edge.example.workers.dev"
    assert body["deck_public_url"] == "https://deck.example.workers.dev"


async def test_cloud_status_fields_present_when_unconfigured(isolated_config):
    """The new fields must always exist (empty), so clients can read them blind."""
    app = web.Application()
    register_deck_routes(app, bot_token="")

    async with TestClient(TestServer(app)) as client:
        res = await client.get("/api/deck/cloud/status")
        assert res.status == 200
        body = await res.json()

    for key in ("cloud_mode", "lighthouse_url", "deck_public_url"):
        assert key in body, f"/cloud/status must always expose {key}"
        assert body[key] == ""
