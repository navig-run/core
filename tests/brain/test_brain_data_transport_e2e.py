"""End to end: a memory call on a linked client lands in the Brain's store.

The other routing tests replace the Brain with a stub server. This one runs the real thing
on loopback: the real deck auth middleware, a real per-machine device token from the OS
device store, the real ``brain_data`` routes and the real MCP implementation writing a real
key-fact store.

That combination is the contract the feature actually promises — "the fact you remember on
the NEURON is the fact the Outpost answers". A stub proves the URL shape; only this proves
the bearer contract, the route registration, the envelope and the storage all line up.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from navig.brain.client import BrainClient, BrainUnavailable  # noqa: E402
from navig.brain.link import BrainLink, write_brain_link  # noqa: E402
from navig.gateway.deck import auth as deck_auth  # noqa: E402
from navig.gateway.deck.routes import brain_data  # noqa: E402
from navig.mcp import capability  # noqa: E402
from navig.mcp.tools import memory as memory_tools  # noqa: E402
from navig.store.os_devices import OsDeviceStore, reset_os_device_store  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def brain_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This install is the Brain: its own config dir, data dir and role."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    (config_dir / "config.yaml").write_text("brain:\n  role: brain\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NAVIG_BRAIN_LINK_FILE", str(tmp_path / "no-link.json"))
    # A loopback request would otherwise ride the desktop bypass and prove nothing about
    # the device token. Turn the bypass off so the ONLY way in is the bearer.
    monkeypatch.setattr(deck_auth, "_local_desktop_bypass", lambda request: False)
    reset_os_device_store()
    capability.reload()
    return tmp_path


def _brain_device() -> tuple[str, str]:
    return OsDeviceStore().add_device(name="neuron", kind="brain")


def _app() -> web.Application:
    app = web.Application(middlewares=[deck_auth.deck_auth_middleware])
    brain_data.register(app)
    return app


def _link_to(url: str, token: str) -> BrainLink:
    link = BrainLink(
        url=url,
        brain_name="outpost-server",
        device_id="83d011d16b16",
        token=token,
        linked_at=0,
        version=1,
    )
    write_brain_link(link)
    capability.reload()
    return link


def _client_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """This process is now the linked NEURON, not the Brain."""
    monkeypatch.setattr(capability, "install_role", lambda use_cache=True: "client")
    capability.reload()


async def test_memory_written_on_the_client_is_readable_on_the_brain(
    brain_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert capability.install_role() == "brain", "the fixture must configure a Brain"
    _device_id, token = _brain_device()

    async with TestClient(TestServer(_app())) as server:
        base = str(server.make_url("/")).rstrip("/")

        # The Brain serves its own rows.
        own = await server.post(
            f"{brain_data.PREFIX}/memory/stats",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert own.status == 200, await own.text()

        _client_pass(monkeypatch)
        link = _link_to(base, token)
        assert link.token not in repr(link)
        route = capability.resolve("memory_key_facts_remember")
        assert route.is_brain, route.describe()

        # The client is blocking on purpose (MCP handlers are sync, and on a real
        # NEURON they run in another process); here it must leave this loop free for
        # the in-process Brain to answer.
        written = await asyncio.to_thread(
            route.client.memory_remember,
            "The Outpost owns NAVIG's Brain memory.",
            source="e2e",
        )
        assert written.get("error") is None, written

    # Read it back through the Brain's own implementation: the store an Outpost
    # session sees. The client never opened its own database.
    found = memory_tools.RAW_TOOLS["memory_key_facts_retrieve"](
        None, {"query": "Outpost Brain memory", "limit": 5}
    )
    assert "Outpost owns NAVIG" in json.dumps(found), found


async def test_a_phone_token_cannot_read_brain_data(brain_home: Path) -> None:
    _device_id, remote_token = OsDeviceStore().add_device(name="phone", kind="remote")
    async with TestClient(TestServer(_app())) as server:
        response = await server.post(
            f"{brain_data.PREFIX}/memory/stats",
            json={},
            headers={"Authorization": f"Bearer {remote_token}"},
        )
    assert response.status == 401, await response.text()


async def test_a_client_host_refuses_to_serve_brain_data(brain_home: Path) -> None:
    """Two linked machines must not bounce a call between them."""
    _device_id, token = _brain_device()
    original = brain_data._authority_role
    brain_data._authority_role = lambda: "client"  # type: ignore[assignment]
    try:
        async with TestClient(TestServer(_app())) as server:
            response = await server.post(
                f"{brain_data.PREFIX}/memory/stats",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = await response.json()
            status = response.status
    finally:
        brain_data._authority_role = original  # type: ignore[assignment]

    assert status == 409, body
    assert body["code"] == "not_authority"


async def test_client_without_a_brain_link_refuses_instead_of_writing_locally(
    brain_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client_pass(monkeypatch)
    route = capability.resolve("memory_key_facts_remember")
    assert route.is_refused, route.describe()

    payload = capability.failure_payload(
        BrainUnavailable("no brain", reason="no_link"),
        capability.capability_for("memory_key_facts_remember"),
    )
    assert payload["isError"] is True
    assert payload["wrote"] is False


async def test_an_unreachable_brain_raises_without_writing(
    brain_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client_pass(monkeypatch)
    _link_to("http://127.0.0.1:9", "t" * 40)
    route = capability.resolve("memory_key_facts_remember")
    assert route.is_brain, route.describe()

    with pytest.raises(BrainUnavailable) as caught:
        route.client.memory_remember({"fact": "must not be stored locally"})
    assert caught.value.wrote is False
    assert caught.value.reason in {"unreachable", "timeout"}


async def test_an_old_brain_is_reported_as_needing_an_upgrade(
    brain_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing(request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "no route"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", missing)
    async with TestClient(TestServer(app)) as server:
        _client_pass(monkeypatch)
        _link_to(str(server.make_url("/")).rstrip("/"), "t" * 40)
        link = BrainLink(
            url=str(server.make_url("/")).rstrip("/"),
            brain_name="old",
            device_id="x",
            token="t" * 40,
            linked_at=0,
            version=1,
        )
        with pytest.raises(BrainUnavailable) as caught:
            await asyncio.to_thread(BrainClient(link).memory_stats)
    assert caught.value.reason == "brain_not_upgraded"
    assert "upgrade the brain" in str(caught.value).lower()
