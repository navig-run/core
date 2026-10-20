"""Brain link: another NAVIG OS uses this computer as its brain, through the relay.

What must hold, and is tested here as the request that must FAIL as much as the one that
must succeed:

* Only the computer itself mints a link (a relayed request — deck key, Telegram, or a
  linked machine — never can).
* A phone's pairing code is never a brain link, and a link code never pairs a phone;
  likewise their tokens. Pairing a phone must never hand out deck access by accident.
* A brain token opens the deck API relayed — under ``telegram_only`` too — and is cut by
  revocation and by the remote-access switch. It never opens a local-only route.
* The uplink turns ``/os/<tenant>/api/deck/*`` into the deck route ONLY with a Bearer and
  ONLY under ``/api/deck/`` — the tenant path is keyless, so it must not reach the routes
  that skip deck auth, by dot segments or otherwise.
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.cloud.uplink import _rewrite_os_path
from navig.gateway.deck import auth as deck_auth
from navig.gateway.deck.auth import RELAY_VIA_HEADER, deck_auth_middleware
from navig.gateway.deck.routes import os_remote
from navig.store import os_devices
from navig.store.os_devices import OsDeviceStore

TENANT = "b" * 64
EDGE = "https://edge.example.workers.dev"
REMOTE = {RELAY_VIA_HEADER: "lighthouse"}


class FakeRelay:
    def __init__(self) -> None:
        self.enabled = True
        self.daily_msg_budget = 1000

    def snapshot(self) -> dict:
        return {"sessions": 0, "devices_connected": []}


class FakeUplink:
    def __init__(self) -> None:
        self.lighthouse_url = EDGE
        self.tenant = TENANT
        self.status = "online"
        self.os_relay = FakeRelay()


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
    monkeypatch.setattr(os_devices, "get_os_device_store", lambda: s)
    os_remote._redeem_times.clear()
    os_remote._edge_version_cache.clear()
    deck_auth._brain_touched.clear()
    yield s
    s.close()


@pytest.fixture()
def edge_version(monkeypatch):
    holder = {"v": "1.4.0"}

    async def _v(_url):
        return holder["v"]

    monkeypatch.setattr(os_remote, "_edge_version", _v)
    return holder


@pytest.fixture()
def deck_config():
    saved = dict(deck_auth._deck_config)
    deck_auth.configure_deck_auth(bot_token="", allowed_users=[], api_key="the-deck-key-0123456789")
    yield deck_auth._deck_config
    deck_auth._deck_config.clear()
    deck_auth._deck_config.update(saved)


async def _whoami(request: web.Request) -> web.Response:
    return web.json_response(
        {"local": request.get("deck_auth_local"), "brain": request.get("deck_brain_device")}
    )


async def client_for(uplink, *, with_auth: bool = False) -> tuple[TestClient, FakeGateway]:
    app = web.Application(middlewares=[deck_auth_middleware] if with_auth else [])
    gw = FakeGateway(uplink)
    app["gateway"] = gw
    os_remote.register(app)
    app.router.add_get("/api/deck/whoami", _whoami)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, gw


# ── The store keeps the two kinds apart ──────────────────────────────────────


class TestKinds:
    def test_a_code_redeems_only_as_the_kind_it_was_minted_for(self, store) -> None:
        phone, _ = store.mint_pair_code()
        link, _ = store.mint_pair_code(kind="brain")
        assert store.take_pair_code(phone, kind="brain") is False
        assert store.take_pair_code(link) is False
        # The wrong-kind attempt must not have burned either code.
        assert store.take_pair_code(phone) is True
        assert store.take_pair_code(link, kind="brain") is True

    def test_a_token_authenticates_only_as_its_kind(self, store) -> None:
        _, phone_token = store.add_device(name="phone")
        brain_id, brain_token = store.add_device(name="lab", kind="brain")
        assert store.device_for_token(phone_token, kind="brain") is None
        assert store.device_for_token(brain_token) is None, "the OS relay must not accept a brain link"
        assert store.device_for_token(brain_token, kind="brain")["id"] == brain_id

    def test_the_device_list_says_which_kind(self, store) -> None:
        store.add_device(name="phone")
        store.add_device(name="lab", kind="brain")
        kinds = {d["name"]: d["kind"] for d in store.list_devices()}
        assert kinds == {"phone": "remote", "lab": "brain"}

    def test_unknown_kinds_are_refused(self, store) -> None:
        with pytest.raises(ValueError):
            store.mint_pair_code(kind="admin")

    def test_opening_qr_dialogs_never_pushes_out_a_link_code(self, store) -> None:
        link, _ = store.mint_pair_code(kind="brain")
        for _ in range(os_devices.MAX_OPEN_CODES + 3):
            store.mint_pair_code()
        assert store.take_pair_code(link, kind="brain") is True

    def test_a_v2_database_migrates_and_its_devices_stay_phones(self, tmp_path) -> None:
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version VALUES (2);
            CREATE TABLE devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                ua TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_seen TEXT, revoked_at TEXT
            );
            CREATE TABLE pair_codes (code_hash TEXT PRIMARY KEY, expires REAL NOT NULL, created_at TEXT NOT NULL);
            """
        )
        token = "existing-phone-token"
        conn.execute(
            "INSERT INTO devices (id, name, token_hash, created_at) VALUES ('d1', 'phone', ?, 'x')",
            (os_devices._sha256(token),),
        )
        conn.commit()
        conn.close()

        s = OsDeviceStore(path)
        try:
            assert s.device_for_token(token)["id"] == "d1"
            assert s.device_for_token(token, kind="brain") is None
            _, new_token = s.add_device(name="lab", kind="brain")
            assert s.device_for_token(new_token, kind="brain") is not None
        finally:
            s.close()
        # Opening it again (now v3) must not try to add the column twice.
        OsDeviceStore(path).close()


# ── Minting and redeeming a link ─────────────────────────────────────────────


class TestLinkRoutes:
    async def test_a_relayed_request_can_never_mint_a_link(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            res = await client.post("/api/deck/os/link", headers=REMOTE)
            assert res.status == 403
            assert (await res.json())["error"] == "local_only"
        finally:
            await client.close()

    async def test_the_computer_gets_a_link_to_its_relay_with_the_code_in_the_fragment(
        self, store, edge_version
    ) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            body = await (await client.post("/api/deck/os/link")).json()
            assert body["link"] == f"{EDGE}/os/{TENANT}#link={body['code']}&v=1"
            assert body["ttl_s"] == 600
            assert store.take_pair_code(body["code"]) is False, "a link code is not a phone code"
            assert store.take_pair_code(body["code"], kind="brain") is True
        finally:
            await client.close()

    async def test_an_edge_without_the_link_route_is_named(self, store, edge_version) -> None:
        edge_version["v"] = "1.3.0"
        client, _ = await client_for(FakeUplink())
        try:
            res = await client.post("/api/deck/os/link")
            assert res.status == 409
            body = await res.json()
            assert body["error"] == "edge_outdated"
            assert "navig lighthouse redeploy" in body["message"]
        finally:
            await client.close()

    async def test_the_remote_access_switch_stops_minting(self, store, edge_version) -> None:
        uplink = FakeUplink()
        uplink.os_relay.enabled = False
        client, _ = await client_for(uplink)
        try:
            assert (await client.post("/api/deck/os/link")).status == 409
        finally:
            await client.close()

    async def test_a_link_code_buys_exactly_one_brain_device(self, store, edge_version) -> None:
        client, gw = await client_for(FakeUplink())
        try:
            code, _ = store.mint_pair_code(kind="brain")
            res = await client.post(
                "/api/os/pair/redeem", json={"op": "link", "code": code, "name": "navig-os-lab"}, headers=REMOTE
            )
            assert res.status == 200
            body = await res.json()
            device = store.device_for_token(body["deviceToken"], kind="brain")
            assert device["id"] == body["deviceId"]
            assert device["name"] == "navig-os-lab"
            assert gw.system_events.emitted == [
                ("os.device.paired", {"id": body["deviceId"], "name": "navig-os-lab", "kind": "brain"})
            ]
            again = await client.post("/api/os/pair/redeem", json={"op": "link", "code": code}, headers=REMOTE)
            assert (await again.json())["error"] == "invalid_code"
        finally:
            await client.close()

    async def test_a_phone_code_is_not_a_link_and_back(self, store, edge_version) -> None:
        client, _ = await client_for(FakeUplink())
        try:
            phone, _ = store.mint_pair_code()
            link, _ = store.mint_pair_code(kind="brain")
            as_link = await client.post("/api/os/pair/redeem", json={"op": "link", "code": phone}, headers=REMOTE)
            as_phone = await client.post("/api/os/pair/redeem", json={"code": link}, headers=REMOTE)
            assert (as_link.status, as_phone.status) == (400, 400)
            assert store.list_devices() == []
        finally:
            await client.close()


# ── Deck auth accepts a brain token, and only that ───────────────────────────


class TestDeckAuth:
    async def test_a_brain_token_opens_the_deck_api_relayed(self, store, deck_config) -> None:
        device_id, token = store.add_device(name="lab", kind="brain")
        client, _ = await client_for(FakeUplink(), with_auth=True)
        try:
            res = await client.get("/api/deck/whoami", headers={"Authorization": f"Bearer {token}", **REMOTE})
            assert res.status == 200
            assert await res.json() == {"local": True, "brain": device_id}
            assert store.get(device_id)["last_seen"]
        finally:
            await client.close()

    async def test_a_phone_token_does_not(self, store, deck_config) -> None:
        _, token = store.add_device(name="phone")
        client, _ = await client_for(FakeUplink(), with_auth=True)
        try:
            res = await client.get("/api/deck/whoami", headers={"Authorization": f"Bearer {token}", **REMOTE})
            assert res.status == 401
        finally:
            await client.close()

    async def test_revoking_the_machine_cuts_it_at_once(self, store, deck_config) -> None:
        device_id, token = store.add_device(name="lab", kind="brain")
        client, _ = await client_for(FakeUplink(), with_auth=True)
        try:
            headers = {"Authorization": f"Bearer {token}", **REMOTE}
            assert (await client.get("/api/deck/whoami", headers=headers)).status == 200
            store.revoke(device_id)
            assert (await client.get("/api/deck/whoami", headers=headers)).status == 401
        finally:
            await client.close()

    async def test_the_remote_access_switch_cuts_it(self, store, deck_config) -> None:
        _, token = store.add_device(name="lab", kind="brain")
        uplink = FakeUplink()
        uplink.os_relay.enabled = False
        client, _ = await client_for(uplink, with_auth=True)
        try:
            res = await client.get("/api/deck/whoami", headers={"Authorization": f"Bearer {token}", **REMOTE})
            assert res.status == 401
        finally:
            await client.close()

    async def test_telegram_only_retires_the_key_not_the_link(self, store, deck_config) -> None:
        deck_config["telegram_only"] = True
        _, token = store.add_device(name="lab", kind="brain")
        client, _ = await client_for(FakeUplink(), with_auth=True)
        try:
            key = {"Authorization": f"Bearer {deck_config['api_key']}", **REMOTE}
            link = {"Authorization": f"Bearer {token}", **REMOTE}
            assert (await client.get("/api/deck/whoami", headers=key)).status == 401
            assert (await client.get("/api/deck/whoami", headers=link)).status == 200
        finally:
            await client.close()

    async def test_a_linked_machine_cannot_mint_links_itself(self, store, deck_config, edge_version) -> None:
        _, token = store.add_device(name="lab", kind="brain")
        client, _ = await client_for(FakeUplink(), with_auth=True)
        try:
            res = await client.post("/api/deck/os/link", headers={"Authorization": f"Bearer {token}", **REMOTE})
            assert res.status == 403
            assert (await res.json())["error"] == "local_only"
        finally:
            await client.close()


# ── The uplink rewrite ───────────────────────────────────────────────────────


BEARER = {"Authorization": "Bearer tok"}


class TestUplinkRewrite:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (f"/os/{TENANT}/api/deck/status", "/api/deck/status"),
            (f"/os/{TENANT}/api/deck/os/devices?x=1", "/api/deck/os/devices?x=1"),
            (f"/os/{TENANT}/pair", "/api/os/pair/redeem"),
        ],
    )
    def test_deck_calls_and_pairing_are_routed(self, path, expected) -> None:
        assert _rewrite_os_path(path, BEARER) == expected

    @pytest.mark.parametrize(
        "path",
        [
            f"/os/{TENANT}/api/events",
            f"/os/{TENANT}/api/ingest/web",
            f"/os/{TENANT}/api/os/pair/redeem",
            f"/os/{TENANT}/api/deck/../events",
            f"/os/{TENANT}/api/deck/./../../api/events",
            f"/os/{TENANT}/api/deck/%2e%2e/events",
            f"/os/{TENANT}/api/deck/..%2Fevents",
            f"/os/{TENANT}/api/deck/..\\events",
            "/os/not-a-tenant/api/deck/status",
        ],
    )
    def test_nothing_outside_the_deck_api_is_reachable(self, path) -> None:
        assert _rewrite_os_path(path, BEARER) == path

    def test_no_bearer_no_route(self) -> None:
        path = f"/os/{TENANT}/api/deck/status"
        assert _rewrite_os_path(path, {}) == path
        assert _rewrite_os_path(path, {"authorization": "Basic x"}) == path
        assert _rewrite_os_path(path, {"authorization": "Bearer t"}) == "/api/deck/status"


# ── End to end through the real uplink dispatch ──────────────────────────────


class TestThroughTheUplink:
    """Frames shaped exactly as the edge sends them (path + headers verbatim), dispatched by
    the real UplinkClient into a real deck app on a loopback port: rewrite, relay marking,
    redeem and deck auth together, which no single unit above proves."""

    async def _relay(self, deck_port: int):
        from unittest.mock import AsyncMock

        from navig.cloud import UplinkClient

        uplink = UplinkClient(lighthouse_url=EDGE, api_key="navig_testkey", gateway_port=deck_port)
        uplink._send = AsyncMock()  # type: ignore[method-assign]

        async def call(method: str, path: str, headers: dict, body: str = "") -> tuple[int, dict]:
            await uplink._dispatch(
                {"id": "r", "kind": "deck", "method": method, "path": path, "headers": headers, "body": body}
            )
            sent = uplink._send.await_args.args[0]
            import json as _json

            try:
                data = _json.loads(sent["body"] or "{}")
            except ValueError:
                data = {}
            return sent["status"], data

        return uplink, call

    async def test_link_redeem_then_deck_calls_then_the_walls(self, store, deck_config) -> None:
        client, _ = await client_for(FakeUplink(), with_auth=True)
        uplink, call = await self._relay(client.port)
        try:
            code, _ = store.mint_pair_code(kind="brain")
            status, body = await call(
                "POST", f"/os/{TENANT}/pair", {"content-type": "application/json"},
                f'{{"op": "link", "code": "{code}", "name": "lab"}}',
            )
            assert status == 200, body
            token = body["deviceToken"]
            bearer = {"authorization": f"Bearer {token}"}

            status, body = await call("GET", f"/os/{TENANT}/api/deck/whoami", bearer)
            assert (status, body["local"], body["brain"]) == (200, True, store.device_for_token(token, kind="brain")["id"])

            # Relayed, so the local-only routes refuse it even with a valid link token.
            status, body = await call("POST", f"/os/{TENANT}/api/deck/os/link", bearer)
            assert (status, body.get("error")) == (403, "local_only")

            # No Bearer, or a path out of the deck API: never rewritten, so nothing is served.
            assert (await call("GET", f"/os/{TENANT}/api/deck/whoami", {}))[0] == 404
            assert (await call("GET", f"/os/{TENANT}/api/deck/../deck/whoami", bearer))[0] == 404
        finally:
            await uplink.stop()  # closes its loopback HTTP session
            await client.close()


# ── Live events for a linked machine (polled, numbered) ──────────────────────


class TestLinkEvents:
    def test_first_poll_starts_at_now_then_delivers_in_order(self) -> None:
        backlog = os_remote.EventBacklog()
        backlog.add("before", {"n": 0})
        first = backlog.since("", None)
        assert (first["events"], first["gap"]) == ([], False)
        backlog.add("a", {"n": 1})
        backlog.add("b", {"n": 2})
        page = backlog.since(first["epoch"], first["cursor"])
        assert [e["type"] for e in page["events"]] == ["a", "b"]
        assert page["gap"] is False
        again = backlog.since(page["epoch"], page["cursor"])
        assert (again["events"], again["cursor"]) == ([], page["cursor"])

    def test_a_restarted_brain_or_an_overrun_backlog_says_events_were_missed(self) -> None:
        backlog = os_remote.EventBacklog(maxlen=3)
        start = backlog.since("", None)
        assert backlog.since("an-old-process", start["cursor"])["gap"] is True
        for i in range(6):
            backlog.add("e", {"i": i})
        page = backlog.since(start["epoch"], start["cursor"])
        assert page["gap"] is True
        assert [e["data"]["i"] for e in page["events"]] == [3, 4, 5]

    def test_a_cursor_from_the_future_resyncs_instead_of_sticking(self) -> None:
        backlog = os_remote.EventBacklog()
        backlog.add("x", {})
        page = backlog.since(backlog.epoch, 999)
        assert (page["cursor"], page["gap"]) == (1, True)
        backlog.add("y", {})
        assert [e["type"] for e in backlog.since(backlog.epoch, page["cursor"])["events"]] == ["y"]

    def test_a_page_is_bounded_and_a_huge_event_travels_as_its_type(self) -> None:
        backlog = os_remote.EventBacklog()
        start = backlog.since("", None)
        backlog.add("big", {"blob": "x" * (os_remote._EVENT_MAX_BYTES + 10)})
        for _ in range(os_remote._EVENTS_PER_REPLY + 5):
            backlog.add("e", {})
        page = backlog.since(start["epoch"], start["cursor"])
        assert len(page["events"]) == os_remote._EVENTS_PER_REPLY
        assert page["events"][0]["data"] == {"_truncated": True}
        rest = backlog.since(page["epoch"], page["cursor"])
        assert len(rest["events"]) == 6

    async def test_a_linked_machine_polls_events_through_the_relay(self, store, deck_config, tmp_path) -> None:
        """The real queue, the real route, the brain token, relayed: an event emitted
        after the first poll arrives on the next one; a bad cursor is a 400."""
        import asyncio

        from navig.gateway.system_events import SystemEventQueue

        queue = SystemEventQueue(storage_path=tmp_path / "events")
        await queue.start()
        uplink = FakeUplink()
        client, gw = await client_for(uplink, with_auth=True)
        gw.system_events = queue
        _, token = store.add_device(name="lab", kind="brain")
        headers = {"Authorization": f"Bearer {token}", **REMOTE}
        try:
            first = await (await client.get("/api/deck/os/link/events", headers=headers)).json()
            await queue.emit("board_update", {"card": 7})
            for _ in range(50):
                page = await (await client.get(
                    "/api/deck/os/link/events",
                    params={"epoch": first["epoch"], "cursor": str(first["cursor"])},
                    headers=headers,
                )).json()
                if page["events"]:
                    break
                await asyncio.sleep(0.05)
            assert [(e["type"], e["data"]) for e in page["events"]] == [("board_update", {"card": 7})]
            bad = await client.get("/api/deck/os/link/events", params={"cursor": "x"}, headers=headers)
            assert bad.status == 400
            nobody = await client.get("/api/deck/os/link/events", headers=REMOTE)
            assert nobody.status == 401
        finally:
            await queue.stop()
            await client.close()
            os_remote._backlogs.clear()
