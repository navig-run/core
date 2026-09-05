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


def test_nl_convert_matches_natural_fiat_phrases():
    # the plain phrasing a user actually types (no `convert`/`currency` prefix)
    assert bc._nl_convert("10 eur to usd") == ("fiat", "10 EUR USD")
    assert bc._nl_convert("100 USD in EUR") == ("fiat", "100 USD EUR")
    assert bc._nl_convert("2.5 gbp -> jpy") == ("fiat", "2.5 GBP JPY")
    assert bc._nl_convert("$10 to eur") == ("fiat", "10 USD EUR")
    assert bc._nl_convert("10,5 eur to usd") == ("fiat", "10.5 EUR USD")


def test_nl_convert_matches_crypto_phrases():
    # crypto on either side → kind "crypto"; 3- and 4/5-letter coin symbols both work
    assert bc._nl_convert("0.5 btc to usd") == ("crypto", "0.5 BTC USD")
    assert bc._nl_convert("100 usd to btc") == ("crypto", "100 USD BTC")
    assert bc._nl_convert("2 eth in eur") == ("crypto", "2 ETH EUR")
    assert bc._nl_convert("10 doge to usd") == ("crypto", "10 DOGE USD")  # 4-letter coin


def test_nl_convert_ignores_ordinary_chat():
    # must NOT hijack normal conversation
    for text in (
        "see you in 10 min", "10 cats and dogs", "let's meet at 10",
        "how are you", "10 usd to usd", "10 cat to dog", "call me in 5",
        "10 hello to you",
    ):
        assert bc._nl_convert(text) is None, text


async def test_natural_currency_phrase_dispatches(monkeypatch):
    # "10 eur to usd" has no command word — dispatch must still convert it (fiat path).
    from navig.telegram import biz_lookups

    monkeypatch.setattr(biz_lookups, "_get_json", AsyncMock(return_value={"rates": {"USD": 1.1}}))
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 3, "text": "10 eur to usd",
           "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, msg, is_owner=True, owner_id=777) is True
    sent = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "sendMessage"]
    assert sent and "USD" in sent[0].args[1]["text"]
    # owner's trigger removed; conversion posted as the owner.
    assert any(c.args and c.args[0] == "deleteBusinessMessages" for c in ch._api_call.call_args_list)


async def test_natural_crypto_phrase_dispatches(monkeypatch):
    # "0.5 btc to usd" routes to the amount-aware smart converter.
    from navig.telegram import biz_lookups

    called = {}

    async def fake_smart(amount, frm, to):
        called["args"] = (amount, frm, to)
        return f"₿ {amount:g} {frm} = 30,000.00 {to}"

    monkeypatch.setattr(biz_lookups, "smart_convert", fake_smart)
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 4, "text": "0.5 btc to usd",
           "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, msg, is_owner=True, owner_id=777) is True
    assert called["args"] == (0.5, "BTC", "USD")
    sent = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "sendMessage"]
    assert sent and "30,000" in sent[0].args[1]["text"]


async def test_smart_convert_math(monkeypatch):
    from navig.telegram import biz_lookups

    async def fake_usd(code):
        return {"BTC": 60000.0, "USD": 1.0, "EUR": 1.08}.get(code.upper())

    monkeypatch.setattr(biz_lookups, "_usd_value", fake_usd)
    out = await biz_lookups.smart_convert(0.5, "btc", "usd")
    assert "30,000" in out and "BTC" in out and "USD" in out


async def test_natural_non_currency_falls_through():
    ch = _ch()
    msg = {"chat": {"id": 555}, "message_id": 3, "text": "see you in 10 min",
           "business_connection_id": "bc1"}
    assert await bc.dispatch(ch, msg, is_owner=True, owner_id=777) is False


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
