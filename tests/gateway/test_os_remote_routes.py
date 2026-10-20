"""Remote NAVIG OS routes: who may pair, who may revoke, and what a code is worth.

The request that matters most is the one that must FAIL: minting a pairing code from
anywhere but the computer itself. Deck auth alone would admit the deck key and allowed
Telegram users, and letting either mint a code would make the deck key grant OS access
after all — the exact coupling per-device keys exist to break. A request relayed through
Lighthouse carries X-Navig-Via, which is how "remote" is simulated here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.auth import RELAY_VIA_HEADER
from navig.gateway.deck.routes import os_remote
from navig.store.os_devices import OsDeviceStore

TENANT = "a" * 64
REMOTE = {RELAY_VIA_HEADER: "lighthouse"}


class FakeRelay:
    def __init__(self) -> None:
        self.enabled = True
        self.daily_msg_budget = 1000
        self.killed: list[str] = []

    def snapshot(self) -> dict:
        return {"sessions": 0, "devices_connected": []}

    def kill_device(self, device_id: str) -> int:
        self.killed.append(device_id)
        return 1

    def set_enabled(self, enabled: bool) -> int:
        self.enabled = enabled
        return 0


class FakeUplink:
    def __init__(self, status: str = "online") -> None:
        self.lighthouse_url = "https://edge.example.workers.dev"
        self.tenant = TENANT
        self.status = status
        self.os_relay = FakeRelay()
        self.retired = False

    async def send_retire(self) -> bool:
        self.retired = True
        return True


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, kind: str, payload: dict) -> None:
        self.emitted.append((kind, payload))


class FakeGateway:
    def __init__(self, uplink) -> None:
        self.cloud_manager = type("CM", (), {"_uplink": uplink})()
        self.system_events = FakeEvents()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = OsDeviceStore(tmp_path / "os_remote.db")
    monkeypatch.setattr(os_remote, "_store", lambda: s)
    os_remote._redeem_times.clear()
    os_remote._edge_version_cache.clear()
    yield s
    s.close()


@pytest.fixture()
def edge_version(monkeypatch):
    holder = {"v": "1.3.0"}

    async def _v(_url):
        return holder["v"]

    monkeypatch.setattr(os_remote, "_edge_version", _v)
    return holder


async def client_for(uplink) -> tuple[TestClient, FakeGateway]:
    app = web.Application()
    gw = FakeGateway(uplink)
    app["gateway"] = gw
    os_remote.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, gw


class TestMinting:
    async def test_a_relayed_request_can_never_mint_a_code(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            res = await client.post("/api/deck/os/pair", headers=REMOTE)
            assert res.status == 403
            assert (await res.json())["error"] == "local_only"
        finally:
            await client.close()

    async def test_the_computer_itself_gets_a_link_with_everything_in_the_fragment(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            res = await client.post("/api/deck/os/pair")
            assert res.status == 200
            body = await res.json()
            parts = urlsplit(body["link"])
            assert (parts.scheme, parts.netloc, parts.query) == ("https", "os.navig.run", "")
            frag = {k: v[0] for k, v in parse_qs(parts.fragment).items()}
            assert frag == {"pair": "1", "v": "1", "e": "https://edge.example.workers.dev", "t": TENANT, "c": body["code"]}
            assert store.take_pair_code(body["code"]) is True
        finally:
            await client.close()

    @pytest.mark.parametrize(
        ("uplink", "version", "error"),
        [(None, "1.3.0", "relay_missing"), ("offline", "1.3.0", "relay_offline"), ("online", "1.2.1", "edge_outdated")],
    )
    async def test_minting_says_what_is_missing(self, store, edge_version, uplink, version, error) -> None:
        edge_version["v"] = version
        client, _ = await client_for(FakeUplink(uplink) if uplink else None)
        try:
            res = await client.post("/api/deck/os/pair")
            assert res.status == 409
            body = await res.json()
            assert body["error"] == error
            assert body["message"], "every refusal carries a sentence the desktop can show"
        finally:
            await client.close()

    async def test_closing_the_qr_dialog_kills_its_code(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            code = (await (await client.post("/api/deck/os/pair")).json())["code"]
            assert (await client.post("/api/deck/os/pair/cancel", json={"code": code})).status == 200
            assert store.take_pair_code(code) is False
        finally:
            await client.close()


class TestRedeem:
    async def test_a_code_buys_exactly_one_device(self, store, edge_version) -> None:
        client, gw = await client_for(FakeUplink())
        try:
            code, _ = store.mint_pair_code()
            ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
            res = await client.post("/api/os/pair/redeem", json={"code": code}, headers={"User-Agent": ua, **REMOTE})
            assert res.status == 200
            body = await res.json()
            assert store.device_for_token(body["deviceToken"])["id"] == body["deviceId"]
            assert body["name"] == "Safari on iPhone"
            assert gw.system_events.emitted == [("os.device.paired", {"id": body["deviceId"], "name": "Safari on iPhone"})]

            again = await client.post("/api/os/pair/redeem", json={"code": code}, headers=REMOTE)
            assert again.status == 400
            assert (await again.json())["error"] == "invalid_code"
        finally:
            await client.close()

    async def test_only_json_is_accepted(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            code, _ = store.mint_pair_code()
            res = await client.post("/api/os/pair/redeem", data=f"code={code}", headers=REMOTE)
            assert res.status == 415
            assert store.take_pair_code(code) is True, "a refused form post must not burn the code"
        finally:
            await client.close()

    async def test_redeem_is_rate_limited_not_locked_out(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            statuses = [
                (await client.post("/api/os/pair/redeem", json={"code": "guess"}, headers=REMOTE)).status
                for _ in range(12)
            ]
            assert statuses[:10] == [400] * 10
            assert statuses[10:] == [429, 429]
        finally:
            await client.close()

    async def test_a_disabled_computer_pairs_nothing(self, store, edge_version) -> None:
        uplink = FakeUplink()
        uplink.os_relay.enabled = False
        client, _ = await client_for(uplink)
        try:
            code, _ = store.mint_pair_code()
            res = await client.post("/api/os/pair/redeem", json={"code": code}, headers=REMOTE)
            assert res.status == 403
            assert store.take_pair_code(code) is True, "a refusal must not consume the code"
        finally:
            await client.close()


class TestDevices:
    async def test_a_lost_phone_can_be_revoked_remotely(self, store, edge_version) -> None:
        uplink = FakeUplink()
        client, _ = await client_for(uplink)
        try:
            device_id, token = store.add_device(name="lost phone")
            res = await client.delete(f"/api/deck/os/devices/{device_id}", headers=REMOTE)
            assert res.status == 200
            assert (await res.json())["revoked"] is True
            assert store.device_for_token(token) is None
            assert uplink.os_relay.killed == [device_id], "its live sessions must end too"
            assert (await client.delete("/api/deck/os/devices/nope")).status == 404
        finally:
            await client.close()

    async def test_the_device_list_never_contains_a_token(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            _, token = store.add_device(name="phone")
            res = await client.get("/api/deck/os/devices")
            text = await res.text()
            assert res.status == 200 and token not in text and "token_hash" not in text
        finally:
            await client.close()


class TestSwitchAndRetire:
    async def test_the_kill_switch_is_local_only(self, store, edge_version, monkeypatch) -> None:
        written: list[tuple[str, object]] = []

        class _CM:
            def set_global(self, key, value):
                written.append((key, value))

        monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
        uplink = FakeUplink()
        client, _ = await client_for(uplink)
        try:
            assert (await client.patch("/api/deck/os/remote", json={"enabled": False}, headers=REMOTE)).status == 403
            assert uplink.os_relay.enabled is True
            res = await client.patch("/api/deck/os/remote", json={"enabled": "false"})
            assert res.status == 200
            assert uplink.os_relay.enabled is False, "the string 'false' must switch it OFF"
            assert written == [("os.remote.enabled", False)]
        finally:
            await client.close()

    async def test_retire_is_local_only(self, store, edge_version) -> None:
        uplink = FakeUplink()
        client, _ = await client_for(uplink)
        try:
            assert (await client.post("/api/deck/os/retire", headers=REMOTE)).status == 403
            assert uplink.retired is False
            assert (await (await client.post("/api/deck/os/retire")).json()) == {"retired": True}
        finally:
            await client.close()

    async def test_status_reports_relay_readiness(self, store, edge_version, monkeypatch) -> None:
        monkeypatch.setattr("navig.cloud.os_discovery.read_descriptor", lambda *a, **k: None)
        client, _ = await client_for(FakeUplink())
        try:
            body = await (await client.get("/api/deck/os/remote")).json()
            assert body["relay"]["state"] == "ready"
            assert body["os_server"]["running"] is False
            assert body["usage_today"]["budget_messages"] == 1000
        finally:
            await client.close()
