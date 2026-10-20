"""Stripe-native Signal sources: signature verification, event → notification
mapping, the store's provider/edit/rotate rules and durable event dedupe."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

WHSEC = "whsec_test_0123456789abcdefghijklmnop"


@pytest.fixture
def signals(tmp_path, monkeypatch):
    """Isolate notify.db and reset the module init flag."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import signals as sig

    return sig


def _stripe_header(secret: str, body: bytes, ts: int | None = None, *, extra_v1: str = "") -> str:
    t = str(int(time.time()) if ts is None else ts)
    sig = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    head = f"t={t},"
    if extra_v1:
        head += f"v1={extra_v1},"
    return head + f"v1={sig}"


def _checkout(**obj_overrides) -> dict:
    obj = {
        "id": "cs_live_1",
        "mode": "payment",
        "payment_status": "paid",
        "amount_total": 1250,
        "currency": "eur",
        "customer_details": {"email": "ada@example.com", "address": {"country": "FR"}},
        "metadata": {"product_name": "Drop #7"},
    }
    obj.update(obj_overrides)
    return {"id": "evt_1", "type": "checkout.session.completed", "livemode": True,
            "data": {"object": obj}}


# ── Pure layer ────────────────────────────────────────────────────────────────


def test_verify_accepts_valid_and_rejects_tampered():
    from navig.notify import stripe_events as se

    body = json.dumps(_checkout()).encode()
    ok = se.verify({"Stripe-Signature": _stripe_header(WHSEC, body)}, body, WHSEC)
    assert ok.ok and ok.event["id"] == "evt_1"

    bad = se.verify({"Stripe-Signature": _stripe_header("whsec_other", body)}, body, WHSEC)
    assert not bad.ok and bad.http_status == 401

    tampered = body.replace(b"1250", b"9999")
    assert not se.verify({"Stripe-Signature": _stripe_header(WHSEC, body)}, tampered, WHSEC).ok


def test_verify_accepts_any_v1_during_a_secret_roll():
    """While a secret is being rolled, Stripe sends one v1 per active secret."""
    from navig.notify import stripe_events as se

    body = json.dumps(_checkout()).encode()
    header = _stripe_header(WHSEC, body, extra_v1="0" * 64)
    assert se.verify({"stripe-signature": header}, body, WHSEC).ok


def test_verify_rejects_stale_missing_and_non_ascii():
    from navig.notify import stripe_events as se

    body = json.dumps(_checkout()).encode()
    stale = _stripe_header(WHSEC, body, ts=int(time.time()) - 3600)
    assert se.verify({"Stripe-Signature": stale}, body, WHSEC).error == "timestamp out of tolerance"
    assert se.verify({}, body, WHSEC).error == "missing Stripe-Signature"
    assert se.verify({"Stripe-Signature": "t=1"}, body, WHSEC).error == "malformed Stripe-Signature"
    # Non-ASCII must be a clean 401, never a TypeError → 500.
    weird = f"t={int(time.time())},v1=ünïcode"
    assert se.verify({"Stripe-Signature": weird}, body, WHSEC).http_status == 401


def test_format_amount_handles_minor_and_zero_decimal_units():
    from navig.notify.stripe_events import format_amount

    assert format_amount(1250, "eur") == "€12.50"
    assert format_amount(123456, "usd") == "$1,234.56"
    assert format_amount(500, "jpy") == "¥500"
    assert format_amount(999, "cad") == "9.99 CAD"
    assert format_amount(None, "usd") == "—"


def test_checkout_paid_renders_a_sale_with_masked_customer():
    from navig.notify import stripe_events as se

    r = se.render(_checkout())
    assert r is not None and r.kind == "sale"
    assert r.title == "💰 Sale — €12.50 · Drop #7"
    assert "a**@example.com" in r.body and "ada@" not in r.body
    assert "FR" in r.body


def test_unpaid_async_checkout_is_ignored_until_it_pays():
    from navig.notify import stripe_events as se

    assert se.render(_checkout(payment_status="unpaid")) is None
    paid_later = _checkout()
    paid_later["type"] = "checkout.session.async_payment_succeeded"
    assert se.render(paid_later).kind == "sale"


def test_test_mode_events_are_tagged():
    from navig.notify import stripe_events as se

    ev = _checkout()
    ev["livemode"] = False
    assert se.render(ev).title.startswith("[test] ")


def test_first_invoice_is_not_a_second_sale_but_renewal_is():
    from navig.notify import stripe_events as se

    inv = {"id": "evt_2", "type": "invoice.paid", "livemode": True,
           "data": {"object": {"billing_reason": "subscription_create", "amount_paid": 900,
                               "currency": "usd"}}}
    assert se.render(inv) is None
    inv["data"]["object"]["billing_reason"] = "subscription_cycle"
    r = se.render(inv)
    assert r.kind == "sale" and r.title == "🔁 Renewal — $9.00"


def test_refund_and_dispute_are_alerts_and_unknown_types_ignored():
    from navig.notify import stripe_events as se

    refund = {"id": "e", "type": "charge.refunded", "livemode": True,
              "data": {"object": {"amount_refunded": 1250, "currency": "eur"}}}
    dispute = {"id": "e", "type": "charge.dispute.created", "livemode": True,
               "data": {"object": {"amount": 1250, "currency": "eur", "reason": "fraudulent"}}}
    assert se.render(refund).kind == "alert"
    d = se.render(dispute)
    assert d.kind == "alert" and d.priority == "high" and "fraudulent" in d.body
    assert se.render({"id": "e", "type": "customer.created", "data": {"object": {}}}) is None


# ── Store ─────────────────────────────────────────────────────────────────────


def test_stripe_source_requires_a_whsec_secret(signals):
    with pytest.raises(ValueError, match="whsec_"):
        signals.add_source("shop", provider="stripe")
    with pytest.raises(ValueError, match="whsec_"):
        signals.add_source("shop", provider="stripe", secret="sk_live_wrong")
    with pytest.raises(ValueError, match="unknown provider"):
        signals.add_source("shop", provider="paypal", secret=WHSEC)
    with pytest.raises(ValueError, match="provider source"):
        signals.add_source("shop", secret=WHSEC)  # a NAVIG source mints its own


def test_stripe_source_routes_sales_to_shared_sale_type(signals):
    row = signals.add_source("grabnite", provider="stripe", secret=WHSEC)
    assert row["provider"] == "stripe"
    assert row["notify_type"] == "sale"
    assert row["secret"] == WHSEC
    listed = signals.list_sources()[0]
    assert listed["provider"] == "stripe" and listed["secret"] != WHSEC  # masked


def test_verify_and_render_routes_sale_and_alert_types(signals):
    src = signals.get_source(signals.add_source("grabnite", provider="stripe", secret=WHSEC)["name"])
    body = json.dumps(_checkout()).encode()
    res = signals.verify_and_render(src, {"Stripe-Signature": _stripe_header(WHSEC, body)}, body)
    assert res.ok and not res.ignored
    assert res.notify_type == "sale" and res.event_id == "evt_1"
    assert res.data["source"] == "grabnite"

    refund = json.dumps({"id": "evt_r", "type": "charge.refunded", "livemode": True,
                         "data": {"object": {"amount_refunded": 5, "currency": "usd"}}}).encode()
    res = signals.verify_and_render(src, {"Stripe-Signature": _stripe_header(WHSEC, refund)}, refund)
    assert res.notify_type == signals.PROVIDER_ALERT_TYPE

    other = json.dumps({"id": "evt_o", "type": "customer.created", "data": {"object": {}}}).encode()
    res = signals.verify_and_render(src, {"Stripe-Signature": _stripe_header(WHSEC, other)}, other)
    assert res.ok and res.ignored


def test_navig_signature_does_not_open_a_stripe_source(signals):
    """A stripe source must not accept the NAVIG HMAC scheme with the same secret."""
    src = signals.get_source(signals.add_source("grabnite", provider="stripe", secret=WHSEC)["name"])
    body = json.dumps(_checkout()).encode()
    ts = str(int(time.time()))
    navig_sig = "sha256=" + hmac.new(WHSEC.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    res = signals.verify_and_render(
        src, {"X-Navig-Signature": navig_sig, "X-Navig-Timestamp": ts}, body
    )
    assert not res.ok and res.http_status == 401


def test_update_source_edits_fields_and_moves_matrix_row(signals):
    from navig.notify import prefs

    signals.add_source("shop", preset="payment_success")
    row = signals.update_source("shop", priority="high", title_tmpl="Paid {amount}")
    assert row["priority"] == "high" and row["title_tmpl"] == "Paid {amount}"

    row = signals.update_source("shop", title_tmpl="")
    assert row["title_tmpl"] is None  # "" clears

    row = signals.update_source("shop", notify_type="sale")
    assert row["notify_type"] == "sale"
    assert "signal:shop" not in {t for t in prefs.get_matrix()}

    row = signals.update_source("shop", notify_type="")
    assert row["notify_type"] == "signal:shop"
    assert "signal:shop" in {t for t in prefs.get_matrix()}

    with pytest.raises(ValueError, match="unknown priority"):
        signals.update_source("shop", priority="urgent")
    with pytest.raises(ValueError, match="unknown notification type"):
        signals.update_source("shop", notify_type="signal:someone-else")
    with pytest.raises(ValueError, match="unknown signal source"):
        signals.update_source("nope", priority="low")


def test_rotate_stripe_source_takes_the_new_whsec(signals):
    signals.add_source("grabnite", provider="stripe", secret=WHSEC)
    with pytest.raises(ValueError, match="whsec_"):
        signals.rotate_secret("grabnite")  # we can't mint a Stripe secret
    new = "whsec_rolled_abcdefghijklmnopqrstuvwxyz"
    assert signals.rotate_secret("grabnite", new) == new
    assert signals.get_source("grabnite")["secret"] == new

    signals.add_source("shop")
    with pytest.raises(ValueError, match="generated secret"):
        signals.rotate_secret("shop", new)
    assert signals.rotate_secret("shop").startswith("sk_sig_")


def test_claim_event_is_durable_and_releasable(signals, monkeypatch):
    from navig.notify import store

    assert signals.claim_event("grabnite", "evt_1") is True
    assert signals.claim_event("grabnite", "evt_1") is False
    # Survives a "restart": a fresh init does not forget delivered events.
    monkeypatch.setattr(store, "_initialised", False)
    assert signals.claim_event("grabnite", "evt_1") is False
    signals.release_event("grabnite", "evt_1")
    assert signals.claim_event("grabnite", "evt_1") is True
    # Same event id under another source is a different event.
    assert signals.claim_event("other", "evt_1") is True


def test_remove_source_forgets_its_seen_events(signals):
    signals.add_source("grabnite", provider="stripe", secret=WHSEC)
    signals.claim_event("grabnite", "evt_1")
    signals.remove_source("grabnite")
    assert signals.claim_event("grabnite", "evt_1") is True


def test_render_test_uses_a_stripe_shaped_sample(signals):
    src = signals.get_source(signals.add_source("grabnite", provider="stripe", secret=WHSEC)["name"])
    nt, title, body, _prio, data = signals.render_test(src)
    assert nt == "sale" and title.startswith("[test] 💰 Sale — $42.00")
    assert data["provider"] == "stripe"


def test_legacy_rows_without_provider_column_still_load(tmp_path, monkeypatch):
    """A notify.db created before `provider` existed migrates additively."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    db = store._db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    raw = sqlite3.connect(db)
    raw.execute(
        "CREATE TABLE notify_signal_sources (name TEXT PRIMARY KEY, secret TEXT NOT NULL, "
        "enabled INTEGER NOT NULL DEFAULT 1, notify_type TEXT NOT NULL DEFAULT 'signal_event', "
        "priority TEXT NOT NULL DEFAULT 'normal', title_tmpl TEXT, body_tmpl TEXT, preset TEXT, "
        "created_at TEXT NOT NULL, last_event_at TEXT, hit_count INTEGER NOT NULL DEFAULT 0)"
    )
    raw.execute(
        "INSERT INTO notify_signal_sources (name, secret, created_at) "
        "VALUES ('old', 'sk_sig_legacy_secret_value_123', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import signals

    row = signals.get_source("old")
    assert row is not None and row["provider"] is None


# ── Payment Element / PaymentIntents (no Checkout) ───────────────────────────


def _payment_intent(**obj_overrides) -> dict:
    obj = {
        "id": "pi_live_1",
        "object": "payment_intent",
        "amount": 900,
        "amount_received": 900,
        "currency": "usd",
        "receipt_email": "ada@example.com",
        "description": "100 credits",
        "invoice": None,
        "metadata": {},
    }
    obj.update(obj_overrides)
    return {"id": "evt_pi_1", "type": "payment_intent.succeeded", "livemode": True,
            "data": {"object": obj}}


def test_payment_intent_succeeded_is_a_sale_keyed_on_the_payment():
    from navig.notify import stripe_events

    r = stripe_events.render(_payment_intent())
    assert r is not None and r.kind == "sale"
    assert r.title == "💰 Sale — $9.00 · 100 credits"
    assert r.body == "a**@example.com paid $9.00"
    assert r.data["payment_key"] == "pi_live_1"


def test_payment_intent_product_prefers_metadata_over_description():
    from navig.notify import stripe_events

    r = stripe_events.render(_payment_intent(metadata={"product_name": "Pro pack"}))
    assert r is not None and r.title.endswith("· Pro pack")


def test_an_invoices_payment_intent_is_left_to_the_invoice_events():
    """A subscription's PaymentIntent is the checkout sale (first invoice) or a
    renewal (invoice.paid) — announcing it here would ring the same money twice."""
    from navig.notify import stripe_events

    assert stripe_events.render(_payment_intent(invoice="in_123")) is None


def test_checkout_sale_carries_its_payment_intent_as_the_payment_key():
    from navig.notify import stripe_events

    r = stripe_events.render(_checkout(payment_intent="pi_from_checkout"))
    assert r is not None and r.data["payment_key"] == "pi_from_checkout"
    # A subscription-mode session has no PaymentIntent of its own: no key, no claim.
    r = stripe_events.render(_checkout(mode="subscription", payment_intent=None))
    assert r is not None and "payment_key" not in r.data


def test_event_profiles_cover_both_integrations():
    from navig.notify import stripe_events

    assert stripe_events.EVENT_PROFILES["checkout"] == stripe_events.RECOMMENDED_EVENTS
    elements = stripe_events.EVENT_PROFILES["elements"]
    assert "payment_intent.succeeded" in elements
    assert "checkout.session.completed" not in elements
    assert {"charge.refunded", "charge.dispute.created"} <= set(elements)


def test_normalize_name_is_the_rule_add_source_applies(signals):
    assert signals.normalize_name("  GrabNite ") == "grabnite"
    with pytest.raises(ValueError):
        signals.normalize_name("x")
    with pytest.raises(ValueError):
        signals.add_source("-bad", provider="stripe", secret=WHSEC)
