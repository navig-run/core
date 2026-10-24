"""Contract tests for GET/POST /api/deck/mind.

The route serves the *effective* MIND profile and validates/writes the shared
``mind.*`` config. The artifact-language firewall is read-only and always
reported; any attempt to write it is a 400.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _app() -> web.Application:
    from navig.gateway.deck.routes.mind import handle_mind_get, handle_mind_post

    app = web.Application()
    app.router.add_get("/api/deck/mind", handle_mind_get)
    app.router.add_post("/api/deck/mind", handle_mind_post)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None, raising=False)
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


async def _get(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/deck/mind")
        return resp.status, await resp.json()


async def _post(app, body):
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/deck/mind", json=body)
        return resp.status, await resp.json()


async def test_get_returns_effective_profile_with_firewall(isolated):
    status, data = await _get(_app())
    assert status == 200
    assert data["artifact_language_firewall"] is True
    assert data["creative_language"] == "ru"
    assert data["engineering_language"] == "en"
    assert data["lexicon"]["density"] == "balanced"


async def test_post_writes_and_reads_back(isolated):
    status, data = await _post(
        _app(),
        {"lexicon": {"density": "intensive"}, "digitalghost": {"frequency": "deep"}},
    )
    assert status == 200, data
    assert data["lexicon"]["density"] == "intensive"
    assert data["digitalghost"]["frequency"] == "deep"
    # Persisted — a fresh GET reads the same effective profile.
    status, again = await _get(_app())
    assert again["lexicon"]["density"] == "intensive"
    assert again["digitalghost"]["frequency"] == "deep"


async def test_post_rejects_unknown_key(isolated):
    status, data = await _post(_app(), {"bogus": True})
    assert status == 400
    assert "unknown setting" in data["error"]


async def test_post_rejects_firewall_write(isolated):
    status, data = await _post(_app(), {"artifact_language_firewall": False})
    assert status == 400
    assert "read-only" in data["error"]


async def test_post_rejects_bad_values(isolated):
    status, data = await _post(_app(), {"lexicon": {"density": "nonsense"}})
    assert status == 400
    status, data = await _post(_app(), {"creative_language": "xx"})
    assert status == 400
    status, data = await _post(_app(), {"digitalghost": {"frequency": "nonsense"}})
    assert status == 400


async def test_post_toggles_mind_layer(isolated):
    status, data = await _post(_app(), {"enabled": False})
    assert status == 200
    assert data["enabled"] is False
    status, data = await _post(_app(), {"enabled": True})
    assert data["enabled"] is True
