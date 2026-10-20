"""A notification can reach SMS/WhatsApp from ANY process, and the Deck's channel
status says what a send would actually do.

Measured on the operator's daemon: the Telegram worker serves the Deck (8789) and
delivers about half of the due reminders, but only `navig gateway start` (8790)
registered messaging adapters. In the worker `_send_one("sms", ...)` returned
"sms adapter not enabled", so an appointment reminder delivered there silently lost
its SMS copy — and the Mini App showed SMS and Telegram as "not configured" on an
install where both worked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path))

    # A fresh process: an EMPTY adapter registry, exactly like the worker / a CLI.
    from navig.messaging import adapter_registry

    monkeypatch.setattr(adapter_registry, "_registry", None)

    from navig.messaging import adapter_setup

    sent: list[tuple[str, str]] = []

    from navig.messaging.adapter import ComplianceMode

    class FakeSms:
        name = "sms"
        compliance = ComplianceMode.OFFICIAL

        async def send_message(self, target, text, **_kw):
            sent.append((target, text))
            return SimpleNamespace(ok=True, status=SimpleNamespace(value="sent"), error=None)

    built: list[str] = []

    def fake_build(name, adapters_cfg):
        built.append(name)
        if name == "sms" and (adapters_cfg.get("sms") or {}).get("enabled"):
            return FakeSms(), ""
        return None, f"{name} is off (adapters.{name}.enabled)"

    cfg = {"sms": {"enabled": True}}
    monkeypatch.setattr(adapter_setup, "build", fake_build)
    monkeypatch.setattr(adapter_setup, "_adapters_config", lambda: cfg)

    return SimpleNamespace(sent=sent, built=built, cfg=cfg)


def test_an_sms_send_from_a_fresh_process_builds_the_adapter(env):
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    prefs.set_setting("target_sms", "+15550001")
    ok, detail = asyncio.run(
        NotificationRouter()._send_one("sms", "appointment", "Dentist", "9:00", "normal", {})
    )
    assert ok is True, detail
    assert env.sent and env.sent[0][0] == "+15550001" and "Dentist" in env.sent[0][1]
    assert env.built == ["sms"]

    # Built once per process, not once per send.
    asyncio.run(NotificationRouter()._send_one("sms", "appointment", "x", "", "normal", {}))
    assert env.built == ["sms"]


def test_sms_switched_off_says_so_instead_of_a_generic_error(env):
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    env.cfg["sms"]["enabled"] = False
    prefs.set_setting("target_sms", "+15550001")
    ok, detail = asyncio.run(
        NotificationRouter()._send_one("sms", "appointment", "x", "", "normal", {})
    )
    assert ok is False and "adapters.sms.enabled" in detail and env.sent == []


def test_deck_status_reports_what_a_send_would_do(env, monkeypatch):
    from navig.gateway.deck.routes import notify as routes
    from navig.notify import prefs

    monkeypatch.setattr("navig.messaging.secrets.resolve_telegram_bot_token", lambda *a, **k: "123:abc")
    monkeypatch.setattr("navig.messaging.notify_operator.resolve_operator_chat_id", lambda: "4242")
    prefs.set_setting("target_sms", "+15550001")

    st = routes._channel_status()
    assert st["telegram"] is True, "Telegram delivers via the Bot API — token + chat = ready"
    assert st["sms"] is True, "SMS can be built in this process, so it is ready"

    monkeypatch.setattr("navig.messaging.secrets.resolve_telegram_bot_token", lambda *a, **k: "")
    assert routes._channel_status()["telegram"] is False


def test_no_sms_number_means_not_configured_even_with_the_adapter(env):
    from navig.gateway.deck.routes import notify as routes

    assert routes._channel_status()["sms"] is False
