"""Tests for business-chat commands + the bot-echo loop guard."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

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


# ── Deletion alert: "(content was not cached)" for content NAVIG had seen ────────
#
# Two root causes, both measured on the live catalog (2026-09-22): the loop guard
# dropped EVERY ``is_bot`` sender (so bot conversations were cataloged one-sided —
# "⚕ Digital Ghost", "SCHEMA", "PING" held only the owner's rows), and a media
# message was stored as text="" with nothing else (20% of business rows), so the
# alert — which read only ``text`` — called both "not cached".


class _FakeCfg:
    def __init__(self):
        self.d: dict = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value, scope=None):  # noqa: A003
        self.d[key] = value

    def save(self, scope=None):
        pass


@pytest.fixture
def biz(tmp_path, monkeypatch):
    """Isolated catalog DB + in-memory config; business inbox on; no real
    ~/.navig touched (resolve_owner/remember_connection write the global config)."""
    fake = _FakeCfg()
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(b, "_cfg", lambda: fake)
    monkeypatch.setattr(b.permissions, "_cfg", lambda: fake, raising=False)
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: True)
    # The policy module reads (and WRITES — set_mode) config through its own _cfg,
    # and navig.core.Config is a process-wide singleton, so patching business's
    # alone would leave this one reading whichever dir the singleton cached first.
    monkeypatch.setattr(b.deletions, "_cfg", lambda: fake)
    # These tests exercise the per-event report, so they ask for it explicitly.
    # The shipped default is `digest`; its own tests below set it themselves.
    fake.d[b.deletions.CFG_MODE] = "instant"
    # The enrichment hooks are real coroutines that want a live channel; stub them.
    import navig.telegram.music_actions as ma
    import navig.telegram.tiktok_actions as ta
    monkeypatch.setattr(ta, "offer_card", AsyncMock())
    monkeypatch.setattr(ma, "offer_links", AsyncMock())
    b.remember_connection("bc1", owner_id=777, can_reply=True)
    return fake


def _no_actions(monkeypatch):
    """Every action handler raises if reached — the message must stay DATA."""
    for mod, name in ((b.autoreply, "handle_command"), (b.autoreply, "maybe_autoreply"),
                      (b.reply_actions, "run_business_reply"), (b.biz_commands, "dispatch")):
        monkeypatch.setattr(mod, name, AsyncMock(side_effect=AssertionError(f"{name} ran")))


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


async def test_another_bots_message_is_cataloged_but_never_acted_on(biz, monkeypatch):
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    msg = {
        "chat": {"id": 5973101279, "first_name": "Digital Ghost", "username": "digitalghost_bot"},
        "message_id": 41, "business_connection_id": "bc1", "date": 1,
        "from": {"id": 5973101279, "is_bot": True, "first_name": "Digital Ghost"},
        "text": "ping",   # even a command-looking text: a bot never reaches dispatch
    }
    await b.handle_business_message(ch, msg)  # no AssertionError == no action ran
    row = _store().get_message_by_ref(5973101279, 41)
    assert row is not None and row["text"] == "ping"


async def test_own_echo_is_detected_by_sender_business_bot_too(biz, monkeypatch):
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    msg = {
        "chat": {"id": 555}, "message_id": 3, "business_connection_id": "bc1", "date": 1,
        "from": {"id": 777, "first_name": "owner"},                 # sent AS the owner …
        "sender_business_bot": {"id": 8490556839, "is_bot": True},  # … by our bot
        "text": "auto-reply text",
    }
    assert b._is_own_echo(ch, msg) is True
    await b.handle_business_message(ch, msg)
    assert _store().get_message_by_ref(555, 3) is None   # skipped entirely, as before


async def test_media_business_message_keeps_the_file_id(biz, monkeypatch):
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    msg = {
        "chat": {"id": 100200300, "first_name": "Sam", "username": "sam_example"},
        "message_id": 1123542, "business_connection_id": "bc1", "date": 1790037882,
        "from": {"id": 777, "first_name": "operator"},
        "photo": [{"file_id": "small", "file_unique_id": "u-small", "file_size": 10},
                  {"file_id": "BIG-FILE-ID", "file_unique_id": "u-big", "file_size": 999}],
        # no caption — exactly the chars=0 message the 02:45 alert called "not cached"
    }
    await b.handle_business_message(ch, msg)
    store = _store()
    row = store.get_message_by_ref(100200300, 1123542)
    assert row is not None and row["media_ref"]
    media = store.get_media(row["media_ref"])
    assert media["kind"] == "photo" and media["file_id"] == "BIG-FILE-ID"


def _seed(store, chat_id, mid, *, sender_id, sender_name, text="", media=None,
          date=None, raw=None, edited_at=None):
    media_id = None
    if media:
        media_id = store.upsert_media(chat_id, message_id=mid, file_id=media["file_id"],
                                      file_unique_id=media["file_unique_id"], kind=media["kind"])
    # Default to "today at 09:05" so the rendered stamp is the time-only form a
    # same-day deletion produces — a fixed epoch would render "1 Jan 1970" and make
    # every assertion below depend on the year the suite runs in.
    if date is None:
        d = datetime.now().replace(hour=9, minute=5, second=0, microsecond=0)
        date = str(int(d.timestamp()))
    store.upsert_message(chat_id, mid, sender_id=sender_id, sender_name=sender_name,
                         date=date, text=text or None, kind="business", media_ref=media_id,
                         edited_at=edited_at, raw=raw or {"business": True})


def _capture_dispatch(monkeypatch, *, delivered=True):
    """Capture the delivered deletion report.

    Delivery no longer goes through NotificationRouter: a router dispatch cannot
    carry the digest's inline button, and a log chat is not the router's target by
    definition. Every report now goes through the one function intercepted here, so
    this stays the single seam. ``title`` is the report's first line, which is where
    the header moved to."""
    calls: list[dict] = []

    async def fake_send(channel, text, *, target=None, owner_id=None):
        calls.append({"body": text, "title": text.split("\n", 1)[0], "target": target})
        return delivered

    monkeypatch.setattr(b.deletions, "send_detail", fake_send)
    import navig.messaging.notify_operator as no
    monkeypatch.setattr(no, "resolve_operator_chat_id", lambda: "777")
    return calls


async def test_deleted_captionless_photo_is_named_and_resent_not_called_uncached(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 100200300, 1123542, sender_id=777, sender_name="operator",
          media={"file_id": "BIG-FILE-ID", "file_unique_id": "u-big", "kind": "photo"})
    ch = _ch("8490556839:ABC")
    payload = {"business_connection_id": "bc1", "message_ids": [1123542],
               "chat": {"id": 100200300, "type": "private", "first_name": "Sam",
                        "username": "sam_example"}}
    await b.handle_deleted_business_messages(ch, payload)

    assert len(calls) == 1
    body = calls[0]["body"]
    assert "not cached" not in body
    assert "In Sam:" in body                   # the person's name, not the hex handle
    assert "• you · 09:05 · 📷 photo" in body   # WHO · WHEN · WHAT, on every line
    # The photo itself comes back to the owner's DM by file_id.
    sends = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "sendPhoto"]
    assert sends and sends[0].args[1]["photo"] == "BIG-FILE-ID"
    assert sends[0].args[1]["chat_id"] == "777"
    assert sends[0].args[1]["caption"].startswith("🗑 Deleted in Sam")
    assert store.get_message_by_ref(100200300, 1123542)["deleted"] is True


async def test_deleted_unknown_message_says_never_seen(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    ch = _ch("8490556839:ABC")
    payload = {"business_connection_id": "bc1", "message_ids": [9],
               "chat": {"id": 5818898024, "type": "private", "first_name": "SCHEMA",
                        "username": "schematrix_bot"}}
    await b.handle_deleted_business_messages(ch, payload)
    assert len(calls) == 1
    assert "not cached" not in calls[0]["body"]
    assert "not seen" in calls[0]["body"]
    assert "never cataloged this chat" in calls[0]["body"]   # no history at all for it
    assert not ch._api_call.called   # nothing to re-send


async def test_one_alert_per_deletion_event_lists_every_message(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator", text="see you at 9")
    _seed(store, 555, 2, sender_id=555, sender_name="Sam", text="ok 👍")
    _seed(store, 555, 3, sender_id=555, sender_name="Sam",
          media={"file_id": "V1", "file_unique_id": "uv1", "kind": "voice"})
    ch = _ch("8490556839:ABC")
    payload = {"business_connection_id": "bc1", "message_ids": [1, 2, 3, 4],
               "chat": {"id": 555, "type": "private", "first_name": "Sam"}}
    await b.handle_deleted_business_messages(ch, payload)

    assert len(calls) == 1                         # was: one DM per id
    assert calls[0]["title"] == "🗑 4 messages deleted"
    body = calls[0]["body"]
    assert "• you · 09:05 — see you at 9" in body
    assert "• Sam · 09:05 — ok 👍" in body
    assert "• Sam · 09:05 · 🎤 voice" in body
    assert body.count("· not seen") == 1           # id 4 was never cataloged
    # …and for a chat NAVIG DOES hold, "not seen" explains itself rather than
    # reading as a failure: the message is simply older than the watch.
    assert "has watched this chat since" in body
    # Every id is accounted for in the one report — 3 with content, 1 as not-seen.
    assert body.count("\n• ") == 4
    methods = [c.args[0] for c in ch._api_call.call_args_list if c.args]
    assert methods == ["sendVoice"]
    assert all(store.get_message_by_ref(555, i)["deleted"] for i in (1, 2, 3))


async def test_media_is_not_resent_when_the_alert_was_not_delivered(biz, monkeypatch):
    """The router is where the owner's prefs live (muted type, quiet hours); the
    media copies must not bypass them."""
    _capture_dispatch(monkeypatch, delivered=False)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator",
          media={"file_id": "P1", "file_unique_id": "up1", "kind": "photo"})
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1], "chat": {"id": 555}})
    assert not ch._api_call.called


async def test_a_whole_chat_clear_is_capped_to_a_readable_list(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    for i in range(1, 61):
        _seed(store, 555, i, sender_id=777, sender_name="operator", text=f"line {i}")
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": list(range(1, 61)),
             "chat": {"id": 555, "first_name": "Sam"}})
    assert calls[0]["title"] == "🗑 60 messages deleted"
    body = calls[0]["body"]
    assert body.count("\n• ") == b._LINES_MAX + 1
    assert body.endswith("• … and 20 more")
    assert all(store.get_message_by_ref(555, i)["deleted"] for i in (1, 40, 60))


def test_chat_label_prefers_the_name_over_the_handle():
    assert b._chat_label({"id": 1, "first_name": "Sam", "username": "sam_example"}) == "Sam"
    assert b._chat_label({"id": 1, "title": "Ops room"}) == "Ops room"
    assert b._chat_label({"id": 1, "username": "schematrix_bot"}, "SCHEMA") == "SCHEMA"
    assert b._chat_label({"id": 1, "username": "schematrix_bot"}) == "@schematrix_bot"
    assert b._chat_label({"id": 42}) == "42"


# ── "why doesn't it show?" — the three cases an alert must tell apart ──────────
#
# Live report, 2026-09-22: three alerts read "(no text — media sent before NAVIG
# kept copies…)". Every one was TRUE — the rows were sent 09-21, before media was
# kept — and every one looked like a bug, because the line never said WHEN the
# message was sent. A deletion of yesterday's photo and today's photo going
# missing rendered identically.


async def test_every_line_says_when_the_message_was_sent(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    old = datetime.now().replace(month=1, day=21, hour=17, minute=18, second=0, microsecond=0)
    _seed(store, 555, 1, sender_id=777, sender_name="operator", text="from january",
          date=str(int(old.timestamp())))
    _seed(store, 555, 2, sender_id=777, sender_name="operator", text="from today")
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1, 2],
             "chat": {"id": 555, "first_name": "Sam"}})

    body = calls[0]["body"]
    assert "21 Jan 17:18 — from january" in body   # older → day + time, never bare
    assert "• you · 09:05 — from today" in body    # today → time only


async def test_a_counterparty_is_named_not_handled(biz, monkeypatch):
    """The DM called the chat "Sam 🧢" and its owner "sam_example" in
    the same two lines — the handle came from `sender_name`, which was stored
    username-first. A private chat's counterparty IS the chat, so the chat's own
    label wins, which also rescues every row already written the old way."""
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 100200300, 5, sender_id=100200300,
          sender_name="sam_example", text="example.blog")
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [5],
             "chat": {"id": 100200300, "type": "private", "first_name": "Sam 🧢",
                      "username": "sam_example"}})

    body = calls[0]["body"]
    assert "sam_example" not in body
    assert "• Sam 🧢 · 09:05 — example.blog" in body


async def test_a_group_keeps_per_sender_names(biz, monkeypatch):
    """The rescue above must not flatten a GROUP, where the chat title and the
    sender are genuinely different things."""
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 900, 1, sender_id=111, sender_name="Dana", text="ship it")
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1],
             "chat": {"id": 900, "type": "supergroup", "title": "Ops room"}})
    assert "• Dana · 09:05 — ship it" in calls[0]["body"]


async def test_not_seen_distinguishes_an_older_message_from_an_unwatched_chat(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator", text="anything")
    ch = _ch("8490556839:ABC")

    # A chat NAVIG holds → the deletion is simply older than the watch.
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [99], "chat": {"id": 555}})
    assert "has watched this chat since" in calls[0]["body"]

    # A chat it has never cataloged → a different fact, and a different sentence.
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [99], "chat": {"id": 42}})
    assert "never cataloged this chat" in calls[1]["body"]


async def test_a_file_less_message_is_named_not_called_empty(biz, monkeypatch):
    """A poll/location/story carries no text and no file. It used to render as
    "(no text)", which reads as a capture failure for a message NAVIG saw fine."""
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator",
          raw={"business": True, "content": "poll"})
    _seed(store, 555, 2, sender_id=777, sender_name="operator",
          raw={"business": True, "content": "location"})
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1, 2], "chat": {"id": 555}})

    body = calls[0]["body"]
    assert "📊 poll" in body and "📍 location" in body
    assert "no copy kept" not in body


async def test_an_edited_message_is_marked(biz, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator", text="final text",
          edited_at="2026-09-22T09:06:00+02:00")
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1], "chat": {"id": 555}})
    assert "· edited —" in calls[0]["body"]


async def test_a_failed_media_resend_is_reported_not_swallowed(biz, monkeypatch):
    """The summary PROMISED a photo. When the file id no longer resolves, saying
    so is the point — an alert that quietly delivers less than it announced
    teaches the operator to distrust the ones that work."""
    _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator",
          media={"file_id": "DEAD", "file_unique_id": "u1", "kind": "photo"})
    ch = _ch("8490556839:ABC")
    ch._api_call = AsyncMock(side_effect=lambda m, d=None: None if m == "sendPhoto" else {"message_id": 1})
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1], "chat": {"id": 555}})

    notes = [c.args[1]["text"] for c in ch._api_call.call_args_list
             if c.args and c.args[0] == "sendMessage"]
    assert notes and "Couldn't re-send 1 of 1" in notes[0]
    assert "photo" in notes[0]


async def test_a_successful_resend_sends_no_failure_note(biz, monkeypatch):
    _capture_dispatch(monkeypatch)
    store = _store()
    _seed(store, 555, 1, sender_id=777, sender_name="operator",
          media={"file_id": "OK", "file_unique_id": "u1", "kind": "photo"})
    ch = _ch("8490556839:ABC")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1], "chat": {"id": 555}})
    methods = [c.args[0] for c in ch._api_call.call_args_list if c.args]
    assert methods == ["sendPhoto"]     # no apology when nothing failed


# ── capture-side: what makes the alert answerable at all ──────────────────────


async def test_a_file_less_message_keeps_its_content_kind(biz, monkeypatch):
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    await b.handle_business_message(ch, {
        "chat": {"id": 555}, "message_id": 8, "business_connection_id": "bc1", "date": 1,
        "from": {"id": 777, "first_name": "operator"},
        "location": {"latitude": 48.85, "longitude": 2.35},
    })
    assert _store().get_message_by_ref(555, 8)["content"] == "location"


async def test_a_media_message_is_not_relabelled_by_a_trailing_field(biz, monkeypatch):
    """`content` is consulted only when there is no file, so a photo forwarded
    from a story stays a photo."""
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    await b.handle_business_message(ch, {
        "chat": {"id": 555}, "message_id": 9, "business_connection_id": "bc1", "date": 1,
        "from": {"id": 777, "first_name": "operator"},
        "photo": [{"file_id": "P", "file_unique_id": "up", "file_size": 9}],
        "story": {"id": 3},
    })
    row = _store().get_message_by_ref(555, 9)
    assert row["media_ref"] and "content" not in row


async def test_a_sender_is_stored_by_name_going_forward(biz, monkeypatch):
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    await b.handle_business_message(ch, {
        "chat": {"id": 555}, "message_id": 10, "business_connection_id": "bc1", "date": 1,
        "from": {"id": 555, "first_name": "Sam", "last_name": "🧢",
                 "username": "sam_example"},
        "text": "hi",
    })
    assert _store().get_message_by_ref(555, 10)["sender_name"] == "Sam 🧢"


async def test_a_later_update_cannot_erase_stored_text(biz, monkeypatch):
    """Telegram re-delivers and edits; an empty string OVERWRITES through the
    upsert's COALESCE, and the deletion alert is the last reader of that text."""
    _no_actions(monkeypatch)
    ch = _ch("8490556839:ABC")
    edited_at_ts = int(datetime.now().timestamp())
    base = {"chat": {"id": 555}, "message_id": 11, "business_connection_id": "bc1",
            "date": edited_at_ts - 60, "from": {"id": 777, "first_name": "operator"}}
    await b.handle_business_message(ch, {**base, "text": "the original words"})
    await b.handle_business_message(ch, {**base, "edit_date": edited_at_ts}, edited=True)

    row = _store().get_message_by_ref(555, 11)
    assert row["text"] == "the original words"
    # A TIME, not the literal "yes" it used to store — `edited_at` is a timestamp
    # everywhere else in this table, and "yes" made the column unsortable.
    assert row["edited_at"].startswith(datetime.now().strftime("%Y-%m-%d"))
    assert datetime.fromisoformat(row["edited_at"])   # parses as a real instant


def test_when_parses_both_stored_date_shapes():
    """The business path stores a unix timestamp string, the regular ingest ISO.
    The alert reads rows written by either."""
    now = datetime.now().replace(hour=14, minute=7, second=0, microsecond=0)
    assert b.format_when(str(int(now.timestamp()))) == "14:07"
    assert b.format_when(now.strftime("%Y-%m-%dT%H:%M:%SZ")) is not None
    assert b.format_when(None) is None
    assert b.format_when("") is None
    assert b.format_when("not a date") is None


def test_media_and_content_labels_are_shared_by_every_surface():
    """One rendering, so the DM and `navig telegram business deleted` cannot
    describe the same message differently."""
    assert b.media_label("video_note") == "📹 video note"
    assert b.media_label("something_new") == "📎 something new"
    assert b.content_label("poll") == "📊 poll"
    assert b.content_label("text") is None
    assert b.content_label(None) is None
