"""Tests for inbound Signals: the per-source store + the pure verify_and_render
core (HMAC pass/fail, timestamp replay window, template rendering)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest


@pytest.fixture
def signals(tmp_path, monkeypatch):
    """Isolate notify.db and reset the module init flag."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import signals as sig

    return sig


def _sign(secret: str, body: bytes, ts: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


# ── Store ─────────────────────────────────────────────────────────────────────


def test_add_list_get_remove(signals):
    row = signals.add_source("stripe-prod", priority="high")
    assert row["name"] == "stripe-prod"
    assert row["secret"].startswith("sk_sig_")
    assert row["priority"] == "high"

    # list masks the secret; get returns it raw (for the verifier).
    listed = signals.list_sources()
    assert len(listed) == 1
    assert listed[0]["secret"] != row["secret"]
    assert "…" in listed[0]["secret"]
    assert signals.get_source("stripe-prod")["secret"] == row["secret"]

    assert signals.remove_source("stripe-prod") is True
    assert signals.get_source("stripe-prod") is None
    assert signals.remove_source("stripe-prod") is False


def test_add_validates(signals):
    with pytest.raises(ValueError):
        signals.add_source("Bad Name!")  # spaces/caps/punct
    with pytest.raises(ValueError):
        signals.add_source("ok", notify_type="nope")
    with pytest.raises(ValueError):
        signals.add_source("ok2", priority="urgent")
    signals.add_source("dupe")
    with pytest.raises(ValueError):
        signals.add_source("dupe")  # already exists


def test_rotate_and_hits(signals):
    s1 = signals.add_source("deploys")["secret"]
    s2 = signals.rotate_secret("deploys")
    assert s1 != s2
    assert signals.get_source("deploys")["secret"] == s2
    with pytest.raises(ValueError):
        signals.rotate_secret("ghost")

    assert signals.get_source("deploys")["hit_count"] == 0
    signals.record_hit("deploys")
    signals.record_hit("deploys")
    row = signals.get_source("deploys")
    assert row["hit_count"] == 2 and row["last_event_at"]


# ── verify_and_render (pure core) ─────────────────────────────────────────────


def test_verify_good_signature_and_default_render(signals):
    src = signals.add_source("hook")
    body = json.dumps({"event": "paid", "amount": 42}).encode()
    ts = "1000000"
    headers = {"X-Navig-Timestamp": ts, "X-Navig-Signature": _sign(src["secret"], body, ts)}

    res = signals.verify_and_render(src, headers, body, now=1000010)
    assert res.ok is True
    assert res.notify_type == "signal:hook"  # routes to its own per-source row
    assert res.title == "Signal: hook"
    assert res.data["source"] == "hook"
    assert res.data["payload"]["amount"] == 42


def test_verify_rejects_bad_signature(signals):
    src = signals.add_source("hook")
    body = b'{"x":1}'
    ts = "1000000"
    headers = {"X-Navig-Timestamp": ts, "X-Navig-Signature": _sign(src["secret"], body, ts)}
    # Tamper one byte of the body → MAC no longer matches.
    res = signals.verify_and_render(src, headers, b'{"x":2}', now=1000010)
    assert res.ok is False and res.http_status == 401


def test_verify_rejects_stale_timestamp(signals):
    src = signals.add_source("hook")
    body = b"{}"
    ts = "1000000"
    headers = {"X-Navig-Timestamp": ts, "X-Navig-Signature": _sign(src["secret"], body, ts)}
    # 10 minutes later — outside the 5-minute window (replay defence).
    res = signals.verify_and_render(src, headers, body, now=1000000 + 600)
    assert res.ok is False and res.http_status == 401
    assert "tolerance" in res.error


def test_verify_requires_headers(signals):
    src = signals.add_source("hook")
    res = signals.verify_and_render(src, {}, b"{}", now=1000000)
    assert res.ok is False and res.http_status == 401


def test_preset_fills_templates_and_seeds_row(signals):
    from navig.notify import prefs

    row = signals.add_source("stripe", preset="payment_success")
    assert row["preset"] == "payment_success"
    assert row["emoji"] == "💰"
    assert row["priority"] == "normal"
    assert "{amount}" in row["title_tmpl"]
    # Routes to its own mutable row, seeded deck+telegram by default.
    assert row["notify_type"] == "signal:stripe"
    assert set(prefs.enabled_channels("signal:stripe")) == {"deck", "telegram"}

    # It shows up as a dynamic matrix row for the deck, under Signals.
    dyn = signals.dynamic_types()
    assert any(t["key"] == "signal:stripe" and t["category"] == "Signals" for t in dyn)


def test_unknown_preset_rejected(signals):
    with pytest.raises(ValueError):
        signals.add_source("x", preset="not_a_preset")


def test_render_event_shared_by_real_and_test_paths(signals):
    # The deck "Test" button renders through render_event with SAMPLE_PAYLOAD —
    # same path a real signed event takes, so a passing test means real events render too.
    src = signals.add_source("pay", preset="payment_success")
    nt, title, body, prio, data = signals.render_event(src, signals.SAMPLE_PAYLOAD)
    assert nt == "signal:pay"
    assert "$42" in title  # {amount} filled from the sample payload
    assert data["source"] == "pay"
    assert prio == "normal"


def test_per_source_mute_and_cleanup(signals):
    from navig.notify import prefs

    signals.add_source("errors", preset="error")
    assert "deck" in prefs.enabled_channels("signal:errors")
    # Mute the deck channel for just this source.
    prefs.set_cell("signal:errors", "deck", False)
    assert "deck" not in prefs.enabled_channels("signal:errors")
    # Removing the source cleans up its matrix rows entirely.
    signals.remove_source("errors")
    assert prefs.enabled_channels("signal:errors") == []


def test_explicit_type_routes_to_existing_category(signals):
    row = signals.add_source("alarms", notify_type="security_alert")
    assert row["notify_type"] == "security_alert"  # no per-source row created
    assert signals.dynamic_types() == []


def test_verify_renders_templates(signals):
    src = signals.add_source(
        "pay",
        title_tmpl="💰 {amount} from {customer}",
        body_tmpl="plan={plan}, missing={nope}",
    )
    body = json.dumps({"amount": "$9", "customer": "Ada", "plan": "pro"}).encode()
    ts = "1000000"
    headers = {"X-Navig-Timestamp": ts, "X-Navig-Signature": _sign(src["secret"], body, ts)}
    res = signals.verify_and_render(src, headers, body, now=1000005)
    assert res.title == "💰 $9 from Ada"
    # Missing keys render empty rather than blowing up.
    assert res.body == "plan=pro, missing="
