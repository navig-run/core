"""Integration tests for the signed Signals ingest route (POST /api/ingest/{source})
end-to-end through aiohttp: good/bad/missing signature, unknown source, replay,
and that a verified event lands in the deck feed."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import feed, signals

    return signals, feed


def _build_app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import ingest

    app = web.Application()
    ingest.register(app, None)  # the route doesn't need the gateway handle
    return app


def _headers(secret: str, body: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Navig-Timestamp": ts, "X-Navig-Signature": sig, "Content-Type": "application/json"}


async def test_signed_event_reaches_deck_feed(env):
    signals, feed = env
    secret = signals.add_source("hookdemo")["secret"]

    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"event": "deploy", "status": "green"}).encode()
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/hookdemo", data=body, headers=_headers(secret, body))
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "deck" in data["delivered"]

    # The deck channel wrote a feed item, routed to the source's own row.
    items = feed.list_items()
    assert len(items) == 1
    assert items[0]["type"] == "signal:hookdemo"
    assert items[0]["data"]["source"] == "hookdemo"


async def test_bad_signature_rejected(env):
    signals, _feed = env
    signals.add_source("h2")
    from aiohttp.test_utils import TestClient, TestServer

    body = b'{"x":1}'
    headers = _headers("sk_sig_wrongsecret", body)
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/h2", data=body, headers=headers)
        assert resp.status == 401


async def test_unknown_source_404(env):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/ghost", data=b"{}", headers=_headers("x", b"{}"))
        assert resp.status == 404


async def test_missing_signature_401(env):
    signals, _feed = env
    signals.add_source("h3")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/h3", data=b"{}", headers={"Content-Type": "application/json"})
        assert resp.status == 401


async def test_replay_is_deduped(env):
    signals, feed = env
    secret = signals.add_source("h4")["secret"]
    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"n": 1}).encode()
    headers = _headers(secret, body, ts=str(int(time.time())))
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/h4", data=body, headers=headers)
        first_body = await first.json()
        second = await client.post("/api/ingest/h4", data=body, headers=headers)
        second_body = await second.json()
    assert first.status == 200 and first_body.get("duplicate") is not True
    assert second_body.get("duplicate") is True
    # Only the first dispatched to the feed.
    assert len(feed.list_items()) == 1


async def test_case_permuted_signature_is_still_deduped(env):
    """A re-fired request whose hex signature is re-CASED still verifies (HMAC
    compare is case-insensitive) and MUST map to the same replay key — else the
    named replay defence is bypassable from a single captured request."""
    signals, feed = env
    secret = signals.add_source("hcase")["secret"]
    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"n": 1}).encode()
    headers = _headers(secret, body)
    prefix, _, hexpart = headers["X-Navig-Signature"].partition("=")  # "sha256", "=", "<hex>"
    recased = dict(headers)
    recased["X-Navig-Signature"] = f"{prefix}={hexpart.upper()}"  # keep prefix, upper the hex
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/hcase", data=body, headers=headers)
        second = await client.post("/api/ingest/hcase", data=body, headers=recased)
        second_body = await second.json()
    assert first.status == 200
    assert second_body.get("duplicate") is True  # deduped despite the case change
    assert len(feed.list_items()) == 1  # the re-cased replay did NOT dispatch again


async def test_all_channels_failed_is_502_and_retryable(env, monkeypatch):
    """Every enabled channel attempted-and-FAILED → not a phantom 200. The route
    returns a retryable 502 and does NOT mark the replay, so a retry re-attempts."""
    signals, feed = env
    secret = signals.add_source("hfail")["secret"]

    async def _all_failed(*_a, **_k):
        return {"type": "signal:hfail", "priority": "normal",
                "channels": [{"channel": "deck", "ok": False, "detail": "boom"}]}
    monkeypatch.setattr("navig.notify.dispatch", _all_failed)

    from aiohttp.test_utils import TestClient, TestServer
    body = json.dumps({"n": 1}).encode()
    headers = _headers(secret, body)
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/hfail", data=body, headers=headers)
        assert first.status == 502  # NOT a phantom {ok:true, delivered:[]}
        assert (await first.json()).get("ok") is False
        second = await client.post("/api/ingest/hfail", data=body, headers=headers)
        assert second.status == 502
        assert (await second.json()).get("duplicate") is not True  # re-attempted


async def test_muted_empty_channels_acks_ok_not_502(env, monkeypatch):
    """An EMPTY channel list = intentional (master-off / muted), not a failure —
    ack an honest 200 with delivered:[] and don't churn retries."""
    signals, _feed = env
    secret = signals.add_source("hmute")["secret"]

    async def _muted(*_a, **_k):
        return {"type": "signal:hmute", "skipped": "master_off", "channels": []}
    monkeypatch.setattr("navig.notify.dispatch", _muted)

    from aiohttp.test_utils import TestClient, TestServer
    body = json.dumps({"n": 1}).encode()
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/hmute", data=body, headers=_headers(secret, body))
        assert resp.status == 200
        data = await resp.json()
    assert data["ok"] is True and data["delivered"] == []


async def test_dispatch_exception_is_502_and_retryable(env, monkeypatch):
    """If dispatch itself raises (before delivering), the replay mark is rolled
    back so a retry re-attempts rather than being silently deduped."""
    signals, _feed = env
    secret = signals.add_source("hboom")["secret"]

    async def _boom(*_a, **_k):
        raise RuntimeError("dispatch exploded")
    monkeypatch.setattr("navig.notify.dispatch", _boom)

    from aiohttp.test_utils import TestClient, TestServer
    body = json.dumps({"n": 1}).encode()
    headers = _headers(secret, body)
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/hboom", data=body, headers=headers)
        assert first.status == 502
        second = await client.post("/api/ingest/hboom", data=body, headers=headers)
        assert second.status == 502
        assert (await second.json()).get("duplicate") is not True


def test_non_ascii_signature_rejected_cleanly(env):
    """A non-ASCII signature must fail closed as 401 — not crash compare_digest
    into an uncaught 500 (verified at the pure-core level)."""
    signals, _feed = env
    src = signals.add_source("hascii")  # returns the row incl. secret
    ts = str(int(time.time()))
    res = signals.verify_and_render(
        src,
        {"X-Navig-Signature": "sha256=café", "X-Navig-Timestamp": ts},
        b'{"x":1}',
    )
    assert res.ok is False
    assert res.http_status == 401


# ── replay LRU: eviction is by TIME, not just count ──────────────────────────


def test_seen_dedupes_within_window():
    from navig.gateway.routes import ingest

    ingest._SEEN.clear()
    assert ingest._seen("s:sig1", now=0.0) is False  # first sighting
    assert ingest._seen("s:sig1", now=5.0) is True  # replay within the window
    ingest._SEEN.clear()


def test_seen_evicts_after_ttl():
    from navig.gateway.routes import ingest

    ingest._SEEN.clear()
    assert ingest._seen("s:sig1", now=0.0) is False
    # Past the TTL the entry ages out — a re-fire is treated as new (verify's own
    # timestamp-tolerance rejects a genuinely-old ts anyway, so this is safe).
    assert ingest._seen("s:sig1", now=ingest._SEEN_TTL_S + 1.0) is False
    ingest._SEEN.clear()


def test_in_window_sig_survives_a_burst_bigger_than_old_cap():
    """The regression: an in-window signature must NOT be evicted early by the
    count cap under load — that reopened the replay hole. Eviction is by age."""
    from navig.gateway.routes import ingest

    ingest._SEEN.clear()
    assert ingest._seen("s:sigA", now=0.0) is False
    # A burst larger than the OLD count-only cap (2000), all within the window.
    for i in range(3000):
        ingest._seen(f"s:flood{i}", now=1.0)
    # sigA is still in-window → its replay is STILL deduped (not aged out, and the
    # 20k memory backstop hasn't tripped).
    assert ingest._seen("s:sigA", now=2.0) is True
    ingest._SEEN.clear()


# ── Stripe-native sources (provider="stripe") ────────────────────────────────

_WHSEC = "whsec_route_test_0123456789abcdefghij"


def _stripe_headers(body: bytes) -> dict[str, str]:
    t = str(int(time.time()))
    sig = hmac.new(_WHSEC.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def _stripe_checkout(event_id: str = "evt_route_1") -> bytes:
    return json.dumps({
        "id": event_id, "type": "checkout.session.completed", "livemode": True,
        "data": {"object": {"id": "cs_1", "payment_status": "paid", "amount_total": 1999,
                            "currency": "usd", "customer_details": {"email": "bo@shop.io"}}},
    }).encode()


async def test_stripe_sale_lands_as_a_sale_and_redelivery_is_deduped(env):
    """A Stripe redelivery is RE-SIGNED (new t, new v1), so signature dedupe can't
    catch it — the durable event-id claim must."""
    signals, feed = env
    signals.add_source("grabnite", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    body = _stripe_checkout()
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/grabnite", data=body, headers=_stripe_headers(body))
        assert first.status == 200
        assert (await first.json())["type"] == "sale"
        # Same event, fresh signature one second later — exactly what Stripe sends on retry.
        hdr = _stripe_headers(body)
        stale_t = int(time.time()) - 1
        sig = hmac.new(_WHSEC.encode(), f"{stale_t}.".encode() + body, hashlib.sha256).hexdigest()
        hdr["Stripe-Signature"] = f"t={stale_t},v1={sig}"
        second = await client.post("/api/ingest/grabnite", data=body, headers=hdr)
        assert (await second.json()).get("duplicate") is True

    items = feed.list_items()
    assert len(items) == 1
    assert items[0]["type"] == "sale"
    assert items[0]["title"] == "💰 Sale — $19.99"


async def test_stripe_unhandled_event_is_acked_not_retried(env):
    signals, feed = env
    signals.add_source("grabnite", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"id": "evt_x", "type": "customer.created", "data": {"object": {}}}).encode()
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/grabnite", data=body, headers=_stripe_headers(body))
        assert resp.status == 200
        assert (await resp.json())["ignored"] == "customer.created"
    assert feed.list_items() == []


async def test_stripe_bad_signature_is_401(env):
    signals, _feed = env
    signals.add_source("grabnite", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    body = _stripe_checkout()
    hdr = _stripe_headers(body)
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/grabnite", data=body + b" ", headers=hdr)
        assert resp.status == 401


async def test_stripe_failed_delivery_releases_the_claim_so_retry_delivers(env, monkeypatch):
    """Delivery failed → 502 AND the event id is released, so Stripe's retry is
    delivered rather than acked as a duplicate of something that never arrived."""
    signals, _feed = env
    signals.add_source("grabnite", provider="stripe", secret=_WHSEC)

    calls = {"n": 0}

    async def _flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"channels": [{"channel": "telegram", "ok": False}]}
        return {"channels": [{"channel": "telegram", "ok": True}]}
    monkeypatch.setattr("navig.notify.dispatch", _flaky)

    from aiohttp.test_utils import TestClient, TestServer

    body = _stripe_checkout("evt_flaky")
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/grabnite", data=body, headers=_stripe_headers(body))
        assert first.status == 502
        retry = await client.post("/api/ingest/grabnite", data=body, headers=_stripe_headers(body))
        assert retry.status == 200
        assert (await retry.json())["delivered"] == ["telegram"]


async def test_two_different_stripe_sales_both_ring(env):
    """Stripe results carry no NAVIG signature — a signature-keyed dedupe would
    share one empty key across EVERY Stripe event and swallow all sales after
    the first. Distinct event ids must each deliver."""
    signals, feed = env
    signals.add_source("grabnite", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        for evt in ("evt_sale_a", "evt_sale_b"):
            body = _stripe_checkout(evt)
            resp = await client.post("/api/ingest/grabnite", data=body, headers=_stripe_headers(body))
            assert (await resp.json()).get("duplicate") is not True, evt
    assert len(feed.list_items()) == 2


def _stripe_pi(event_id: str, pi_id: str) -> bytes:
    return json.dumps({
        "id": event_id, "type": "payment_intent.succeeded", "livemode": True,
        "data": {"object": {"id": pi_id, "object": "payment_intent", "amount_received": 1999,
                            "currency": "usd", "receipt_email": "bo@shop.io", "invoice": None}},
    }).encode()


async def test_one_payment_as_checkout_and_payment_intent_rings_once(env):
    """An endpoint ticking both checkout.session.completed AND payment_intent.succeeded
    receives the SAME payment twice under two event ids. The route claims the
    underlying PaymentIntent, so the operator hears about it once — in either order."""
    signals, feed = env
    signals.add_source("shop", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    checkout = json.loads(_stripe_checkout("evt_cs_both"))
    checkout["data"]["object"]["payment_intent"] = "pi_both"
    checkout_body = json.dumps(checkout).encode()
    pi_body = _stripe_pi("evt_pi_both", "pi_both")
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/shop", data=pi_body, headers=_stripe_headers(pi_body))
        assert first.status == 200
        assert (await first.json()).get("duplicate") is not True
        second = await client.post(
            "/api/ingest/shop", data=checkout_body, headers=_stripe_headers(checkout_body)
        )
        assert second.status == 200
        assert (await second.json()).get("duplicate") is True
    assert len(feed.list_items()) == 1


async def test_two_different_payment_intents_both_ring(env):
    """The payment claim is per PaymentIntent — it must not merge distinct sales."""
    signals, feed = env
    signals.add_source("shop", provider="stripe", secret=_WHSEC)

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        for evt, pi in (("evt_pi_a", "pi_a"), ("evt_pi_b", "pi_b")):
            body = _stripe_pi(evt, pi)
            resp = await client.post("/api/ingest/shop", data=body, headers=_stripe_headers(body))
            assert (await resp.json()).get("duplicate") is not True, evt
    titles = [i["title"] for i in feed.list_items()]
    assert titles == ["💰 Sale — $19.99", "💰 Sale — $19.99"]


async def test_failed_delivery_releases_the_payment_claim_too(env, monkeypatch):
    """A 502 must release BOTH claims; releasing only the event id would leave the
    payment claimed, and Stripe's retry would be acked as a duplicate of nothing."""
    signals, _feed = env
    signals.add_source("shop", provider="stripe", secret=_WHSEC)

    calls = {"n": 0}

    async def _flaky(*_a, **_k):
        calls["n"] += 1
        ok = calls["n"] > 1
        return {"channels": [{"channel": "telegram", "ok": ok}]}
    monkeypatch.setattr("navig.notify.dispatch", _flaky)

    from aiohttp.test_utils import TestClient, TestServer

    body = _stripe_pi("evt_pi_flaky", "pi_flaky")
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/shop", data=body, headers=_stripe_headers(body))
        assert first.status == 502
        retry = await client.post("/api/ingest/shop", data=body, headers=_stripe_headers(body))
        assert retry.status == 200
        assert (await retry.json())["delivered"] == ["telegram"]


# ── ingest actions: a verified source that DOES something ─────────────────────
async def test_a_claimed_source_runs_its_action_not_the_bell(env):
    signals, feed = env
    secret = signals.add_source("voicedemo")["secret"]
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway import ingest_actions

    seen = []

    async def action(payload):
        seen.append(payload)
        return {"speech": "done"}

    ingest_actions.register("voicedemo", action)
    try:
        body = json.dumps({"intent": "Status"}).encode()
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.post("/api/ingest/voicedemo", data=body, headers=_headers(secret, body))
            assert resp.status == 200
            assert await resp.json() == {"ok": True, "speech": "done"}
            # A replay of the same signed request never runs the action twice.
            again = await client.post("/api/ingest/voicedemo", data=body, headers=_headers(secret, body, resp.request_info.headers["X-Navig-Timestamp"]))
            assert (await again.json()).get("duplicate") is True
            # An unsigned request never reaches it.
            bad = await client.post("/api/ingest/voicedemo", data=body, headers=_headers("sk_sig_wrong", body))
            assert bad.status == 401
    finally:
        ingest_actions.unregister("voicedemo")
    assert seen == [{"intent": "Status"}]
    assert feed.list_items() == []  # no bell for an action


async def test_a_failing_action_is_retryable(env):
    signals, _feed = env
    secret = signals.add_source("voicefail")["secret"]
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway import ingest_actions

    calls = []

    async def action(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError("OBS closed")
        return {"speech": "ok"}

    ingest_actions.register("voicefail", action)
    try:
        body = b'{"intent":"Status"}'
        headers = _headers(secret, body)
        async with TestClient(TestServer(_build_app())) as client:
            first = await client.post("/api/ingest/voicefail", data=body, headers=headers)
            assert first.status == 502
            retry = await client.post("/api/ingest/voicefail", data=body, headers=headers)
            assert retry.status == 200  # the failed delivery was not marked as seen
    finally:
        ingest_actions.unregister("voicefail")
