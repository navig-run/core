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
