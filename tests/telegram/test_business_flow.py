"""Tests for business-chat commands + the bot-echo loop guard."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import navig.telegram.biz_commands as bc
import navig.telegram.business as b


def _ch(token: str = "8490556839:ABC"):
    ch = MagicMock()
    ch.bot_token = token
    ch._api_call = AsyncMock(return_value={"message_id": 100})
    ch.send_message = AsyncMock()
    return ch


def test_parse_duration():
    assert bc.parse_duration("30 seconds") == 30
    assert bc.parse_duration("5 min") == 300
    assert bc.parse_duration("1h 30m") == 5400
    assert bc.parse_duration("nope") == 0


def test_bot_id_extraction():
    assert b._bot_id(_ch("8490556839:XYZ")) == "8490556839"
    assert b._bot_id(MagicMock(bot_token="")) == ""


async def test_ping_runnable_by_anyone_posts_as_owner():
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 1, "text": "ping", "business_connection_id": "bc1"}
    # A counterparty (not owner) can run ping.
    assert await bc.dispatch(ch, msg, is_owner=False, owner_id=777) is True
    sent = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "sendMessage"]
    assert sent and sent[0].args[1]["text"] in bc._PONGS
    assert sent[0].args[1]["business_connection_id"] == "bc1"  # posted AS the owner


async def test_time_deletes_owner_trigger_and_posts():
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 7, "text": "time", "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, msg, is_owner=True, owner_id=777) is True
    methods = [c.args[0] for c in ch._api_call.call_args_list if c.args]
    assert "deleteBusinessMessages" in methods  # owner's "time" command removed (business API)
    assert "sendMessage" in methods


async def test_timer_starts_then_cancels():
    ch = _ch()
    start = {"chat": {"id": 555}, "message_id": 8, "text": "timer 30 seconds",
             "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, start, is_owner=True, owner_id=777) is True
    assert 555 in bc._TIMERS  # a live countdown task is registered

    cancel = {"chat": {"id": 555}, "message_id": 9, "text": "timer cancel",
              "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, cancel, is_owner=True, owner_id=777) is True
    await asyncio.sleep(0)  # let the cancellation settle
    assert 555 not in bc._TIMERS


async def test_unknown_command_falls_through():
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 1, "text": "hello there", "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, msg, is_owner=True, owner_id=777) is False


async def test_weather_command_shows_typing_then_result(monkeypatch):
    from navig.telegram import biz_lookups

    monkeypatch.setattr(biz_lookups, "weather", AsyncMock(return_value="🌤 Paris 21°C"))
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 5, "text": "weather paris", "business_connection_id": "bc1"}

    assert await bc.dispatch(ch, msg, is_owner=False, owner_id=777) is True
    methods = [c.args[0] for c in ch._api_call.call_args_list if c.args]
    assert "sendChatAction" in methods  # 'typing…' while it fetches
    sent = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "sendMessage"]
    assert sent and "Paris" in sent[0].args[1]["text"]


async def test_crypto_formats_price(monkeypatch):
    from navig.telegram import biz_lookups

    monkeypatch.setattr(biz_lookups, "_get_json",
                        AsyncMock(return_value={"bitcoin": {"usd": 58000, "usd_24h_change": -2.5}}))
    out = await biz_lookups.crypto("btc")
    assert "Bitcoin" in out and "58,000" in out and "-2.5" in out


async def test_currency_parses_and_converts(monkeypatch):
    from navig.telegram import biz_lookups

    monkeypatch.setattr(biz_lookups, "_get_json", AsyncMock(return_value={"rates": {"EUR": 0.9}}))
    out = await biz_lookups.currency("100 usd eur")
    assert "100 USD" in out and "90" in out
    assert "Usage" in await biz_lookups.currency("100")  # too few codes


def test_whois_parse_extracts_fields():
    from navig.telegram import biz_lookups

    raw = ("Registrar: MarkMonitor Inc.\nCreation Date: 1997-09-15T00:00:00Z\n"
           "Registry Expiry Date: 2028-09-14T04:00:00Z\nDomain Status: clientDeleteProhibited https://…")
    f = biz_lookups._parse_whois(raw)
    assert f["registrar"].startswith("MarkMonitor")
    assert f["created"].startswith("1997-09-15")
    assert f["expires"].startswith("2028-09-14")


async def test_loop_guard_skips_the_bots_own_echoed_message(monkeypatch):
    # Telegram echoes the bot's business sends back as business_message updates;
    # the guard must drop them BEFORE any handler (else pro mode loops forever).
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: True)
    monkeypatch.setattr(b.autoreply, "handle_command", AsyncMock(side_effect=AssertionError("ran")))
    monkeypatch.setattr(b.biz_commands, "dispatch", AsyncMock(side_effect=AssertionError("ran")))
    monkeypatch.setattr(b.autoreply, "maybe_autoreply", AsyncMock(side_effect=AssertionError("ran")))

    ch = _ch("8490556839:ABC")
    msg = {
        "chat": {"id": 555}, "message_id": 2, "business_connection_id": "bc1",
        "from": {"id": 8490556839, "is_bot": True}, "text": "🟢 Pro mode ON",
    }
    await b.handle_business_message(ch, msg)  # no AssertionError == guard worked
