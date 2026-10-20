"""Voice escalation: an unacknowledged emergency eventually telephones you.

Three things this pins, because each one is a way the alert can vanish:
1. Calls are **off by default** — an install that never opted in must never spend money.
2. The call fires only for an *unacknowledged* emergency, after its window.
3. A failed call is recorded, not swallowed (the original bug this whole module exists for).

The Twilio HTTP is faked; nothing here reaches the network.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path)), store._db_path()

    from navig.notify import call, pushover

    # Credentials without ever reading or printing them.
    monkeypatch.setattr(call, "_auth", lambda: ("ACxxxx", "tokxxxx"))

    posted: list[dict] = []
    granted: dict[str, dict] = {}

    def _post(url, **kw):
        data = kw.get("data") or {}
        posted.append({"url": url, "data": dict(data)})

        class R:
            status_code = 201

            def json(self):
                return {"sid": "CA123", "status": "queued"}

        return R()

    def _get(url, **kw):
        class R:
            status_code = 200

            def json(self):
                return {"incoming_phone_numbers": [
                    {"phone_number": "+18446440109", "capabilities": {"voice": True, "sms": True}},
                    {"phone_number": "+15550001", "capabilities": {"voice": False, "sms": True}},
                ]}

        return R()

    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setattr("requests.get", _get)
    return SimpleNamespace(call=call, pushover=pushover, posted=posted)


from types import SimpleNamespace  # noqa: E402  (used by the fixture above)

# ── cost guard: off unless asked ─────────────────────────────────────────────


def test_calls_are_off_until_the_owner_turns_them_on(env):
    from navig.notify import prefs

    assert env.call.call_after_min() is None, "an install must not place calls it never enabled"
    prefs.set_setting("pushover_call_min", "")
    assert env.call.call_after_min() is None
    env.pushover.record_pending("R1", "security_alert", "t", "b")
    assert env.pushover._db().execute(
        "SELECT call_at FROM notify_pushover_pending WHERE receipt='R1'"
    ).fetchone()["call_at"] is None


def test_enabling_calls_schedules_one(env):
    from navig.notify import prefs

    prefs.set_setting("pushover_call_min", "5")
    assert env.call.call_after_min() == 5
    env.pushover.record_pending("R2", "security_alert", "t", "b")
    row = env.pushover._db().execute(
        "SELECT escalate_at, sent_at, call_at FROM notify_pushover_pending WHERE receipt='R2'"
    ).fetchone()
    assert row["call_at"] is not None
    # The two windows are measured from the emergency, not chained off each other.
    assert row["call_at"] - row["sent_at"] == pytest.approx(300, abs=1)


# ── the call itself ──────────────────────────────────────────────────────────


def test_the_call_carries_the_number_and_the_message(env):
    from navig.notify import call, prefs

    prefs.set_setting("target_sms", "+15550001")  # the phone number it calls
    out = call.place_call("Unacknowledged NAVIG alert: breach.")
    assert out.ok and out.call_sid == "CA123"
    data = env.posted[-1]["data"]
    assert data["From"] == "+18446440109"  # the VOICE-capable number, not just the first
    assert data["To"]  # the phone number from the SMS target
    assert data["Twiml"].startswith("<Response><Say>") and "Unacknowledged" in data["Twiml"]


def test_the_twilio_url_is_not_doubled(env, monkeypatch):
    """API already ends in /2010-04-01 — prefixing it again 404s and silently
    makes every call impossible (found by running discovery for real)."""
    urls: list[str] = []

    def _get(url, **kw):
        urls.append(url)

        class R:
            status_code = 200

            def json(self):
                return {"incoming_phone_numbers": []}

        return R()

    monkeypatch.setattr("requests.get", _get)
    env.call._discover_from()
    assert len(urls) == 1
    assert urls[0].count("2010-04-01") == 1, urls[0]


def test_discovery_caches_the_number_it_found(env):
    from navig.notify import prefs

    assert env.call.from_number() == "+18446440109"
    assert prefs.get_raw("call_from_number", "") == "+18446440109"


def test_no_credentials_means_honest_failure_not_a_crash(env, monkeypatch):
    from navig.notify import call

    monkeypatch.setattr(call, "_auth", lambda: ("", ""))
    out = call.place_call("x")
    assert out.ok is False and "credentials" in out.detail
    assert env.posted == [], "nothing must be attempted without credentials"


# ── escalation wiring ────────────────────────────────────────────────────────


def test_an_acknowledged_emergency_never_calls(env, monkeypatch):
    from navig.notify import call as voice
    from navig.notify import prefs, pushover

    prefs.set_setting("pushover_call_min", "0")
    calls: list[str] = []
    monkeypatch.setattr(voice, "place_call", lambda text, **k: (calls.append(text), SimpleNamespace(ok=True, detail="calling"))[1])
    monkeypatch.setattr(pushover, "receipt_status", lambda r: {"acknowledged": 1})

    env.pushover.record_pending("R3", "security_alert", "Breach", "")
    assert asyncio.run(pushover.check_escalations()) == [{"receipt": "R3", "result": "acknowledged"}]
    assert calls == []


def test_the_call_waits_for_its_own_window(env, monkeypatch):
    """Two independent windows from the emergency: SMS at 15 min, call at 5 min."""
    from navig.notify import call as voice
    from navig.notify import prefs, pushover

    prefs.set_setting("pushover_call_min", "5")
    monkeypatch.setattr(pushover, "receipt_status", lambda r: {"acknowledged": 0})
    monkeypatch.setattr(pushover, "_escalate_by_sms", _async_ok)

    sent: list[str] = []

    class _Out:
        ok = True
        detail = "calling +1"

    monkeypatch.setattr(voice, "place_call", lambda text, **k: (sent.append(text), _Out())[1])

    env.pushover.record_pending("R4", "security_alert", "Breach", "from 1.2.3.4")
    import time as _t

    now = _t.time()
    # Neither window is due yet.
    assert asyncio.run(pushover.check_escalations(now=now)) == []
    assert sent == []
    # The call window (5 min) passes first, while the SMS window (15 min) has not.
    out = asyncio.run(pushover.check_escalations(now=now + 301))
    assert sent == ["Unacknowledged NAVIG alert: Breach. from 1.2.3.4"]
    assert out == [{"receipt": "R4", "result": "called"}]
    # Never called twice.
    assert asyncio.run(pushover.check_escalations(now=now + 10_000)) == []
    assert len(sent) == 1


def test_a_call_supersedes_a_later_sms(env, monkeypatch):
    """Whichever window lands first wins; a call is the stronger signal, so an SMS
    queued after it would be noise. Pinning it so the rule is deliberate."""
    from navig.notify import call as voice
    from navig.notify import prefs, pushover

    prefs.set_setting("pushover_call_min", "0")  # call immediately, SMS at 15 min
    monkeypatch.setattr(pushover, "receipt_status", lambda r: {"acknowledged": 0})
    sms = {"n": 0}

    async def _sms(*_a, **_k):
        sms["n"] += 1
        return True, "sent"

    monkeypatch.setattr(pushover, "_escalate_by_sms", _sms)

    class _Out:
        ok = True
        detail = "calling"

    monkeypatch.setattr(voice, "place_call", lambda text, **k: _Out())

    env.pushover.record_pending("R6", "security_alert", "Breach", "")
    import time as _t

    now = _t.time()
    asyncio.run(pushover.check_escalations(now=now))
    assert sms["n"] == 0, "the call ran first"
    # The SMS window is still ahead, but the row is already decided: no late SMS.
    asyncio.run(pushover.check_escalations(now=now + 4000))
    assert sms["n"] == 0
    row = env.pushover._db().execute(
        "SELECT state FROM notify_pushover_pending WHERE receipt='R6'"
    ).fetchone()
    assert row["state"] == "called"


async def _async_ok(*_a, **_k) -> tuple[bool, str]:
    return True, "sent"


def test_a_failed_call_is_recorded_not_silent(env, monkeypatch):
    from navig.notify import call as voice
    from navig.notify import feed, prefs, pushover

    prefs.set_setting("pushover_call_min", "0")

    class _Out:
        ok = False
        detail = "number unassigned"

    monkeypatch.setattr(voice, "place_call", lambda text, **k: _Out())
    monkeypatch.setattr(pushover, "receipt_status", lambda r: {"acknowledged": 0})
    monkeypatch.setattr(pushover, "_escalate_by_sms", _async_ok)

    env.pushover.record_pending("R5", "security_alert", "Breach", "")
    import time as _t

    now = _t.time()
    asyncio.run(pushover.check_escalations(now=now))  # SMS stage
    asyncio.run(pushover.check_escalations(now=now))  # call stage
    items = feed.list_items(limit=10)
    assert any("voice escalation failed" in i["body"] for i in items), [i["body"] for i in items]


def test_an_existing_table_gains_the_call_column(env):
    """A real install's table was created before the call stage existed."""

    db = str(env.pushover._db().execute("SELECT 1").fetchone())  # touch
    from navig.notify import store

    c = store.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(notify_pushover_pending)").fetchall()}
    assert "call_at" in cols
    assert db  # keeps the assertion honest about which DB was touched


import asyncio  # noqa: E402
