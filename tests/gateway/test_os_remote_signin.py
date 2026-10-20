"""Signing in from a new device: a navig.run ticket buys a REQUEST, never access.

The chain under test, through the real routes and the real store:
ticket (signed, for this tenant + linked email) → request → the owner approves (on the
computer, or in Telegram as the owner) → the waiting browser's next poll receives the
device token exactly once. And everything that must fail: a relayed approval, a second
use of a ticket, a guessed poll secret, another Telegram user's tap.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("cryptography")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.cloud import remote_ticket
from navig.gateway.deck.routes import os_remote
from navig.store.os_devices import OsDeviceStore
from navig.telegram import os_access_actions

from .test_os_remote_routes import REMOTE, TENANT, FakeGateway, FakeUplink

OWNER = 111
EMAIL = "owner@example.com"
# RFC 8032 §7.1 test 1 — a public test key, standing in for navig.run's ticket key.
_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
KEYS = {"rt_test_1": bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")}


def _b64(b: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sign(claims: dict) -> str:
    """What api.navig.run's /api/remote/ticket returns (same layout as remote-access.ts)."""
    import json

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = Ed25519PrivateKey.from_private_bytes(_SEED).sign(remote_ticket.SIGNED_PREFIX + payload.encode())
    return f"{remote_ticket.TICKET_PREFIX}{payload}.{_b64(sig)}"


def _claims(**over) -> dict:
    return {"v": 1, "kid": "rt_test_1", "e": EMAIL, "t": TENANT, "x": 0, "n": "n", **over}


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[tuple] = []
        self.markups: list[tuple] = []

    async def send_message(self, chat_id, text, parse_mode=None, keyboard=None, **_):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"message_id": 900 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text, parse_mode=None, keyboard=None):
        self.edits.append((chat_id, message_id, text))
        return {}

    async def edit_message_reply_markup(self, chat_id, message_id, markup):
        self.markups.append((chat_id, message_id, markup))
        return {}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    s = OsDeviceStore(tmp_path / "os_remote.db")
    monkeypatch.setattr(os_remote, "_store", lambda: s)
    monkeypatch.setattr(os_access_actions, "_store", lambda: s)
    monkeypatch.setattr(os_remote, "_account_email", lambda: EMAIL)
    monkeypatch.setattr(os_access_actions, "owner_chat_id", lambda: OWNER)
    monkeypatch.setattr(remote_ticket, "PUBLIC_KEYS", KEYS)
    monkeypatch.setattr(os_access_actions, "_gateway", None)
    os_access_actions._prompts.clear()
    os_remote._redeem_times.clear()
    os_remote._poll_times.clear()
    yield s
    s.close()


async def start():
    app = web.Application()
    gw = FakeGateway(FakeUplink())
    gw.channels = {"telegram": FakeTelegram()}
    app["gateway"] = gw
    os_remote.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, gw


def fresh_ticket(**over) -> str:
    import time

    return _sign(_claims(x=int(time.time()) + 300, n=over.pop("n", "nonce-1"), **over))


async def ask(client, ticket, name="Safari on iPad"):
    return await client.post("/api/os/pair/redeem", json={"op": "request", "ticket": ticket, "name": name}, headers=REMOTE)


async def poll(client, rid, secret):
    res = await client.post("/api/os/pair/redeem", json={"op": "poll", "id": rid, "secret": secret}, headers=REMOTE)
    return await res.json()


class TestRequest:
    async def test_a_valid_ticket_creates_a_request_and_asks_the_owner_everywhere(self, env) -> None:
        client, gw = await start()
        try:
            res = await ask(client, fresh_ticket())
            assert res.status == 200, await res.text()
            body = await res.json()
            assert body["telegram"] is True and body["pollSecret"] and body["requestId"]
            assert "deviceToken" not in body, "a request must never carry a key"
            assert env.list_devices() == []
            assert [k for k, _ in gw.system_events.emitted] == ["os.device.request"]
            tg = gw.channels["telegram"]
            assert tg.sent[0]["chat_id"] == OWNER
            assert "Safari on iPad" in tg.sent[0]["text"]
            buttons = [b["callback_data"] for b in tg.sent[0]["keyboard"][0]]
            assert buttons == [f"osr:ok:{body['requestId']}", f"osr:no:{body['requestId']}"]
            assert all(len(b.encode()) <= 64 for b in buttons)
            assert await poll(client, body["requestId"], body["pollSecret"]) == {"status": "pending"}
        finally:
            await client.close()

    @pytest.mark.parametrize(
        ("over", "code"),
        [({"t": "b" * 64}, "wrong_computer"), ({"e": "someone@else.com"}, "wrong_account")],
    )
    async def test_a_ticket_for_another_computer_or_account_is_refused(self, env, over, code) -> None:
        client, gw = await start()
        try:
            res = await ask(client, fresh_ticket(**over))
            assert res.status == 403
            assert (await res.json())["error"] == code
            assert gw.channels["telegram"].sent == []
        finally:
            await client.close()

    async def test_one_ticket_buys_one_request(self, env) -> None:
        client, _ = await start()
        try:
            ticket = fresh_ticket()
            assert (await ask(client, ticket)).status == 200
            again = await ask(client, ticket)
            assert again.status == 409
            assert (await again.json())["error"] == "ticket_used"
        finally:
            await client.close()

    async def test_nothing_is_asked_while_remote_access_is_off(self, env) -> None:
        client, gw = await start()
        try:
            gw.cloud_manager._uplink.os_relay.enabled = False
            res = await ask(client, fresh_ticket())
            assert res.status == 403
            assert gw.channels["telegram"].sent == []
        finally:
            await client.close()


class TestApproval:
    async def test_approve_on_the_computer_then_the_poll_gets_the_token_once(self, env) -> None:
        client, gw = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            rid, secret = body["requestId"], body["pollSecret"]
            assert [r["id"] for r in (await (await client.get("/api/deck/os/requests")).json())["requests"]] == [rid]

            res = await client.post(f"/api/deck/os/requests/{rid}/approve")
            assert res.status == 200
            # The Telegram buttons are retired so they cannot be tapped into a no-op.
            assert gw.channels["telegram"].markups and "can now use" in gw.channels["telegram"].edits[0][2]

            got = await poll(client, rid, secret)
            assert got["status"] == "approved" and got["deviceToken"]
            assert env.device_for_token(got["deviceToken"])["id"] == got["deviceId"]
            assert await poll(client, rid, secret) == {"status": "used"}, "the token is shown once"
            assert "os.device.paired" in [k for k, _ in gw.system_events.emitted]
        finally:
            await client.close()

    async def test_a_relayed_request_cannot_approve_but_can_deny(self, env) -> None:
        client, _ = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            rid = body["requestId"]
            res = await client.post(f"/api/deck/os/requests/{rid}/approve", headers=REMOTE)
            assert res.status == 403
            assert (await client.post(f"/api/deck/os/requests/{rid}/deny", headers=REMOTE)).status == 200
            assert await poll(client, rid, body["pollSecret"]) == {"status": "denied"}
            assert (await client.post(f"/api/deck/os/requests/{rid}/approve")).status == 409
            assert env.list_devices() == []
        finally:
            await client.close()

    async def test_a_wrong_secret_learns_nothing(self, env) -> None:
        client, _ = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            await client.post(f"/api/deck/os/requests/{body['requestId']}/approve")
            assert await poll(client, body["requestId"], "guess") == {"status": "unknown"}
            assert await poll(client, "nope", body["pollSecret"]) == {"status": "unknown"}
            assert env.list_devices() == [], "a failed poll must not mint"
        finally:
            await client.close()


class TestTelegram:
    async def test_the_owners_tap_approves(self, env) -> None:
        client, gw = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            tg = gw.channels["telegram"]
            toast = await os_access_actions.handle_callback(tg, f"osr:ok:{body['requestId']}", OWNER, 901, OWNER)
            assert toast.startswith("Allowed")
            got = await poll(client, body["requestId"], body["pollSecret"])
            assert got["status"] == "approved"
            decided = [p for k, p in gw.system_events.emitted if k == "os.device.request.decided"]
            assert decided and decided[0]["approved"] is True, "the desktop hears about a Telegram decision"
        finally:
            await client.close()

    async def test_another_allowed_users_tap_does_nothing(self, env) -> None:
        client, gw = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            toast = await os_access_actions.handle_callback(
                gw.channels["telegram"], f"osr:ok:{body['requestId']}", 222, 901, 222
            )
            assert "Only the owner" in toast
            assert await poll(client, body["requestId"], body["pollSecret"]) == {"status": "pending"}
        finally:
            await client.close()

    async def test_a_second_tap_says_so_instead_of_pretending(self, env) -> None:
        client, gw = await start()
        try:
            body = await (await ask(client, fresh_ticket())).json()
            tg = gw.channels["telegram"]
            await os_access_actions.handle_callback(tg, f"osr:no:{body['requestId']}", OWNER, 901, OWNER)
            toast = await os_access_actions.handle_callback(tg, f"osr:ok:{body['requestId']}", OWNER, 901, OWNER)
            assert toast == "Already denied."
            assert env.list_devices() == []
        finally:
            await client.close()


class TestAccountLink:
    async def test_linking_is_local_only(self, env) -> None:
        client, _ = await start()
        try:
            res = await client.post("/api/deck/os/account", json={"email": EMAIL}, headers=REMOTE)
            assert res.status == 403
            assert (await client.delete("/api/deck/os/account", headers=REMOTE)).status == 403
        finally:
            await client.close()

    async def test_link_calls_navig_run_as_this_computer_and_remembers_the_email(self, env, monkeypatch) -> None:
        calls: list[tuple] = []
        saved: dict = {}

        async def fake_api(method, path, body=None):
            calls.append((method, path, body))
            return 200, {"ok": True}

        class CM:
            def set_global(self, k, v):
                saved[k] = v

            def get(self, k, d=None):
                return d

        monkeypatch.setattr(os_remote, "_api", fake_api)
        monkeypatch.setattr("navig.config.get_config_manager", lambda: CM())
        client, _ = await start()
        try:
            res = await client.post("/api/deck/os/account", json={"email": " Owner@Example.com "})
            assert res.status == 200, await res.text()
            assert calls[0][:2] == ("POST", "/api/remote/link/start")
            assert calls[0][2]["email"] == EMAIL and calls[0][2]["edge_url"] == "https://edge.example.workers.dev"
            assert saved == {"os.remote.account_email": EMAIL}
            bad = await client.post("/api/deck/os/account", json={"email": "nope"})
            assert bad.status == 400
        finally:
            await client.close()
