"""Unit tests for the shared outbound-send seam: navig.messaging.send.route_and_send.

One code path behind both `navig dispatch send` and the deck
`POST /api/deck/messages/send`, so it is tested once here.
"""

from __future__ import annotations

import pytest

import navig.messaging.adapter_registry as areg_mod
import navig.messaging.delivery as delivery_mod
import navig.messaging.routing as routing_mod
import navig.store.contacts as contacts_mod
import navig.store.threads as threads_mod
from navig.messaging.send import AdapterUnavailableError, route_and_send


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
    ok = True
    message_id = "mid-1"
    error = None


class _Adapter:
    def __init__(self):
        self.sent = []
        self.route = None

    async def get_or_create_thread(self, route):
        self.route = route
        return _Thread()

    async def send_message(self, conv_id, message):
        self.sent.append((conv_id, message))
        return _Receipt()


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
        return "d-1"

    def apply_receipt(self, delivery_id, receipt):
        self.receipts.append((delivery_id, receipt))


def _wire(monkeypatch, *, registry, tracker, resolve=None):
    monkeypatch.setattr(contacts_mod, "get_contact_store", lambda: object())
    monkeypatch.setattr(threads_mod, "get_thread_store", lambda: object())
    monkeypatch.setattr(areg_mod, "get_adapter_registry", lambda: registry)
    monkeypatch.setattr(delivery_mod, "get_delivery_tracker", lambda: tracker)

    class _Engine:
        def __init__(self, *a):
            pass

        def resolve(self, target, network=None):
            if resolve is not None:
                return resolve(target, network)
            return _Decision()

    monkeypatch.setattr(routing_mod, "RoutingEngine", _Engine)


async def test_happy_path_resolves_tracks_and_sends(monkeypatch):
    adapter = _Adapter()
    tracker = _Tracker()
    _wire(monkeypatch, registry=_Registry(adapter), tracker=tracker)

    decision, receipt = await route_and_send("@alice", "hi", network=None)

    assert decision.adapter_name == "sms"
    assert receipt.message_id == "mid-1"
    # thread key is "<adapter>:<address>", and the message is sent to its conv id
    assert adapter.route == "sms:+15551230000"
    assert adapter.sent == [("conv-1", "hi")]
    # delivery recorded once with the resolved fields, and the receipt applied
    assert len(tracker.records) == 1
    assert tracker.records[0]["adapter"] == "sms"
    assert tracker.records[0]["target"] == "+15551230000"
    assert tracker.records[0]["contact_alias"] == "alice"
    assert tracker.receipts == [("d-1", receipt)]


async def test_no_route_propagates(monkeypatch):
    from navig.messaging.routing import NoRouteError

    def _raise(target, network):
        raise NoRouteError("no route for target")

    _wire(monkeypatch, registry=_Registry(_Adapter()), tracker=_Tracker(), resolve=_raise)
    with pytest.raises(NoRouteError):
        await route_and_send("@ghost", "hi")


async def test_unavailable_adapter_raises(monkeypatch):
    class _NoneRegistry:
        def get(self, name):
            return None

    _wire(monkeypatch, registry=_NoneRegistry(), tracker=_Tracker())
    with pytest.raises(AdapterUnavailableError) as excinfo:
        await route_and_send("@alice", "hi")
    assert excinfo.value.adapter_name == "sms"
