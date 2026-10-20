"""Confirm-first sending: "send an sms to Alex …" drafts, and only the asker's tap sends.

The natural-language intent reads free text with an LLM. That reading must never be the
last step before a real SMS leaves — so these tests pin that the intent ONLY produces a
card, and that a send happens exactly once, from the right person, inside the window.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.telegram import send_drafts

OWNER = 456


@pytest.fixture(autouse=True)
def _clean_drafts():
    send_drafts.reset_for_tests()
    yield
    send_drafts.reset_for_tests()


def _channel() -> TelegramChannel:
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[OWNER])
    channel.sent = []
    channel.edits = []

    async def send_message(chat_id, text, parse_mode="HTML", **kwargs):
        channel.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"ok": True}

    async def _api_call(method, data=None, *a, **k):  # never the network
        if method == "editMessageText":
            channel.edits.append(data)
        return {"ok": True}

    channel.send_message = send_message
    channel._api_call = _api_call
    channel._messaging_dispatch = AsyncMock(return_value=SimpleNamespace(ok=True, error=None))
    return channel


def _llm_says(monkeypatch, **payload):
    body = {"is_send": True, "contact": "alex", "channel": "sms", "language": None,
            "message": "Running late", **payload}
    monkeypatch.setattr("navig.llm.generate.llm_generate", lambda **_kw: json.dumps(body))


async def _draft(channel, monkeypatch, **payload) -> send_drafts.Draft:
    from navig.gateway.channels.telegram_messaging_mixin import TelegramMessagingMixin

    _llm_says(monkeypatch, **payload)
    handled = await TelegramMessagingMixin._try_nl_messaging_intent(
        channel, OWNER, "send an sms to alex: running late", user_id=OWNER
    )
    assert handled is True
    ((draft_id, draft),) = send_drafts._drafts.items()
    return draft


async def test_the_intent_only_drafts_it_never_sends(monkeypatch):
    channel = _channel()
    draft = await _draft(channel, monkeypatch)
    channel._messaging_dispatch.assert_not_called()
    card = channel.sent[-1]
    assert "SMS to alex" in card["text"]
    assert "Running late" in card["text"]
    buttons = [b["callback_data"] for b in card["keyboard"][0]]
    assert buttons == [f"msg:sd:{draft.id}", f"msg:sx:{draft.id}"]


async def test_send_tap_sends_exactly_once(monkeypatch):
    channel = _channel()
    draft = await _draft(channel, monkeypatch)
    toast = await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, OWNER)
    assert toast == "Sent"
    channel._messaging_dispatch.assert_awaited_once_with("alex", "Running late", network="sms")
    assert "sent to alex" in channel.edits[-1]["text"]

    again = await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, OWNER)
    assert "already handled" in again
    assert channel._messaging_dispatch.await_count == 1  # a double tap never sends twice


async def test_another_person_cannot_send_and_does_not_burn_the_draft(monkeypatch):
    channel = _channel()
    draft = await _draft(channel, monkeypatch)
    toast = await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, 999)
    assert "Only the person who asked" in toast
    channel._messaging_dispatch.assert_not_called()
    # The owner can still act on it.
    assert await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, OWNER) == "Sent"


async def test_cancel_sends_nothing_and_consumes_the_draft(monkeypatch):
    channel = _channel()
    draft = await _draft(channel, monkeypatch)
    assert await send_drafts.handle_callback(channel, f"msg:sx:{draft.id}", OWNER, 10, OWNER) == "Cancelled"
    assert "Not sent" in channel.edits[-1]["text"]
    later = await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, OWNER)
    assert "already handled" in later
    channel._messaging_dispatch.assert_not_called()


async def test_an_expired_draft_is_refused(monkeypatch):
    channel = _channel()
    draft = await _draft(channel, monkeypatch)
    took, reason = send_drafts.claim(
        draft.id, chat_id=OWNER, user_id=OWNER, now=draft.created + send_drafts.DRAFT_TTL_S + 1
    )
    assert took is None and "expired" in reason
    channel._messaging_dispatch.assert_not_called()


async def test_a_failed_send_says_so(monkeypatch):
    channel = _channel()
    channel._messaging_dispatch = AsyncMock(return_value=SimpleNamespace(ok=False, error="no route"))
    draft = await _draft(channel, monkeypatch)
    assert await send_drafts.handle_callback(channel, f"msg:sd:{draft.id}", OWNER, 10, OWNER) == "Not sent"
    assert "no route" in channel.edits[-1]["text"]


async def test_the_words_are_html_escaped_on_the_card(monkeypatch):
    channel = _channel()
    await _draft(channel, monkeypatch, message="<b>hi</b> & bye")
    assert "&lt;b&gt;hi&lt;/b&gt; &amp; bye" in channel.sent[-1]["text"]


async def test_a_group_chat_never_drafts(monkeypatch):
    channel = _channel()
    _llm_says(monkeypatch)
    nl = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_commands.TelegramCommandsMixin._handle_natural_language_request",
        nl,
    )
    await channel._handle_natural_language_request(
        -100, OWNER, "send an sms to alex: running late", is_group=True
    )
    assert send_drafts._drafts == {}
    channel._messaging_dispatch.assert_not_called()


async def test_the_private_chat_path_drafts_when_messaging_is_on(monkeypatch):
    channel = _channel()
    _llm_says(monkeypatch)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_extensions.is_enabled", lambda ext: ext == "messaging"
    )
    handled = await channel._handle_natural_language_request(
        OWNER, OWNER, "send an sms to alex: running late", is_group=False
    )
    assert handled is True
    assert len(send_drafts._drafts) == 1
    channel._messaging_dispatch.assert_not_called()


async def test_messaging_switched_off_never_drafts(monkeypatch):
    channel = _channel()
    _llm_says(monkeypatch)
    monkeypatch.setattr("navig.gateway.channels.telegram_extensions.is_enabled", lambda ext: False)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_commands.TelegramCommandsMixin._handle_natural_language_request",
        AsyncMock(return_value=False),
    )
    await channel._handle_natural_language_request(
        OWNER, OWNER, "send an sms to alex: running late", is_group=False
    )
    assert send_drafts._drafts == {}


async def test_a_russian_operator_gets_russian_buttons(monkeypatch):
    monkeypatch.setattr("navig.core.i18n.current_language", lambda: "ru")
    channel = _channel()
    await _draft(channel, monkeypatch)
    card = channel.sent[-1]
    labels = [b["text"] for b in card["keyboard"][0]]
    assert labels == ["✅ Отправить", "✖ Отмена"]
    assert "Ничего не отправится" in card["text"]

