"""Stripe-native Signal sources — verify a Stripe webhook and render it.

A Signal source with ``provider="stripe"`` is pointed at directly from the Stripe
dashboard (Developers → Webhooks → Add endpoint), so a project needs **zero code**
to get "💰 new sale" on Telegram. Stripe signs each delivery with the endpoint's
``whsec_…`` secret; we verify that instead of the NAVIG ``X-Navig-Signature``.

Why this beats calling the Signals SDK from the app for Stripe-paid projects:
Stripe re-signs and retries a non-2xx delivery for ~3 days, and the Lighthouse
edge answers 503 while the brain is asleep — so a sale made while the PC is off
still arrives once it is back. An app-side call has no such retry.

Pure like ``signals.verify_and_render``: stdlib only, no NAVIG imports, so the
layer stays extractable. The route owns dedupe (keyed on ``event_id``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

SIG_HEADER = "Stripe-Signature"
SECRET_PREFIX = "whsec_"
DEFAULT_TOLERANCE_S = 300

# The events worth ticking in the Stripe dashboard. Anything else verifies, is
# acknowledged with 200 and ignored — so an over-subscribed endpoint never makes
# Stripe retry-storm, it just stays quiet.
#
# Which set depends on HOW the project takes money:
#   checkout — Stripe Checkout (`checkout.sessions.create`) and subscriptions
#   elements — Payment Element / PaymentIntents (`paymentIntents.create`), no Checkout
# Ticking both is safe: one payment surfaces as a Checkout event AND a PaymentIntent
# event, and the ingest route claims the underlying payment (`payment_key`) once.
RECOMMENDED_EVENTS: tuple[str, ...] = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "invoice.paid",
    "invoice.payment_failed",
    "charge.refunded",
    "charge.dispute.created",
    "customer.subscription.deleted",
)
ELEMENTS_EVENTS: tuple[str, ...] = (
    "payment_intent.succeeded",
    "charge.refunded",
    "charge.dispute.created",
)
EVENT_PROFILES: dict[str, tuple[str, ...]] = {
    "checkout": RECOMMENDED_EVENTS,
    "elements": ELEMENTS_EVENTS,
}

# Currencies Stripe expresses in whole units (no minor unit).
_ZERO_DECIMAL = frozenset(
    "bif clp djf gnf jpy kmf krw mga pyg rwf ugx vnd vuv xaf xof xpf".split()
)
_SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£", "jpy": "¥", "inr": "₹", "krw": "₩"}


@dataclass
class StripeVerdict:
    """Signature check outcome. ``ok=False`` carries an HTTP status + reason."""

    ok: bool
    http_status: int = 200
    error: str = ""
    event: dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeRender:
    """One Stripe event rendered as a notification."""

    kind: str  # "sale" (routes to the source's own type) | "alert" (finance_alert)
    title: str
    body: str
    priority: str = "normal"
    data: dict[str, Any] = field(default_factory=dict)


def _header(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v or ""
    return ""


def verify(
    headers: dict[str, str],
    body: bytes,
    secret: str,
    *,
    now: float | None = None,
    tolerance: int = DEFAULT_TOLERANCE_S,
) -> StripeVerdict:
    """Verify ``Stripe-Signature: t=<ts>,v1=<hex>[,v1=<hex>…]`` over ``f"{t}.{body}"``.

    Accepts ANY ``v1`` entry: while a secret is being rolled in the Stripe
    dashboard, Stripe sends one signature per active secret, and only one of
    them is ours.
    """
    if not secret:
        return StripeVerdict(False, 500, "source misconfigured: no signing secret")
    raw = _header(headers, SIG_HEADER)
    if not raw:
        return StripeVerdict(False, 401, "missing Stripe-Signature")

    ts = ""
    candidates: list[str] = []
    for item in raw.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            ts = value
        elif key == "v1" and value:
            candidates.append(value)
    if not ts or not candidates:
        return StripeVerdict(False, 401, "malformed Stripe-Signature")
    try:
        ts_int = int(ts)
    except ValueError:
        return StripeVerdict(False, 401, "bad timestamp")
    if abs((time.time() if now is None else now) - ts_int) > tolerance:
        return StripeVerdict(False, 401, "timestamp out of tolerance")

    expected = hmac.new(
        secret.encode(), ts.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    # compare_digest raises TypeError on non-ASCII str — compare as bytes.
    if not any(
        hmac.compare_digest(expected.encode(), c.encode("utf-8", "replace"))
        for c in candidates
    ):
        return StripeVerdict(False, 401, "bad signature")

    try:
        event = json.loads(body)
    except (ValueError, TypeError):
        return StripeVerdict(False, 400, "invalid JSON body")
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        return StripeVerdict(False, 400, "not a Stripe event")
    return StripeVerdict(True, event=event)


def format_amount(minor: Any, currency: Any) -> str:
    """``1250, "eur"`` → ``€12.50``; zero-decimal currencies stay whole."""
    cur = str(currency or "").lower()
    try:
        value = int(minor)
    except (TypeError, ValueError):
        return "—"
    if cur in _ZERO_DECIMAL:
        number = f"{value:,}"
    else:
        number = f"{value / 100:,.2f}"
    symbol = _SYMBOLS.get(cur)
    if symbol:
        return f"-{symbol}{number[1:]}" if number.startswith("-") else f"{symbol}{number}"
    return f"{number} {cur.upper()}".strip()


def mask_email(email: Any) -> str:
    """``ada@example.com`` → ``a**@example.com`` — enough to recognise, not to harvest."""
    text = str(email or "").strip()
    local, at, domain = text.partition("@")
    if not at or not local:
        return ""
    return f"{local[0]}{'*' * min(len(local) - 1, 6)}@{domain}"


_PRODUCT_KEYS = ("product_name", "product", "item_name", "item", "plan", "title", "name")


def _product(obj: dict[str, Any]) -> str:
    meta = obj.get("metadata") or {}
    if isinstance(meta, dict):
        for k in _PRODUCT_KEYS:
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:80]
    return ""


def _join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


def render(event: dict[str, Any]) -> StripeRender | None:
    """Map one verified Stripe event onto a notification; ``None`` = ignore it.

    Sales come from Checkout (``checkout.session.*``), subscription renewals
    (``invoice.paid`` with ``billing_reason=subscription_cycle``) and, for a
    Payment Element integration with no Checkout, ``payment_intent.succeeded``.
    A first invoice never announces (the checkout sale already did), and a
    PaymentIntent that belongs to an invoice is left to the invoice events.

    One payment can surface twice (a Checkout session completes AND its
    PaymentIntent succeeds), so every sale carries ``payment_key`` — the
    PaymentIntent id — and the ingest route announces each payment once.
    """
    etype = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict):
        return None
    test = "" if event.get("livemode", True) else "[test] "
    base_data = {
        "provider": "stripe",
        "event_id": event.get("id"),
        "event_type": etype,
        "object_id": obj.get("id"),
        "livemode": bool(event.get("livemode", True)),
    }

    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        # An async method (SEPA, bank transfer) completes the session UNPAID and
        # pays later via async_payment_succeeded — announce it only once paid.
        if obj.get("payment_status") != "paid":
            return None
        amount = format_amount(obj.get("amount_total"), obj.get("currency"))
        details = obj.get("customer_details") or {}
        who = mask_email(details.get("email") or obj.get("customer_email"))
        country = str((details.get("address") or {}).get("country") or "")
        product = _product(obj)
        mode = "subscription" if obj.get("mode") == "subscription" else ""
        data = {**base_data, "amount": amount, "customer": who, "product": product}
        if isinstance(obj.get("payment_intent"), str) and obj["payment_intent"]:
            data["payment_key"] = obj["payment_intent"]
        return StripeRender(
            kind="sale",
            title=f"{test}💰 Sale — {amount}" + (f" · {product}" if product else ""),
            body=_join(f"{who} paid {amount}" if who else f"Paid {amount}", mode, country),
            priority="normal",
            data=data,
        )

    if etype == "payment_intent.succeeded":
        # An invoice's PaymentIntent is a subscription payment: the checkout sale
        # (first invoice) or invoice.paid (renewal) announces it.
        if obj.get("invoice"):
            return None
        amount = format_amount(
            obj.get("amount_received") or obj.get("amount"), obj.get("currency")
        )
        meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        who = mask_email(
            obj.get("receipt_email") or meta.get("email") or meta.get("customer_email")
        )
        product = _product(obj) or str(obj.get("description") or "").strip()[:80]
        data = {**base_data, "amount": amount, "customer": who, "product": product}
        if obj.get("id"):
            data["payment_key"] = str(obj["id"])
        return StripeRender(
            kind="sale",
            title=f"{test}💰 Sale — {amount}" + (f" · {product}" if product else ""),
            body=f"{who} paid {amount}" if who else f"Paid {amount}",
            priority="normal",
            data=data,
        )

    if etype == "invoice.paid":
        if obj.get("billing_reason") != "subscription_cycle":
            return None  # the first invoice is the checkout sale above
        amount = format_amount(obj.get("amount_paid"), obj.get("currency"))
        who = mask_email(obj.get("customer_email"))
        return StripeRender(
            kind="sale",
            title=f"{test}🔁 Renewal — {amount}",
            body=_join(f"{who} renewed" if who else "Subscription renewed", amount),
            data={**base_data, "amount": amount, "customer": who},
        )

    if etype == "invoice.payment_failed":
        amount = format_amount(obj.get("amount_due"), obj.get("currency"))
        who = mask_email(obj.get("customer_email"))
        return StripeRender(
            kind="alert",
            title=f"{test}⚠️ Payment failed — {amount}",
            body=_join(who, f"attempt {obj.get('attempt_count') or 1}"),
            priority="high",
            data={**base_data, "amount": amount, "customer": who},
        )

    if etype == "charge.refunded":
        amount = format_amount(obj.get("amount_refunded"), obj.get("currency"))
        who = mask_email((obj.get("billing_details") or {}).get("email") or obj.get("receipt_email"))
        return StripeRender(
            kind="alert",
            title=f"{test}↩️ Refund — {amount}",
            body=_join(f"Refunded to {who}" if who else "Refund issued", amount),
            data={**base_data, "amount": amount, "customer": who},
        )

    if etype == "charge.dispute.created":
        amount = format_amount(obj.get("amount"), obj.get("currency"))
        reason = str(obj.get("reason") or "").replace("_", " ")
        return StripeRender(
            kind="alert",
            title=f"{test}🚩 Dispute opened — {amount}",
            body=_join(reason, "respond in the Stripe dashboard before the deadline"),
            priority="high",
            data={**base_data, "amount": amount, "reason": reason},
        )

    if etype == "customer.subscription.deleted":
        return StripeRender(
            kind="alert",
            title=f"{test}👋 Subscription canceled",
            body=_join(str(obj.get("customer") or ""), str((obj.get("cancellation_details") or {}).get("reason") or "")),
            data=base_data,
        )

    return None


def sample_event(*, now: float | None = None) -> dict[str, Any]:
    """A realistic paid Checkout event — the "test" button renders through this."""
    ts = int(time.time() if now is None else now)
    return {
        "id": f"evt_test_navig_{ts}",
        "object": "event",
        "type": "checkout.session.completed",
        "livemode": False,
        "created": ts,
        "data": {
            "object": {
                "id": "cs_test_navig_sample",
                "object": "checkout.session",
                "mode": "payment",
                "payment_status": "paid",
                "amount_total": 4200,
                "currency": "usd",
                "customer_details": {"email": "ada@example.com", "address": {"country": "GB"}},
                "metadata": {"product_name": "Sample product"},
            }
        },
    }


def sign(secret: str, body: bytes, *, ts: int | None = None) -> str:
    """Build a ``Stripe-Signature`` header value (used by ``navig signals test``)."""
    t = str(int(time.time()) if ts is None else ts)
    sig = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"
