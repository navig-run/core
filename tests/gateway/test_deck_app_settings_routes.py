"""Contract tests for GET/POST /api/deck/apps/{app_id}/settings.

The generic per-app settings endpoint (desktop OS app options): shallow-merge
writes to the ``apps.<id>.settings`` subtree of the global config, null deletes
a key, unknown apps 404, and every write survives a full in-memory config drop
(the daemon-restart proof). Isolated via NAVIG_CONFIG_DIR + a forced
ConfigSingleton re-init (the singleton caches config_dir at first touch).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes.app_settings import (
    handle_deck_app_settings_get,
    handle_deck_app_settings_post,
)


def _app(gateway=None) -> web.Application:
    app = web.Application()
    if gateway is not None:
        app["gateway"] = gateway
    app.router.add_get("/api/deck/apps/{app_id}/settings", handle_deck_app_settings_get)
    app.router.add_post("/api/deck/apps/{app_id}/settings", handle_deck_app_settings_post)
    return app


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Per-test config isolation.

    The session conftest isolates NAVIG_CONFIG_DIR once, but ConfigSingleton
    caches its paths at first init — re-point the env AND drop the singleton so
    this test's reads/writes hit a fresh tmp config.yaml.
    """
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


async def _get(client: TestClient, path: str):
    res = await client.get(path)
    return res.status, await res.json()


async def _post(client: TestClient, path: str, body: dict):
    res = await client.post(path, json=body)
    return res.status, await res.json()


async def test_unknown_app_404s(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/apps/definitely-not-an-app/settings")
        assert status == 404
        assert body["ok"] is False
        status, body = await _post(
            client, "/api/deck/apps/definitely-not-an-app/settings", {"settings": {"x": 1}}
        )
        assert status == 404


async def test_get_unset_returns_empty(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/apps/remote/settings")
        assert status == 200
        assert body == {"ok": True, "id": "remote", "settings": {}}


async def test_post_merges_and_null_deletes(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"default_host": "vps-1"}}
        )
        assert status == 200
        assert body["settings"] == {"default_host": "vps-1"}

        # Merge keeps existing keys.
        status, body = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"row_limit": 500}}
        )
        assert status == 200
        assert body["settings"] == {"default_host": "vps-1", "row_limit": 500}

        # Null deletes.
        status, body = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"default_host": None}}
        )
        assert status == 200
        assert body["settings"] == {"row_limit": 500}

        status, body = await _get(client, "/api/deck/apps/remote/settings")
        assert body["settings"] == {"row_limit": 500}


async def test_write_lands_on_disk_and_survives_memory_drop(isolated_config):
    """The daemon-restart proof: value present in config.yaml + readable after
    every in-memory config layer is dropped."""
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(
            client, "/api/deck/apps/system/settings", {"settings": {"poll_interval_s": 10}}
        )
        assert status == 200

        cfg_path = ConfigSingleton().global_config_path
        on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        assert on_disk["apps"]["system"]["settings"]["poll_interval_s"] == 10

        reset_config_manager()
        ConfigSingleton._instance = None

        status, body = await _get(client, "/api/deck/apps/system/settings")
        assert status == 200
        assert body["settings"] == {"poll_interval_s": 10}


async def test_validation_rejects_bad_input(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        # Missing / empty settings object.
        status, _ = await _post(client, "/api/deck/apps/remote/settings", {})
        assert status == 400
        status, _ = await _post(client, "/api/deck/apps/remote/settings", {"settings": {}})
        assert status == 400
        # Bad key shapes (dots would escape the subtree; uppercase off-contract).
        for bad_key in ("Weird-Key", "a.b", "x" * 65, ""):
            status, _ = await _post(
                client, "/api/deck/apps/remote/settings", {"settings": {bad_key: 1}}
            )
            assert status == 400, bad_key
        # 'enabled' is reserved for /modules/toggle.
        status, body = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"enabled": False}}
        )
        assert status == 400
        assert "modules/toggle" in body["error"]
        # Non-primitive values.
        status, _ = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"obj": {"nested": 1}}}
        )
        assert status == 400
        # Oversized string / list.
        status, _ = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"s": "x" * 5000}}
        )
        assert status == 400
        status, _ = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"l": list(range(100))}}
        )
        assert status == 400


async def test_key_cap_enforced(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        too_many = {f"k{i}": i for i in range(40)}
        status, body = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": too_many}
        )
        assert status == 400
        assert "too many" in body["error"]


async def test_write_emits_apps_settings_update(isolated_config):
    events: list[tuple[str, dict]] = []

    class _Queue:
        async def emit(self, name, payload):
            events.append((name, payload))

    class _Gateway:
        system_events = _Queue()

    async with TestClient(TestServer(_app(gateway=_Gateway()))) as client:
        status, _ = await _post(
            client, "/api/deck/apps/remote/settings", {"settings": {"default_host": "vps-1"}}
        )
        assert status == 200
    assert events == [
        ("apps_settings_update", {"id": "remote", "settings": {"default_host": "vps-1"}})
    ]
