"""NAVIG Dock bridge — pairing, the extension-origin auth fix, and the call relay.

Drives the real deck middleware + routes over aiohttp's TestClient (which connects from
127.0.0.1, the desktop-bypass path) with a scripted fake Dock on the WebSocket.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

EXT_A = "a" * 32
EXT_B = "b" * 32
ADMIN = "admin-token-for-tests"
BEARER = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.dock.hub import reset_bridge

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    reset_bridge()
    yield tmp_path
    reset_bridge()
    reset_config_manager()
    ConfigSingleton._instance = None


def _origin(ext: str) -> dict[str, str]:
    return {"Origin": f"chrome-extension://{ext}"}


def _app() -> web.Application:
    from navig.gateway.deck import register_deck_routes
    from navig.gateway.routes import dock as dock_routes

    app = web.Application()
    register_deck_routes(app, bot_token="")
    dock_routes.register(app, SimpleNamespace(config=SimpleNamespace(auth_token=ADMIN)))
    return app


# ── pairing store ────────────────────────────────────────────────────────────


def test_pair_is_trust_on_first_use_and_verifies_token(isolated_config):
    from navig.dock import pairing

    assert pairing.load() == ()
    token = pairing.pair(EXT_A, "NAVIG Dock")
    assert pairing.verify(EXT_A, token)
    assert not pairing.verify(EXT_A, token + "x")
    assert not pairing.verify(EXT_B, token)
    assert token not in pairing.pairing_path().read_text(encoding="utf-8"), "only the hash is stored"


def test_second_extension_cannot_take_over_pairing(isolated_config):
    from navig.dock import pairing

    pairing.pair(EXT_A)
    with pytest.raises(pairing.PairingConflict):
        pairing.pair(EXT_B)


def test_repairing_same_extension_rotates_token(isolated_config):
    from navig.dock import pairing

    old = pairing.pair(EXT_A)
    new = pairing.pair(EXT_A)
    assert new != old and pairing.verify(EXT_A, new) and not pairing.verify(EXT_A, old)


def test_unreadable_pairing_file_fails_closed_and_is_never_overwritten(isolated_config):
    from navig.dock import pairing

    path = pairing.pairing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert pairing.load() is None
    assert not pairing.extension_origin_trusted(f"chrome-extension://{EXT_A}")
    # pair() quarantines the corrupt bytes rather than trusting them; unpair resets.
    assert pairing.unpair(None) == 0
    assert pairing.load() == ()


def test_extension_origin_policy(isolated_config):
    from navig.dock import pairing

    a, b = f"chrome-extension://{EXT_A}", f"chrome-extension://{EXT_B}"
    assert pairing.extension_origin_trusted(a) and pairing.extension_origin_trusted(b)  # legacy until paired
    pairing.pair(EXT_A)
    assert pairing.extension_origin_trusted(a)
    assert not pairing.extension_origin_trusted(b)
    assert pairing.extension_origin_trusted(b, pairing.PAIR_PATH), "the pair route stays reachable"
    assert not pairing.extension_origin_trusted("https://evil.example")


# ── the auth fix, through the real deck middleware ──────────────────────────


async def test_unpaired_extension_is_refused_once_the_dock_has_paired(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        assert (await client.get("/api/deck/status", headers=_origin(EXT_B))).status == 200  # legacy
        res = await client.post("/api/deck/dock/pair", json={"name": "NAVIG Dock"}, headers=_origin(EXT_A))
        assert res.status == 200
        assert (await res.json())["extension_id"] == EXT_A
        assert (await client.get("/api/deck/status", headers=_origin(EXT_A))).status == 200
        assert (await client.get("/api/deck/status", headers=_origin(EXT_B))).status == 401
        assert (await client.get("/api/deck/status")).status == 200, "the OS app (no Origin) is unaffected"
        res = await client.post("/api/deck/dock/pair", json={}, headers=_origin(EXT_B))
        assert res.status == 409
        assert (await client.post("/api/deck/dock/pair", json={})).status == 403, "only an extension can pair"


# ── the bridge ───────────────────────────────────────────────────────────────


class FakeDock:
    """A scripted Dock: answers each call with ``replies[tool](args)`` (or never)."""

    def __init__(self, ws, replies):
        self.ws, self.replies = ws, replies
        self.calls: list[dict] = []
        self.cancels: list[str] = []
        self.task = asyncio.ensure_future(self._run())

    async def _run(self):
        async for msg in self.ws:
            if msg.type != WSMsgType.TEXT:
                continue
            frame = json.loads(msg.data)
            if frame["t"] == "cancel":
                self.cancels.append(frame["id"])
            elif frame["t"] == "call":
                self.calls.append(frame)
                fn = self.replies.get(frame["tool"])
                if fn is not None:
                    await self.ws.send_str(json.dumps({"t": "result", "id": frame["id"], **fn(frame["args"])}))


async def _connect(client, token, ext=EXT_A):
    ws = await client.ws_connect("/api/deck/dock/bridge", headers=_origin(ext))
    await ws.send_str(json.dumps({"t": "hello", "proto": 1, "token": token, "version": "test"}))
    return ws


async def _paired(client) -> str:
    res = await client.post("/api/deck/dock/pair", json={}, headers=_origin(EXT_A))
    return (await res.json())["token"]


async def _call(client, tool, args=None, deadline_ms=5000, headers=BEARER):
    res = await client.post("/dock/call", json={"tool": tool, "args": args or {}, "deadline_ms": deadline_ms},
                            headers=headers)
    return res.status, (await res.json()).get("data")


async def test_wrong_token_is_closed_unauthorized(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        await _paired(client)
        ws = await _connect(client, "not-the-token")
        msg = await ws.receive(timeout=5)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
        assert ws.close_code == 4003


async def test_call_round_trip_and_status(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws = await _connect(client, token)
        welcome = json.loads((await ws.receive(timeout=5)).data)
        assert welcome["t"] == "welcome" and welcome["proto"] == 1
        dock = FakeDock(ws, {"tabs": lambda a: {"ok": True, "data": {"tabs": [{"tab_id": 7}]}}})

        status, body = await _call(client, "tabs")
        assert status == 200 and body["ok"] is True
        assert body["data"] == {"tabs": [{"tab_id": 7}]}
        assert "hub_ms" in body["timing"]

        res = await client.get("/dock/status", headers=BEARER)
        assert (await res.json())["data"]["connected"] is True
        assert (await client.post("/dock/call", json={"tool": "tabs"})).status == 401, "bearer required"
        await ws.close()
        dock.task.cancel()


async def test_invalid_args_never_reach_the_dock(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws = await _connect(client, token)
        await ws.receive(timeout=5)
        dock = FakeDock(ws, {})
        _, body = await _call(client, "act", {"tab_id": 1, "actions": [{"action": "done"}]})
        assert body["ok"] is False and body["error"]["category"] == "invalid"
        _, body = await _call(client, "act", {"tab_id": 1, "actions": [{"action": "click", "ref": "nope"}]})
        assert body["error"]["category"] == "invalid"
        assert dock.calls == []
        await ws.close()
        dock.task.cancel()


async def test_deadline_sends_cancel_and_marks_mutation_uncertain(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws = await _connect(client, token)
        await ws.receive(timeout=5)
        dock = FakeDock(ws, {})  # never answers
        _, body = await _call(client, "act", {"tab_id": 1, "actions": [{"action": "click", "ref": "ref_1"}]},
                              deadline_ms=1000)
        assert body["error"]["category"] == "timeout"
        assert body["error"]["effects"] == "uncertain"
        await asyncio.sleep(0.1)
        assert dock.cancels == [dock.calls[0]["id"]]
        await ws.close()
        dock.task.cancel()


async def test_disconnect_mid_call_fails_without_replay(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws = await _connect(client, token)
        await ws.receive(timeout=5)
        dock = FakeDock(ws, {})
        call = asyncio.ensure_future(_call(client, "act", {"tab_id": 1, "actions": [{"action": "key", "keys": "Enter"}]}))
        for _ in range(50):
            if dock.calls:
                break
            await asyncio.sleep(0.02)
        await ws.close()
        _, body = await call
        assert body["error"]["category"] == "disconnected"
        assert body["error"]["effects"] == "uncertain"
        # A fresh Dock connection does not see the old call again.
        ws2 = await _connect(client, token)
        await ws2.receive(timeout=5)
        dock2 = FakeDock(ws2, {})
        await asyncio.sleep(0.2)
        assert dock2.calls == []
        await ws2.close()
        dock.task.cancel()
        dock2.task.cancel()


async def test_newer_dock_supersedes_older(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws1 = await _connect(client, token)
        await ws1.receive(timeout=5)
        ws2 = await _connect(client, token)
        await ws2.receive(timeout=5)
        msg = await ws1.receive(timeout=5)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
        assert ws1.close_code == 4002
        await ws2.close()


async def test_user_stop_pauses_agent_calls(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        token = await _paired(client)
        ws = await _connect(client, token)
        await ws.receive(timeout=5)
        dock = FakeDock(ws, {"tabs": lambda a: {"ok": True, "data": {"tabs": []}}})
        await ws.send_str(json.dumps({"t": "event", "kind": "stopped"}))
        await asyncio.sleep(0.1)
        _, body = await _call(client, "tabs")
        assert body["error"]["category"] == "unavailable"
        assert "Stop" in body["error"]["message"]
        await ws.send_str(json.dumps({"t": "event", "kind": "resumed"}))
        await asyncio.sleep(0.1)
        _, body = await _call(client, "tabs")
        assert body["ok"] is True
        await ws.close()
        dock.task.cancel()


async def test_no_dock_connected_is_a_clear_error(isolated_config):
    async with TestClient(TestServer(_app())) as client:
        _, body = await _call(client, "tabs")
        assert body["ok"] is False and body["error"]["category"] == "unavailable"
        assert "Let AI agents use this browser" in body["error"]["message"]
