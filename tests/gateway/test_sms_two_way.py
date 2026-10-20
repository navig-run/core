"""Two-way SMS: an allowed, Twilio-signed text is answered by the assistant.

Pins the three conditions that keep a phone number from becoming a remote shell:
the sender must be on ``sms.allowed_numbers``, the request must carry a valid Twilio
signature (whatever ``sms.verify_signature`` says), and the assistant is reached with
``allow_cli=False``. Also pins the Lighthouse fix: a relayed request is verified
against the edge URL Twilio actually signed, not the loopback URL it arrived on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.routes import sms_webhook

TOKEN = "twilio-test-token"
OWNER = "+15550100100"
EDGE = "https://edge.example.workers.dev"
DECK_KEY = "deck-key-for-tests-0123456789"


def _sign(url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


class _CM:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def refresh_global_config(self):
        pass


class _Secret:
    def __init__(self, v):
        self.v = v

    def reveal(self):
        return self.v


class _Vault:
    def get_secret(self, label):
        return _Secret(TOKEN if label == "twilio_auth_token" else "")


class _Adapter:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, thread_id, text, attachments=None):
        self.sent.append((thread_id, text))
        return type("R", (), {"ok": True, "error": None})()

    async def receive_webhook(self, form):
        return form

    async def ingest_event(self, event):
        pass


class _Registry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, name):
        return self.adapter if name == "sms" else None

    def get_unchecked(self, name):
        return self.get(name)


class _Router:
    def __init__(self):
        self.calls: list[dict] = []

    async def route_message(self, **kw):
        self.calls.append(kw)
        return "Disk C: is 41% full."


@pytest.fixture
def env(monkeypatch):
    state = {
        "config": {"sms.allowed_numbers": f"{OWNER}, +15550100999"},
        "adapter": _Adapter(),
        "router": _Router(),
        "notified": [],
        "spawned": [],
    }
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM(state["config"]))
    monkeypatch.setattr("navig.vault.get_vault", lambda: _Vault())
    monkeypatch.setattr(
        "navig.messaging.adapter_registry.get_adapter_registry", lambda: _Registry(state["adapter"])
    )

    async def fake_dispatch(*a, **kw):
        state["notified"].append(a)

    monkeypatch.setattr("navig.notify.dispatch", fake_dispatch)
    monkeypatch.setattr("navig.core.background.spawn", lambda coro, name=None: state["spawned"].append(coro))
    return state


async def _post(env, form, *, sign_url=None, headers=None, bad_sig=False):
    gateway = type("G", (), {"router": env["router"]})()
    app = web.Application()
    sms_webhook.register(app, gateway)
    async with TestClient(TestServer(app)) as client:
        url = sign_url or f"http://{client.server.host}:{client.server.port}/sms/webhook"
        h = dict(headers or {})
        h["X-Twilio-Signature"] = "forged" if bad_sig else _sign(url, form)
        resp = await client.post("/sms/webhook", data=form, headers=h)
        assert resp.status == 200
        assert "<Response></Response>" in await resp.text()
    for coro in env["spawned"]:
        await coro
    return env


async def test_allowed_signed_text_is_answered_by_sms(env):
    await _post(env, {"From": OWNER, "Body": "how full is my disk?"})

    call = env["router"].calls[0]
    assert call["channel"] == "sms" and call["user_id"] == OWNER
    assert call["message"] == "how full is my disk?"
    assert call["allow_cli"] is False
    assert env["adapter"].sent == [(OWNER, "Disk C: is 41% full.")]
    assert env["notified"] == []  # the operator is talking to NAVIG, not being notified


async def test_formatting_differences_in_the_allowlist_still_match(env):
    env["config"]["sms.allowed_numbers"] = ["+1 (555) 010-0100"]
    await _post(env, {"From": OWNER, "Body": "hi"})
    assert len(env["router"].calls) == 1


async def test_unknown_number_only_notifies(env):
    await _post(env, {"From": "+15550199999", "Body": "navig vault list"})

    assert env["router"].calls == [] and env["adapter"].sent == []
    assert len(env["notified"]) == 1


async def test_allowed_number_without_a_valid_signature_is_not_answered(env):
    await _post(env, {"From": OWNER, "Body": "navig run whoami"}, bad_sig=True)

    assert env["router"].calls == [] and env["adapter"].sent == []
    assert len(env["notified"]) == 1  # still lands in the inbox (verify_signature is off)


async def test_empty_allowlist_answers_nobody(env):
    env["config"].pop("sms.allowed_numbers")
    await _post(env, {"From": OWNER, "Body": "hi"})
    assert env["router"].calls == []


async def test_lighthouse_relay_is_verified_against_the_edge_url(env):
    from navig.cloud.uplink import api_key_hash

    env["config"].update({"cloud.lighthouse_url": EDGE, "deck.api_key": DECK_KEY})
    edge_url = f"{EDGE}/sms/{api_key_hash(DECK_KEY)}"

    await _post(
        env, {"From": OWNER, "Body": "status"}, sign_url=edge_url, headers={"X-Navig-Via": "lighthouse"}
    )

    assert len(env["router"].calls) == 1


async def test_signature_for_the_loopback_url_fails_when_relayed(env):
    env["config"].update({"cloud.lighthouse_url": EDGE, "deck.api_key": DECK_KEY})

    # Signed for the loopback URL (what the old code rebuilt) — not what Twilio signs.
    await _post(env, {"From": OWNER, "Body": "status"}, headers={"X-Navig-Via": "lighthouse"})

    assert env["router"].calls == []


async def test_a_long_answer_is_capped_at_three_messages(env):
    async def long_reply(**kw):
        return "x" * 10_000

    env["router"].route_message = long_reply
    await _post(env, {"From": OWNER, "Body": "tell me everything"})

    assert len(env["adapter"].sent) == 3
    assert env["adapter"].sent[-1][1].endswith("(full answer on the deck)")


async def test_a_failed_turn_still_answers(env):
    async def boom(**kw):
        raise RuntimeError("provider down")

    env["router"].route_message = boom
    await _post(env, {"From": OWNER, "Body": "hi"})

    assert len(env["adapter"].sent) == 1 and "couldn't" in env["adapter"].sent[0][1]
