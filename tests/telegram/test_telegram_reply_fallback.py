"""Regression: a missing reply target must not drop the whole Telegram send.

Live bug (2026-07-21): TikTok / music enrichment cards on messages in a business
chat thread onto the shared message via ``reply_to_message_id``. When Telegram
can't find that reply target it returns ``400 Bad Request: message to be replied
not found`` — and the card was DROPPED (logged 3× per link in the gateway log,
seen at 14:03, 18:05, 18:06).

``_api_call`` now resends once WITHOUT the reply parameters so the content still
lands as a plain message — the same graceful degradation
``_send_into_business_chat`` already gets from ``allow_sending_without_reply``,
applied to every ``_api_call`` reply path.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    """Minimal aiohttp-session stand-in: scripted responses + recorded posts."""

    def __init__(self, responses: list[_FakeResp]):
        self._responses = list(responses)
        self.posts: list[dict] = []

    def post(self, url, json=None):
        self.posts.append({"url": url, "json": json})
        return self._responses.pop(0)


def _channel(session: _FakeSession):
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._session = session
    return ch


async def test_reply_not_found_resends_without_reply_to_message_id():
    session = _FakeSession([
        _FakeResp({"ok": False, "description": "Bad Request: message to be replied not found"}),
        _FakeResp({"ok": True, "result": {"message_id": 7}}),
    ])
    ch = _channel(session)

    result = await ch._api_call(
        "sendMessage",
        {"chat_id": 1, "text": "🎵 TikTok link", "reply_to_message_id": 999},
    )

    assert result == {"message_id": 7}, "the message must still be delivered"
    assert len(session.posts) == 2, "must resend exactly once"
    assert "reply_to_message_id" not in session.posts[1]["json"], "reply param must be stripped"
    assert session.posts[1]["json"]["text"] == "🎵 TikTok link", "the content must survive"


async def test_reply_not_found_strips_reply_parameters_too():
    session = _FakeSession([
        _FakeResp({"ok": False, "description": "message to be replied not found"}),
        _FakeResp({"ok": True, "result": {"message_id": 8}}),
    ])
    ch = _channel(session)

    result = await ch._api_call(
        "sendMessage",
        {"chat_id": 1, "text": "hi", "reply_parameters": {"message_id": 5}},
    )
    assert result == {"message_id": 8}
    assert "reply_parameters" not in session.posts[1]["json"]


async def test_unrelated_error_is_not_retried():
    session = _FakeSession([
        _FakeResp({"ok": False, "description": "Bad Request: chat not found"}),
    ])
    ch = _channel(session)

    result = await ch._api_call(
        "sendMessage", {"chat_id": 1, "text": "hi", "reply_to_message_id": 9}
    )
    assert result is None
    assert len(session.posts) == 1, "a non-reply error must not trigger the reply-strip retry"


async def test_reply_not_found_without_a_reply_param_does_not_loop():
    """The retry is guarded on a reply param being present — no infinite loop."""
    session = _FakeSession([
        _FakeResp({"ok": False, "description": "message to be replied not found"}),
    ])
    ch = _channel(session)

    result = await ch._api_call("sendMessage", {"chat_id": 1, "text": "hi"})
    assert result is None
    assert len(session.posts) == 1


async def test_send_message_delivers_when_reply_target_gone():
    """End-to-end: send_message with a dead reply target still delivers the text."""
    session = _FakeSession([
        _FakeResp({"ok": False, "description": "Bad Request: message to be replied not found"}),
        _FakeResp({"ok": True, "result": {"message_id": 11}}),
    ])
    ch = _channel(session)

    result = await ch.send_message(42, "card", parse_mode=None, reply_to_message_id=123)
    assert result == {"message_id": 11}
    assert "reply_to_message_id" not in session.posts[-1]["json"]


# ---------------------------------------------------------------------------
# Multipart media sends post directly (bypassing _api_call's backstop), so they
# carry allow_sending_without_reply inline via _add_reply_fields.
# ---------------------------------------------------------------------------


def test_add_reply_fields_includes_allow_sending_without_reply():
    from unittest.mock import MagicMock

    from navig.gateway.channels.telegram import TelegramChannel

    form = MagicMock()
    TelegramChannel._add_reply_fields(form, 123)

    added = {c.args[0]: c.args[1] for c in form.add_field.call_args_list}
    assert added.get("reply_to_message_id") == "123"
    # The flag that makes Telegram send the media even if the reply target is gone.
    assert added.get("allow_sending_without_reply") == "true"


def test_add_reply_fields_is_a_noop_without_a_reply_target():
    from unittest.mock import MagicMock

    from navig.gateway.channels.telegram import TelegramChannel

    form = MagicMock()
    TelegramChannel._add_reply_fields(form, None)
    form.add_field.assert_not_called()
