"""OsRelay — the brain's half of NAVIG OS over Lighthouse.

A real aiohttp WebSocket server stands in for the NAVIG OS server, and two lists stand in
for the edge (control frames and data). What is pinned:

  * the device token is checked and SWAPPED for the OS server's own token, which never
    leaves the PC; the upgrade carries the relay/device headers;
  * every refusal carries the close code the hosted page turns into a sentence;
  * batching and fragmentation are lossless and keep order;
  * revoke, the kill switch and the daily budget end sessions.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("aiohttp")

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from navig.cloud import os_relay as relay_mod
from navig.cloud.os_relay import (
    BATCH_SEP,
    FRAGMENT_BYTES,
    OsRelay,
    Reassembler,
    RelayFramingError,
    fragment,
)
from navig.store.os_devices import OsDeviceStore

SID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OS_TOKEN = "os-server-token-0123456789abcdef"


class FakeOs:
    """A NAVIG OS server: acks the handshake, then does whatever the test scripts."""

    def __init__(self) -> None:
        self.handshakes: list[dict] = []
        self.headers: list[dict] = []
        self.received: list[str] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.on_ready: asyncio.Event = asyncio.Event()

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        self.headers.append(dict(request.headers))
        self.sockets.append(ws)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            if data.get("type") == "handshake":
                self.handshakes.append(data)
                if data.get("token") != OS_TOKEN:
                    await ws.close(code=4005, message=b"Auth failed")
                    break
                await ws.send_str(json.dumps({"type": "handshake_ack", "clientId": "c1"}))
                self.on_ready.set()
            else:
                self.received.append(msg.data)
        return ws


class Edge:
    def __init__(self) -> None:
        self.control: list[dict] = []
        self.data: list[str] = []

    async def send_control(self, frame: dict) -> None:
        self.control.append(frame)

    async def send_data(self, sid: str, payload: str) -> None:
        assert sid == SID
        self.data.append(payload)

    def closes(self) -> list[dict]:
        return [f for f in self.control if f.get("t") == "os_close"]

    def envelopes(self) -> list[str]:
        out: list[str] = []
        reasm = Reassembler()
        for payload in self.data:
            out.extend(reasm.feed(payload))
        return out


@pytest.fixture()
async def fake_os():
    os_srv = FakeOs()
    app = web.Application()
    app.router.add_get("/", os_srv.handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    os_srv.url = f"ws://127.0.0.1:{server.port}/"
    yield os_srv
    await server.close()


@pytest.fixture()
def store(tmp_path):
    s = OsDeviceStore(tmp_path / "os_remote.db")
    yield s
    s.close()


def make_relay(edge: Edge, store, fake_os=None, **kw) -> OsRelay:
    descriptor = {"ws_url": fake_os.url, "token": OS_TOKEN} if fake_os is not None else None
    return OsRelay(
        send_control=edge.send_control,
        send_data=edge.send_data,
        store_fn=lambda: store,
        descriptor_fn=lambda: descriptor,
        **kw,
    )


def handshake(token: str) -> str:
    return json.dumps({"type": "handshake", "id": "h1", "protocolVersion": "1.0", "token": token,
                       "workspaceId": "w1", "reconnectClientId": "rc", "lastSeq": 7})


async def settle(cond, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def open_session(relay: OsRelay, token: str) -> None:
    relay.on_open({"t": "os_open", "sid": SID, "ua": "test"})
    relay.on_data(SID, handshake(token))


class TestHandshake:
    async def test_device_token_is_swapped_for_the_os_token(self, fake_os, store) -> None:
        device_id, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)

        hs = fake_os.handshakes[0]
        assert hs["token"] == OS_TOKEN, "the OS server must see its own token"
        assert token not in json.dumps(fake_os.handshakes), "the device token must never reach it"
        # Everything else passes through, so OS reconnect replay keeps working.
        assert (hs["workspaceId"], hs["reconnectClientId"], hs["lastSeq"]) == ("w1", "rc", 7)
        assert fake_os.headers[0]["X-Navig-Via"] == "relay"
        assert fake_os.headers[0]["X-Navig-Remote-Device"] == device_id
        assert {"t": "os_ok", "sid": SID, "deviceId": device_id} in edge.control
        await settle(lambda: any("handshake_ack" in e for e in edge.envelopes()))
        await relay.close_all()

    async def test_an_unknown_device_never_reaches_the_os_server(self, fake_os, store) -> None:
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, "not-a-paired-device")
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4401
        assert fake_os.handshakes == []

    async def test_a_revoked_device_is_refused(self, fake_os, store) -> None:
        device_id, token = store.add_device(name="lost")
        store.revoke(device_id)
        edge = Edge()
        await open_session(make_relay(edge, store, fake_os), token)
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4401

    async def test_no_os_server_running_says_so(self, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        await open_session(make_relay(edge, store, None), token)
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4410

    async def test_a_silent_socket_is_dropped(self, store, monkeypatch) -> None:
        monkeypatch.setattr(relay_mod, "HANDSHAKE_TIMEOUT_S", 0.05)
        edge = Edge()
        relay = make_relay(edge, store, None)
        relay.on_open({"t": "os_open", "sid": SID})
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4401

    async def test_a_non_handshake_first_message_is_refused(self, fake_os, store) -> None:
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        relay.on_open({"t": "os_open", "sid": SID})
        relay.on_data(SID, json.dumps({"type": "request", "token": "x"}))
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4401

    async def test_the_os_restarting_with_a_new_token_is_transient(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = OsRelay(
            send_control=edge.send_control, send_data=edge.send_data, store_fn=lambda: store,
            descriptor_fn=lambda: {"ws_url": fake_os.url, "token": "stale-token-from-last-launch"},
        )
        await open_session(relay, token)
        await settle(lambda: edge.closes())
        # 4410 is "not running / restarting": the page reconnects instead of forgetting the device.
        assert edge.closes()[0]["code"] == 4410


class TestStreaming:
    async def test_small_messages_are_batched_and_arrive_in_order(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        sent = [json.dumps({"type": "event", "seq": i}) for i in range(200)]
        for m in sent:
            await fake_os.sockets[0].send_str(m)
        await settle(lambda: sum(1 for e in edge.envelopes() if '"event"' in e) == 200)
        events = [e for e in edge.envelopes() if '"event"' in e]
        assert events == sent
        assert len(edge.data) < 50, f"200 events should need far fewer uplink messages, got {len(edge.data)}"
        await relay.close_all()

    async def test_a_big_message_is_fragmented_and_reassembled_exactly(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        big = json.dumps({"type": "response", "result": "x" * (FRAGMENT_BYTES * 2 + 123)})
        await fake_os.sockets[0].send_str(big)
        await settle(lambda: big in edge.envelopes())
        assert all(len(p) <= FRAGMENT_BYTES + 64 for p in edge.data), "no uplink message exceeds a fragment"
        await relay.close_all()

    async def test_browser_messages_including_fragments_reach_the_os(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        small = json.dumps({"type": "request", "id": "1", "channel": "a"})
        big = json.dumps({"type": "request", "id": "2", "channel": "transfer:chunk", "data": "y" * 700_000})
        relay.on_data(SID, small)
        for part in fragment(big):
            relay.on_data(SID, part)
        await settle(lambda: len(fake_os.received) == 2)
        assert fake_os.received == [small, big]
        await relay.close_all()


class TestEnding:
    async def test_revoking_ends_a_live_session(self, fake_os, store) -> None:
        device_id, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        assert relay.kill_device(device_id) == 1
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4401
        await settle(lambda: fake_os.sockets[0].closed)

    async def test_the_kill_switch_ends_sessions_and_refuses_new_ones(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        assert relay.set_enabled(False) == 1
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4403
        edge2 = Edge()
        relay2 = make_relay(edge2, store, fake_os, enabled=False)
        await open_session(relay2, token)
        await settle(lambda: edge2.closes())
        assert edge2.closes()[0]["code"] == 4403

    async def test_the_budget_refuses_new_sessions_and_says_so_once(self, fake_os, store) -> None:
        device_id, token = store.add_device(name="phone")
        store.add_usage(device_id, msgs_in=600, msgs_out=600)
        noted: list[dict] = []
        edge = Edge()
        relay = make_relay(edge, store, fake_os, daily_msg_budget=1000, on_budget_exceeded=noted.append)
        await open_session(relay, token)
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4429
        relay.on_open({"t": "os_open", "sid": "1f8fad5b-d9cb-469f-a165-70867728950e"})
        await asyncio.sleep(0.1)
        assert len(noted) == 1, "the incident is recorded once per day, not per refusal"

    async def test_a_browser_leaving_closes_the_os_socket_without_an_echo(self, fake_os, store) -> None:
        _, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        relay.on_close(SID, 1000, "bye")
        await settle(lambda: fake_os.sockets[0].closed)
        await asyncio.sleep(0.05)
        assert edge.closes() == [], "the edge already knows; telling it again is noise"

    async def test_usage_is_metered_to_the_store(self, fake_os, store) -> None:
        device_id, token = store.add_device(name="phone")
        edge = Edge()
        relay = make_relay(edge, store, fake_os)
        await open_session(relay, token)
        await asyncio.wait_for(fake_os.on_ready.wait(), 3)
        await settle(lambda: edge.data)
        relay.on_close(SID, 1000, "")
        await settle(lambda: store.usage_today()["msgs_in"] >= 1)
        usage = store.usage_today()
        assert usage["msgs_in"] >= 1 and usage["msgs_out"] >= 1

    async def test_a_flooding_browser_is_cut_off_not_buffered_forever(self, store, monkeypatch) -> None:
        monkeypatch.setattr(relay_mod, "INBOX_MAX", 4)
        edge = Edge()
        relay = make_relay(edge, store, None)
        relay.on_open({"t": "os_open", "sid": SID})
        for _ in range(10):
            relay.on_data(SID, "{}")
        await settle(lambda: edge.closes())
        assert edge.closes()[0]["code"] == 4429


class TestReassembler:
    def test_batches_split_on_the_record_separator(self) -> None:
        assert Reassembler().feed(BATCH_SEP.join(['{"a":1}', '{"b":2}'])) == ['{"a":1}', '{"b":2}']

    def test_out_of_order_fragments_reassemble(self) -> None:
        parts = fragment("z" * 1000, size=100)
        r = Reassembler()
        out: list[str] = []
        for p in reversed(parts):
            out.extend(r.feed(p))
        assert out == ["z" * 1000]

    @pytest.mark.parametrize(
        "payload",
        ["\x1fbroken", "\x1fm|x|2|c", "\x1fm|5|2|c", "\x1f|0|1|c"],
    )
    def test_malformed_fragments_are_refused(self, payload) -> None:
        with pytest.raises(RelayFramingError):
            Reassembler().feed(payload)

    def test_an_oversized_message_is_refused_before_it_is_built(self) -> None:
        r = Reassembler(max_bytes=150)
        with pytest.raises(RelayFramingError):
            for p in fragment("q" * 400, size=100):
                r.feed(p)

    def test_too_many_partial_messages_are_refused(self) -> None:
        r = Reassembler(max_partial=2)
        r.feed(fragment("a" * 200, size=100)[0])
        r.feed(fragment("b" * 200, size=100)[0])
        with pytest.raises(RelayFramingError):
            r.feed(fragment("c" * 200, size=100)[0])
