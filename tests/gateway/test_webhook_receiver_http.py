"""Integration tests for WebhookReceiver.handle_webhook over aiohttp — the actual
HTTP security gate, which had zero direct coverage.

The signature *helpers* are unit-tested in test_webhooks_signatures_receiver.py;
this pins the *enforcement* path so a future "log-and-continue" regression can't
silently accept an unverified webhook: unknown source -> 404, disabled -> 403,
bad JSON -> 400, verify_signature with no secret -> 500 (refuse, don't accept),
invalid/missing signature -> 401, and a valid signed event -> 200 AND reaches the
registered handler (while every rejected request does NOT).

Routes are registered the correct way (``app.add_routes(receiver.get_routes())``)
— the gateway's own ``_setup_webhook_routes`` unpacking is a separate concern.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.webhooks.receiver import WebhookReceiver

_SECRET = "shhh-super-secret-key"


def _receiver_with_handler():
    """A receiver with a signed source, a disabled source, and a verify-but-no-secret
    source, plus a handler that records every dispatched event."""
    received = []
    rcv = WebhookReceiver(
        {
            "webhooks": {
                "enabled": True,
                "sources": {
                    "secure": {
                        "enabled": True,
                        "verify_signature": True,
                        "signature_header": "X-Sig",
                        "signature_algo": "sha256",
                    },
                    "off": {"enabled": False, "verify_signature": False},
                    "nosecret": {
                        "enabled": True,
                        "verify_signature": True,
                        "signature_header": "X-Sig",
                        "signature_algo": "sha256",
                    },
                },
                "secrets": {"secure": _SECRET},  # 'nosecret' deliberately has none
            }
        }
    )

    @rcv.on_event
    async def _capture(event):
        received.append(event)

    return rcv, received


def _app(rcv: WebhookReceiver) -> web.Application:
    app = web.Application()
    app.add_routes(rcv.get_routes())
    return app


def _sign(body: bytes) -> str:
    return hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


async def test_valid_signature_accepts_and_dispatches():
    rcv, received = _receiver_with_handler()
    body = json.dumps({"event": "push", "n": 1}).encode()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/secure", data=body, headers={"X-Sig": _sign(body)})
        assert res.status == 200
        data = await res.json()
        assert data["ok"] is True and data["event_id"]
    assert len(received) == 1  # the signed event reached the handler
    assert received[0].signature_valid is True


async def test_invalid_signature_rejected_401_and_not_dispatched():
    rcv, received = _receiver_with_handler()
    body = json.dumps({"event": "push"}).encode()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/secure", data=body, headers={"X-Sig": "deadbeef"})
        assert res.status == 401
    assert received == []  # a forged event never reaches the handler


async def test_missing_signature_header_rejected_401():
    rcv, received = _receiver_with_handler()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/secure", data=b"{}")  # no X-Sig at all
        assert res.status == 401
    assert received == []


async def test_verify_true_but_no_secret_is_500_not_silent_accept():
    rcv, received = _receiver_with_handler()
    body = b"{}"
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/nosecret", data=body, headers={"X-Sig": _sign(body)})
        assert res.status == 500  # misconfigured → refuse, never accept unauthenticated
    assert received == []


async def test_unknown_source_404():
    rcv, received = _receiver_with_handler()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/ghost", data=b"{}")
        assert res.status == 404
    assert received == []


async def test_disabled_source_403():
    rcv, _ = _receiver_with_handler()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/off", data=b"{}")
        assert res.status == 403


async def test_invalid_json_400_before_dispatch():
    rcv, received = _receiver_with_handler()
    async with TestClient(TestServer(_app(rcv))) as client:
        res = await client.post("/webhook/secure", data=b"{not valid json", headers={"X-Sig": "x"})
        assert res.status == 400
    assert received == []


def test_config_set_string_booleans_are_coerced():
    """`navig config set webhooks.* false` stores the STRING "false" (truthy). The receiver
    must coerce it — otherwise the top-level toggle can't disable the receiver, a disabled
    source stays enabled, and an unsigned source's `verify_signature: "false"` stays truthy so
    its legitimate webhooks get rejected against a missing secret."""
    rcv = WebhookReceiver(
        {
            "webhooks": {
                "enabled": "false",  # config-set string, NOT a bool
                "sources": {
                    "unsigned": {"enabled": "true", "verify_signature": "false"},
                    "gone": {"enabled": "false"},
                },
            }
        }
    )
    assert rcv.enabled is False                              # top-level toggle honored
    assert rcv._sources["unsigned"].enabled is True
    assert rcv._sources["unsigned"].verify_signature is False  # not the truthy string "false"
    assert rcv._sources["gone"].enabled is False


def _unsigned_receiver() -> WebhookReceiver:
    """An enabled source with signature verification off — so the request body reaches
    event extraction (the code path the input-validation bugs live in)."""
    return WebhookReceiver(
        {"webhooks": {"sources": {"plain": {"enabled": True, "verify_signature": False}}}}
    )


async def test_non_object_json_body_is_400_not_a_500_crash():
    """A valid but non-object JSON body (`[]`, `42`) parses fine, but extract_event_type /
    the handlers call payload.get(...) — a list/int has no .get, which used to escape the
    narrow parse try as an unhandled AttributeError → 500, reachable unauthenticated."""
    rcv = _unsigned_receiver()
    async with TestClient(TestServer(_app(rcv))) as client:
        for bad in (b"[]", b"42", b'"x"', b"true"):
            res = await client.post("/webhook/plain", data=bad)
            assert res.status == 400, f"{bad!r} should be 400, got {res.status}"
        # a real object still works
        ok = await client.post("/webhook/plain", data=b'{"event": "ping"}')
        assert ok.status == 200


async def test_history_limit_zero_and_negative_are_bounded():
    """`?limit=0` used to return the ENTIRE buffer (`[-0:]`) — over-exposing every stored
    payload — and negatives sliced from the front. Both must return nothing now."""
    rcv = _unsigned_receiver()
    async with TestClient(TestServer(_app(rcv))) as client:
        await client.post("/webhook/plain", data=b'{"n": 1}')
        await client.post("/webhook/plain", data=b'{"n": 2}')

        assert (await (await client.get("/webhook/history?limit=0")).json())["events"] == []
        assert (await (await client.get("/webhook/history?limit=-5")).json())["events"] == []
        # a normal limit still returns the stored events
        many = await (await client.get("/webhook/history?limit=10")).json()
        assert len(many["events"]) == 2
