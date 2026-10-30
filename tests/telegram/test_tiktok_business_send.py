"""A TikTok card in a business chat must post AS the business account.

Observed on the live install (gateway.log, 2026-10-18/19): the owner sent a
TikTok link in a business conversation three times, ``offer_card`` ran — and
each time Telegram answered ``Forbidden: bot can't initiate conversation with a
user``. The cause: ``tiktok_actions`` sends through ``channel.send_*`` with no
``business_connection_id``, which is the only route Telegram accepts for a
business chat, so the card never reached the other person in the conversation.

Three seams, each independently capable of dropping the bcid again:

1. **the wire** — ``send_message`` / ``_post_media`` must put the field on the
   request (JSON body for text, multipart form field for media);
2. **the business layer** — ``offer_card`` must be handed a channel that
   carries it;
3. **the button tap** — ``handle_callback`` must receive one too: the tap
   arrives later as a plain ``callback_query``, outside the business-message
   handler, and its sends (video/analyse/transcript/errors) need the same
   identity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import navig.telegram.business as b
from navig.gateway.channels.telegram import TelegramChannel
from navig.telegram import tiktok_actions

_LINK = "https://vm.tiktok.com/ZGdxryFmF"


# ── seam 1: the wire ─────────────────────────────────────────────────────────


def _wire_channel() -> TelegramChannel:
    """A real channel whose API calls are captured instead of sent."""
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.base_url = "https://api.telegram.org/bot8490556839:TEST"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


async def test_send_message_puts_the_connection_id_on_the_wire():
    ch = _wire_channel()
    await ch.send_message(5, "hi", business_connection_id="bc1")
    method, data = ch._api_call.call_args.args
    assert method == "sendMessage"
    assert data["business_connection_id"] == "bc1"


async def test_send_message_without_a_connection_is_byte_identical():
    """The default path must not grow the field — a plain chat sent as a
    business message would be a different (wrong) identity."""
    ch = _wire_channel()
    await ch.send_message(5, "hi")
    assert "business_connection_id" not in ch._api_call.call_args.args[1]


class _Resp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return {"ok": True, "result": {"message_id": 2}}


class _Session:
    """Stands in for aiohttp.ClientSession, capturing the outgoing form."""

    def __init__(self):
        self.form = None
        self.url = ""

    def post(self, url, data=None, **kw):
        self.url = url
        self.form = data
        return _Resp()


def _form_names(form) -> list[str]:
    """Every field name in an aiohttp FormData, across its field shapes."""
    names: list[str] = []
    for field in form._fields:
        if isinstance(field, tuple) and len(field) == 3:
            # aiohttp 3.x: (MultiDict holding 'name', headers, value)
            hdr = field[0]
            names.append(hdr.get("name") if hasattr(hdr, "get") else None)
        elif isinstance(field, tuple) and field:
            names.append(field[0])
        else:
            names.append(getattr(field, "name", None))
    return [n for n in names if n]


def _form_value(form, name: str):
    """Read one field from an aiohttp FormData, across its field shapes."""
    for field in form._fields:
        if isinstance(field, tuple) and len(field) == 3:
            hdr = field[0]
            if hasattr(hdr, "get") and hdr.get("name") == name:
                return field[2]
        elif isinstance(field, tuple) and field and field[0] == name:
            return field[1]
        elif getattr(field, "name", None) == name:
            return field.value
    raise AssertionError(f"form has no field {name!r}: {_form_names(form)!r}")


async def test_media_uploads_carry_the_connection_id_as_a_form_field():
    """The video/cover go through multipart ``_post_media``, not ``_api_call`` —
    a bcid that only exists in JSON bodies would still lose the actual clip."""
    ch = _wire_channel()
    sess = _Session()
    ch._session = sess
    await ch.send_video(429106811, b"\x00\x01", caption="clip",
                        business_connection_id="bc1")
    assert sess.url.endswith("/sendVideo")
    # Positive controls first: the extractor must read fields at all, or the
    # assertion below could pass against an empty form and prove nothing.
    assert _form_value(sess.form, "chat_id") == "429106811"
    assert _form_value(sess.form, "business_connection_id") == "bc1"


async def test_media_uploads_without_a_connection_omit_the_field():
    ch = _wire_channel()
    sess = _Session()
    ch._session = sess
    await ch.send_video(429106811, b"\x00\x01", caption="clip")
    names = _form_names(sess.form)
    assert "video" in names  # extractor control — a vacuous pass is a failed test
    assert "business_connection_id" not in names


# ── the proxy itself ─────────────────────────────────────────────────────────

async def test_business_sender_stamps_every_send_and_delegates_the_rest():
    base = AsyncMock()
    base.allowed_users = {159901607}
    sender = b.BusinessSender(base, "bc7")

    await sender.send_message(5, "card", parse_mode="HTML")
    base.send_message.assert_awaited_once_with(
        5, "card", parse_mode="HTML", business_connection_id="bc7")

    await sender.send_video(5, b"\x00", caption="clip")
    base.send_video.assert_awaited_once_with(
        5, b"\x00", caption="clip", business_connection_id="bc7")

    await sender.send_rich_message(5, markdown="brief")
    base.send_rich_message.assert_awaited_once_with(
        5, markdown="brief", business_connection_id="bc7")

    # Non-send surface delegates untouched — `_is_owner` reads allowed_users
    # through the same attribute path on the proxied channel.
    assert sender.allowed_users == {159901607}


async def test_business_sender_never_overrides_an_explicit_connection_id():
    base = AsyncMock()
    sender = b.BusinessSender(base, "bc7")
    await sender.send_message(5, "y", business_connection_id="bcOther")
    base.send_message.assert_awaited_once_with(
        5, "y", business_connection_id="bcOther")


# ── seam 2: the business layer ───────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Isolated config + catalog DB; both telegram modules read the same fake config.

    Mirrors ``test_security``'s fixture (flat dotted keys, no real
    ``~/.navig/config.yaml`` read or written) — inlined rather than imported so
    the file stays self-contained.
    """
    class _FakeConfig:
        def get(self, key, default=None):
            return self.d.get(key, default)

        def set(self, key, value, scope=None):  # noqa: A003 - mirror Config.set
            self.d[key] = value

        def save(self, scope=None):
            pass

        d: dict = {}

    fake = _FakeConfig()
    fake.d = {}
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("navig.telegram.permissions._cfg", lambda: fake)
    monkeypatch.setattr("navig.telegram.business._cfg", lambda: fake)
    return fake


async def test_business_link_hands_offer_card_a_business_sender(cfg, monkeypatch):
    cfg.d["telegram.business.enabled"] = True
    b.remember_connection("conn1", owner_id=999)

    captured: dict = {}

    async def fake_offer(channel, chat_id, message_id, text, *, is_owner):
        captured["channel"] = channel
        return False

    monkeypatch.setattr(tiktok_actions, "offer_card", fake_offer)

    ch = AsyncMock()
    ch.bot_token = "8490556839:ABC"
    msg = {
        "chat": {"id": 429106811, "title": "Biz"},
        "from": {"id": 111, "username": "bob"},
        "message_id": 7,
        "text": _LINK,
        "business_connection_id": "conn1",
        "date": 0,
    }
    await b.handle_business_message(ch, msg)

    assert isinstance(captured.get("channel"), b.BusinessSender), (
        "offer_card was not handed a BusinessSender — every card, cover and "
        "error message it sends would be rejected by Telegram in the business chat"
    )
    assert captured["channel"].bcid == "conn1"


async def test_business_link_also_wraps_music_offer_links(cfg, monkeypatch):
    """The sibling enrichment shares the send path and must get the same
    BusinessSender — raw-channel music sends fail in a business chat exactly
    like the TikTok card did (and used to be wired that way)."""
    cfg.d["telegram.business.enabled"] = True
    b.remember_connection("conn1", owner_id=999)

    captured: dict = {}

    async def fake_offer_links(channel, chat_id, message_id, text):
        captured["channel"] = channel
        return False

    from navig.telegram import music_actions

    monkeypatch.setattr(music_actions, "offer_links", fake_offer_links)
    monkeypatch.setattr(tiktok_actions, "offer_card", AsyncMock(return_value=False))

    ch = AsyncMock()
    ch.bot_token = "8490556839:ABC"
    msg = {
        "chat": {"id": 429106811, "title": "Biz"},
        "from": {"id": 111, "username": "bob"},
        "message_id": 8,
        "text": "https://open.spotify.com/track/xyz",
        "business_connection_id": "conn1",
        "date": 0,
    }
    await b.handle_business_message(ch, msg)

    assert isinstance(captured.get("channel"), b.BusinessSender), (
        "music offer_links received the raw channel — its business-chat sends "
        "would hit the same Forbidden rejection the TikTok card hit"
    )
    assert captured["channel"].bcid == "conn1"


# ── seam 3: the button tap ───────────────────────────────────────────────────


async def _drain_taps():
    """The tap runs off the update loop; wait for it before asserting."""
    import asyncio

    from navig.core import background

    tasks = [t for t in background._bg_tasks if t.get_name() == "tiktok-callback"]
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_button_tap_carries_the_connection_id(monkeypatch):
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    captured: dict = {}

    async def fake_handle(channel, cb_data, chat_id, message_id, user_id, *,
                          source_text=""):
        captured["channel"] = channel
        captured["source_text"] = source_text

    monkeypatch.setattr(tiktok_actions, "handle_callback", fake_handle)

    handler = CallbackHandler(AsyncMock())
    await handler.handle({
        "id": "cb1",
        "data": "tk:dl:429106811:5",
        "message": {
            "chat": {"id": 429106811},
            "message_id": 5,
            "business_connection_id": "bc9",
            "reply_to_message": {"text": _LINK},
        },
        "from": {"id": 159901607},
    })
    await _drain_taps()

    assert isinstance(captured.get("channel"), b.BusinessSender), (
        "a tk: tap on a business-chat card reached handle_callback without the "
        "connection id — the download would be rejected exactly like the card "
        "was before this wiring existed"
    )
    assert captured["channel"].bcid == "bc9"
    assert captured["source_text"] == _LINK


async def test_button_tap_in_a_normal_chat_stays_the_raw_channel(monkeypatch):
    """No bcid on the message → no wrapper: a DM card must not be sent as a
    business message."""
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    captured: dict = {}

    async def fake_handle(channel, cb_data, chat_id, message_id, user_id, *,
                          source_text=""):
        captured["channel"] = channel

    monkeypatch.setattr(tiktok_actions, "handle_callback", fake_handle)

    raw = AsyncMock()
    handler = CallbackHandler(raw)
    await handler.handle({
        "id": "cb2",
        "data": "tk:dl:100:5",
        "message": {
            "chat": {"id": 100},
            "message_id": 5,
            "reply_to_message": {"text": _LINK},
        },
        "from": {"id": 42},
    })
    await _drain_taps()

    assert captured.get("channel") is raw


async def test_a_second_tap_is_refused_while_the_first_worker_runs(monkeypatch):
    """Long polling awaits each update in turn. With the worker awaited inline, the
    second tap was only processed after the first had released its in-flight slot
    — so it started the duplicate download the slot exists to stop."""
    import asyncio

    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    started: list[int] = []
    gate = asyncio.Event()

    async def slow(channel, chat_id, url):
        started.append(chat_id)
        await gate.wait()

    monkeypatch.setattr(tiktok_actions.permissions, "can_use", lambda *a, **kw: True)
    monkeypatch.setattr(tiktok_actions, "_worker_for", lambda action: slow)
    monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _LINK)
    tiktok_actions._IN_FLIGHT.clear()
    raw = AsyncMock()
    handler = CallbackHandler(raw)
    update = {"id": "cb3", "data": "tk:dl:100:5",
              "message": {"chat": {"id": 100}, "message_id": 5,
                          "reply_to_message": {"text": _LINK}},
              "from": {"id": 42}}
    try:
        # Each handle() returns at once, as the update loop needs it to.
        await asyncio.wait_for(handler.handle(update), timeout=2)
        await asyncio.sleep(0)
        await asyncio.wait_for(handler.handle({**update, "id": "cb4"}), timeout=2)
        await asyncio.sleep(0)
        gate.set()
        await _drain_taps()
    finally:
        tiktok_actions._IN_FLIGHT.clear()
    assert started == [100], "the second tap started the worker again"
    busy = [c for c in raw.send_message.call_args_list if tiktok_actions._t("tiktok.busy") in c.args]
    assert busy, raw.send_message.call_args_list


async def test_an_album_in_a_business_chat_carries_the_connection_id():
    """`send_media_group` fell through `__getattr__` to the raw channel, so a
    business-chat slideshow album went out without the bcid, was rejected and
    fell back to single photos (PR #2009 review)."""
    base = AsyncMock()
    sender = b.BusinessSender(base, "bc7")
    await sender.send_media_group(5, [b"a", b"b"], caption="slides")
    base.send_media_group.assert_awaited_once_with(
        5, [b"a", b"b"], caption="slides", business_connection_id="bc7")

    ch = _wire_channel()
    sess = _Session()
    ch._session = sess
    await ch.send_media_group(429106811, [b"\x00", b"\x01"], business_connection_id="bc1")
    assert sess.url.endswith("/sendMediaGroup")
    assert _form_value(sess.form, "chat_id") == "429106811"
    assert _form_value(sess.form, "business_connection_id") == "bc1"
