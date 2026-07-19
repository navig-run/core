"""Regression: POST /api/deck/messages/send was dead.

It imported `RoutingEngine` from `navig.commands.dispatch` and
`get_adapter_registry` from `navig.messaging.registry` — neither module has ever
exposed those names — and then called `adapter.send(...)` (real method is
`send_message`) reading `receipt.id` (real field is `message_id`). Every deck
send therefore 500'd. It now mirrors the proven CLI path
(`navig.commands.dispatch.dispatch_send`): resolve → track → thread → send.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import messages as messages_mod

    app = web.Application()
    app.router.add_post("/messages/send", messages_mod.handle_deck_messages_send)
    return app


# ── fakes mirroring the real messaging surfaces the route depends on ──────────
class _Target:
    address = "+15551230000"
    display_hint = "alice"


class _Decision:
    adapter_name = "sms"
    resolved_target = _Target()
    compliance_mode = None


class _Thread:
    remote_conversation_id = "conv-1"


class _Receipt:
    def __init__(self, ok=True, message_id="mid-1", error=None):
        self.ok = ok
        self.message_id = message_id
        self.error = error


class _Adapter:
    def __init__(self, receipt):
        self._receipt = receipt
        self.sent = []

    async def get_or_create_thread(self, route):
        return _Thread()

    async def send_message(self, conv_id, message):
        self.sent.append((conv_id, message))
        return self._receipt


class _Registry:
    def __init__(self, adapter):
        self._adapter = adapter

    def get(self, name):
        return self._adapter


class _Tracker:
    def __init__(self):
        self.records = []
        self.receipts = []

    def record_send(self, **kw):
        self.records.append(kw)
        return "delivery-1"

    def apply_receipt(self, delivery_id, receipt):
        self.receipts.append((delivery_id, receipt))


def _wire(monkeypatch, *, registry, tracker, resolve=None):
    import navig.messaging.routing as routing_mod

    monkeypatch.setattr("navig.store.contacts.get_contact_store", lambda: object())
    monkeypatch.setattr("navig.store.threads.get_thread_store", lambda: object())
    monkeypatch.setattr("navig.messaging.adapter_registry.get_adapter_registry", lambda: registry)
    monkeypatch.setattr("navig.messaging.delivery.get_delivery_tracker", lambda: tracker)

    class _Engine:
        def __init__(self, *a):
            pass

        def resolve(self, target, network=None):
            if resolve is not None:
                return resolve(target, network)
            return _Decision()

    monkeypatch.setattr(routing_mod, "RoutingEngine", _Engine)


async def test_send_happy_path(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    adapter = _Adapter(_Receipt(ok=True, message_id="mid-1"))
    tracker = _Tracker()
    _wire(monkeypatch, registry=_Registry(adapter), tracker=tracker)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/messages/send", json={"target": "@alice", "body": "hi"})
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["ok"] is True
        assert data["adapter"] == "sms"
        assert data["id"] == "mid-1"
        assert data["error"] == ""

    # the real send path actually ran, and delivery was tracked
    assert adapter.sent == [("conv-1", "hi")]
    assert tracker.records and tracker.receipts


async def test_missing_fields_returns_400(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/messages/send", json={"body": "hi"})  # no target
        assert r.status == 400


async def test_no_route_returns_404(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from navig.messaging.routing import NoRouteError

    def _raise(target, network):
        raise NoRouteError("no route for target")

    _wire(monkeypatch, registry=_Registry(_Adapter(_Receipt())), tracker=_Tracker(), resolve=_raise)
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/messages/send", json={"target": "@ghost", "body": "hi"})
        assert r.status == 404


async def test_unavailable_adapter_returns_502(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    class _NoneRegistry:
        def get(self, name):
            return None

    _wire(monkeypatch, registry=_NoneRegistry(), tracker=_Tracker())
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/messages/send", json={"target": "@alice", "body": "hi"})
        assert r.status == 502


def test_real_seams_exist_not_the_phantoms():
    import navig.commands.dispatch as dispatch_mod
    import navig.messaging.registry as registry_mod
    from navig.messaging.adapter_registry import get_adapter_registry  # noqa: F401
    from navig.messaging.routing import RoutingEngine  # noqa: F401

    # the phantom names must not reappear on the modules the old route wrongly used
    assert not hasattr(dispatch_mod, "RoutingEngine")
    assert not hasattr(registry_mod, "get_adapter_registry")
