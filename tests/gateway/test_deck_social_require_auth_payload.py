"""The deck GET payload parses `require_auth` the way the bot enforces it.

Review finding on #2037: the payload copied the RAW config value, so NAVIG OS's
Require-auth switch rendered the CLI's stored string `"false"` as **on** — while
this very PR made the bot honour it as off. The two surfaces then disagreed
about who may talk to the bot, which is the worst direction for a security
switch: the operator sees "protected" over an open bot.

Both directions pin here: explicit off reads as `false`, and an empty value
reads as `true` (unset = sign-in stays on — `coerce_bool("")` alone would say
`False`).
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
    from navig.gateway.deck.routes.social import handle_deck_social_telegram_get

    app = web.Application()
    app.router.add_get("/api/deck/social/telegram", handle_deck_social_telegram_get)
    return app


async def _get_require_auth(isolated):
    client = TestClient(TestServer(_app()))
    await client.start_server()
    try:
        resp = await client.get("/api/deck/social/telegram")
        assert resp.status == 200
        body = await resp.json()
        assert "require_auth" in body, body.keys()
        return body["require_auth"]
    finally:
        await client.close()


async def _set_and_read(raw):
    from navig.config import ConfigManager

    ConfigManager().set_global("telegram.require_auth", raw)
    return await _get_require_auth(None)


async def test_explicit_off_reads_as_false(isolated):
    """`navig config set telegram.require_auth false` must show OFF in NAVIG OS."""
    value = await _set_and_read("false")
    assert value is False, f"raw {value!r} leaked to the switch"


async def test_empty_value_reads_as_true(isolated):
    """An empty value is 'unset': sign-in stays on (coerce_bool('') would say off)."""
    value = await _set_and_read("")
    assert value is True, f"empty value unlocked the switch: {value!r}"


async def test_garbage_and_missing_keep_it_on(isolated):
    assert await _set_and_read("nope") is True
    assert await _set_and_read(True) is True
