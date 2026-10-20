"""Pushover: emergency priority (rings on silent, repeats until acknowledged) only
for the priorities the operator chose, credentials from the vault, and honest
failures. No network — the API is faked."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def po(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path))

    from navig.notify import pushover

    calls: list[tuple[str, dict]] = []
    replies: dict[str, dict] = {
        "messages.json": {"status": 1, "request": "r1"},
        "users/validate.json": {"status": 1, "devices": ["iphone"]},
    }

    def _post(path, data):
        calls.append((path, dict(data)))
        return replies[path]

    creds = {"token": "atoken", "user": "ukey"}
    monkeypatch.setattr(pushover, "_post", _post)
    monkeypatch.setattr(pushover, "credentials", lambda: (creds["token"], creds["user"]))
    return pushover, calls, replies, creds


@pytest.mark.parametrize(
    ("priority", "level", "expected"),
    [
        ("critical", "high", 2), ("high", "high", 2), ("normal", "high", 0), ("low", "high", -1),
        ("critical", "critical", 2), ("high", "critical", 1),
        ("critical", "never", 1), ("high", "never", 1),
    ],
)
def test_priority_mapping(po, priority, level, expected):
    pushover, *_ = po
    assert pushover.pushover_priority(priority, level) == expected


def test_an_emergency_send_repeats_until_acknowledged(po):
    pushover, calls, replies, _ = po
    replies["messages.json"] = {"status": 1, "receipt": "rcpt1"}
    out = pushover.send("Down", "api.navig.run is down", priority=2, sound_name="siren")
    assert out.ok and out.receipt == "rcpt1" and "acknowledged" in out.detail
    path, data = calls[-1]
    assert path == "messages.json"
    assert data["priority"] == 2 and data["retry"] == pushover.RETRY_SEC and data["expire"] == pushover.EXPIRE_SEC
    assert data["sound"] == "siren" and data["token"] == "atoken" and data["user"] == "ukey"


def test_a_normal_send_has_no_retry(po):
    pushover, calls, *_ = po
    assert pushover.send("hi", "there").ok
    assert "retry" not in calls[-1][1] and calls[-1][1]["priority"] == 0


def test_not_configured_is_a_failure_not_a_phantom_send(po):
    pushover, calls, _replies, creds = po
    creds["token"] = ""
    out = pushover.send("x", "y")
    assert out.ok is False and "not configured" in out.detail and calls == []


def test_pushover_errors_are_reported(po):
    pushover, _calls, replies, _ = po
    replies["messages.json"] = {"status": 0, "errors": ["user identifier is invalid"]}
    out = pushover.send("x", "y")
    assert out.ok is False and out.detail == "user identifier is invalid"


def test_validate(po):
    pushover, _calls, replies, _ = po
    assert pushover.validate("t", "u").devices == ("iphone",)
    replies["users/validate.json"] = {"status": 0, "errors": ["application token is invalid"]}
    out = pushover.validate("t", "u")
    assert out.ok is False and "token is invalid" in out.detail
    assert pushover.validate("", "u").ok is False


def test_the_router_delivers_a_critical_type_as_an_emergency(po, monkeypatch):
    pushover, calls, *_ = po
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    for ch in ("deck", "telegram", "sms"):
        prefs.set_cell("security_alert", ch, False)
    prefs.set_cell("security_alert", "pushover", True)
    out = asyncio.run(NotificationRouter().dispatch("security_alert", "SSH brute force", "from 1.2.3.4", priority="critical"))
    assert out["channels"] == [{"channel": "pushover", "ok": True, "detail": "sent"}]
    data = calls[-1][1]
    assert data["priority"] == 2 and data["title"].endswith("SSH brute force")


def test_the_emergency_level_setting_is_honoured(po):
    pushover, *_ = po
    from navig.notify import prefs

    assert pushover.emergency_level() == "high"
    prefs.set_setting("pushover_emergency", "critical")
    assert pushover.pushover_priority("high") == 1
    prefs.set_setting("pushover_emergency", "bogus")
    assert pushover.emergency_level() == "high"


def test_no_type_defaults_to_pushover(po):
    """An alarm nobody asked for is noise: Pushover is opt-in per type."""
    from navig.notify import prefs
    from navig.notify.types import TYPE_KEYS

    assert [t for t in TYPE_KEYS if prefs.is_enabled(t, "pushover")] == []


def test_valid_keys_without_a_device_are_still_accepted(po):
    """The phone app not being logged in yet is not a reason to throw away right keys."""
    pushover, _calls, replies, _ = po
    replies["users/validate.json"] = {"status": 0, "errors": ["user is valid but has no active devices"]}
    out = pushover.validate("t", "u")
    assert out.ok is True and "no device" in out.detail


def _emergency(po, monkeypatch, *, acked: int, sms_ok: bool = True, sms_target: str = "+15550001"):
    pushover, calls, replies, _ = po
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    replies["messages.json"] = {"status": 1, "receipt": "R1"}
    monkeypatch.setattr(pushover, "receipt_status", lambda r: {"acknowledged": acked})
    sms: list[tuple] = []

    async def _send_one(self, channel, type_key, title, body, priority, data, **_kw):
        sms.append((channel, title, body))
        return sms_ok, ("sent" if sms_ok else "twilio down")

    monkeypatch.setattr(NotificationRouter, "_send_one", _send_one)
    prefs.set_setting("target_sms", sms_target)
    ok, _detail = pushover.send_notification("security_alert", "SSH brute force", "from 1.2.3.4", "critical")
    assert ok
    return pushover, sms


def test_an_unacknowledged_emergency_escalates_to_sms_once(po, monkeypatch):
    pushover, sms = _emergency(po, monkeypatch, acked=0)
    import time

    assert asyncio.run(pushover.check_escalations(now=time.time() + 60)) == []  # not due yet
    later = time.time() + 16 * 60
    assert asyncio.run(pushover.check_escalations(now=later)) == [{"receipt": "R1", "result": "escalated"}]
    assert sms and sms[0][0] == "sms" and "SSH brute force" in sms[0][1]
    assert "escalated to SMS" in sms[0][2]
    assert asyncio.run(pushover.check_escalations(now=later + 60)) == []  # never twice


def test_an_acknowledged_emergency_does_not_escalate(po, monkeypatch):
    pushover, sms = _emergency(po, monkeypatch, acked=1)
    import time

    out = asyncio.run(pushover.check_escalations(now=time.time() + 16 * 60))
    assert out == [{"receipt": "R1", "result": "acknowledged"}] and sms == []


def test_a_failed_escalation_is_recorded_not_silent(po, monkeypatch):
    pushover, _sms = _emergency(po, monkeypatch, acked=0, sms_target="")
    import time

    from navig.notify import feed

    out = asyncio.run(pushover.check_escalations(now=time.time() + 16 * 60))
    assert out[0]["result"].startswith("failed: no SMS number")
    assert any("Unacknowledged emergency" in i["title"] for i in feed.list_items(limit=10))


def test_escalation_can_be_switched_off(po, monkeypatch):
    from navig.notify import prefs

    prefs.set_setting("pushover_escalate_min", "0")
    pushover, sms = _emergency(po, monkeypatch, acked=0)
    import time

    assert asyncio.run(pushover.check_escalations(now=time.time() + 99 * 60)) == [] and sms == []


def test_a_pushover_emergency_rings_through_quiet_hours(po, monkeypatch):
    pushover, calls, *_ = po
    from datetime import datetime

    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    h = datetime.now().hour
    prefs.set_setting("quiet_hours_enabled", True)
    prefs.set_setting("quiet_hours_start", h)
    prefs.set_setting("quiet_hours_end", (h + 1) % 24)
    for ch in ("deck", "telegram", "sms"):
        prefs.set_cell("security_alert", ch, ch == "telegram")
    prefs.set_cell("security_alert", "pushover", True)

    # high + default level "high" = emergency → Pushover passes, Telegram stays quiet.
    out = asyncio.run(NotificationRouter().dispatch("security_alert", "x", "y", priority="high"))
    assert [c["channel"] for c in out["channels"]] == ["pushover"]
    # normal = not an emergency → quiet hours silence Pushover too.
    out = asyncio.run(NotificationRouter().dispatch("security_alert", "x", "y", priority="normal"))
    assert out["channels"] == []


def test_deploys_no_longer_buzz_the_phone_by_default():
    from navig.notify.types import NOTIFICATION_TYPES

    deploy = next(t for t in NOTIFICATION_TYPES if t["key"] == "deploy")
    assert deploy["default_channels"] == ["deck"]
