"""The deck's "Clean up yesterday's routine messages" switch.

``daily_clean_enabled`` on GET/POST /api/deck/social/telegram maps onto the
nested ``telegram.daily_clean.enabled`` key that ``navig/telegram/chat_tidy.py``
reads — read through coerce_bool, written as a real bool, siblings preserved.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


def _app() -> web.Application:
    from navig.gateway.deck.routes.social import (
        handle_deck_social_telegram_get,
        handle_deck_social_telegram_post,
    )

    app = web.Application()
    app.router.add_get("/api/deck/social/telegram", handle_deck_social_telegram_get)
    app.router.add_post("/api/deck/social/telegram", handle_deck_social_telegram_post)
    return app


def _fresh(key: str):
    from navig.config import ConfigManager

    return ConfigManager().get(key)


async def test_default_off_then_toggle_round_trip(isolated):
    from navig.config import ConfigManager
    from navig.telegram import chat_tidy

    ConfigManager().set_global("telegram.daily_clean.hour", 5)
    async with TestClient(TestServer(_app())) as client:
        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["daily_clean_enabled"] is False

        resp = await client.post("/api/deck/social/telegram", json={"daily_clean_enabled": True})
        body = await resp.json()
        assert resp.status == 200 and body["updated"] == ["daily_clean_enabled"]
        assert _fresh("telegram.daily_clean.enabled") is True  # a real bool
        assert _fresh("telegram.daily_clean.hour") == 5  # sibling preserved
        assert chat_tidy.settings(fresh=True)["enabled"] is True

        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["daily_clean_enabled"] is True

        await client.post("/api/deck/social/telegram", json={"daily_clean_enabled": False})
        assert _fresh("telegram.daily_clean.enabled") is False


async def test_string_config_value_is_coerced(isolated):
    from navig.config import ConfigManager

    ConfigManager().set_global("telegram.daily_clean.enabled", "false")  # `navig config set`
    async with TestClient(TestServer(_app())) as client:
        data = await (await client.get("/api/deck/social/telegram")).json()
    assert data["daily_clean_enabled"] is False


async def test_non_bool_rejected(isolated):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/deck/social/telegram", json={"daily_clean_enabled": "yes"})
        assert resp.status == 400
    assert _fresh("telegram.daily_clean.enabled") is None


async def test_hour_and_kept_kinds_round_trip(isolated):
    async with TestClient(TestServer(_app())) as client:
        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["daily_clean_keep_kinds"] == []
        ids = [k["id"] for k in data["daily_clean_kinds"]]
        assert "greeting" in ids and "spark" in ids

        resp = await client.post("/api/deck/social/telegram",
                                 json={"daily_clean_hour": 6,
                                       "daily_clean_keep_kinds": ["greeting", "greeting"]})
        assert resp.status == 200

        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["daily_clean_hour"] == 6
        assert data["daily_clean_keep_kinds"] == ["greeting"]
        kept = {k["id"]: k["kept"] for k in data["daily_clean_kinds"]}
        assert kept["greeting"] is True and kept["spark"] is False
    assert _fresh("telegram.daily_clean.keep_kinds") == ["greeting"]


@pytest.mark.parametrize("body", [
    {"daily_clean_hour": 24}, {"daily_clean_hour": True}, {"daily_clean_hour": "6"},
    {"daily_clean_keep_kinds": ["nope"]}, {"daily_clean_keep_kinds": "greeting"},
])
async def test_bad_tidy_values_are_refused(isolated, body):
    async with TestClient(TestServer(_app())) as client:
        out = await (await client.post("/api/deck/social/telegram", json=body)).json()
        assert out.get("errors"), out
    assert _fresh("telegram.daily_clean.keep_kinds") is None


async def test_system_cleanup_defaults_on_and_round_trips(isolated):
    """The system-card switch is ON by default — GET must say so (the generic
    nested-bool loop coerces an ABSENT key to False, so the payload overrides it
    from chat_tidy.settings), and POST must flip it off and on again."""
    from navig.telegram import chat_tidy

    async with TestClient(TestServer(_app())) as client:
        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["system_cleanup_enabled"] is True, "absent config means ON"
        assert data["system_cleanup_hour"] == chat_tidy.DEFAULT_HOUR

        resp = await client.post(
            "/api/deck/social/telegram", json={"system_cleanup_enabled": False}
        )
        body = await resp.json()
        assert resp.status == 200 and body["updated"] == ["system_cleanup_enabled"]
        assert _fresh("telegram.system_cleanup.enabled") is False  # a real bool
        assert chat_tidy.settings(fresh=True)["system_enabled"] is False

        data = await (await client.get("/api/deck/social/telegram")).json()
        assert data["system_cleanup_enabled"] is False

        await client.post("/api/deck/social/telegram", json={"system_cleanup_enabled": True})
        assert _fresh("telegram.system_cleanup.enabled") is True

        # the hour round-trips and refuses nonsense
        resp = await client.post(
            "/api/deck/social/telegram", json={"system_cleanup_hour": 6}
        )
        assert (await resp.json())["updated"] == ["system_cleanup_hour"]
        assert chat_tidy.settings(fresh=True)["system_hour"] == 6
        out = await (
            await client.post("/api/deck/social/telegram", json={"system_cleanup_hour": 99})
        ).json()
        assert out.get("errors"), out
