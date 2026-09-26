"""A list-shaped AI reply becomes a native checklist -- when the operator asks for it.

This feature was unreachable from the day it was written. `HAS_CHECKLIST` was computed and
never read, `TelegramChecklistMixin` had no method on `TelegramChannel`, and the Deck rendered a
toggle for it defaulting to True. These tests drive `_send_response` on a REAL channel and
assert what reaches Telegram.

Three things are pinned that a plain "call the mixin" would have got wrong:

1. Detection runs on the RAW MARKDOWN. `_send_response` converts to HTML first; the detector
   reads bullets, so it must see `response_md`.
2. The reply's keyboard RIDES ALONG. An AI reply almost always carries one (explore questions,
   dig-deeper); the mixin's own helper dropped it, and losing those buttons on every list
   reply would be a regression for anyone who opts in.
3. It is OPT-IN and fails CLOSED. "On by default" was never a behaviour anyone experienced, a
   list is not always a task list, and an unreadable config must not switch it on.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

_LIST_REPLY = "Here is your plan:\n\n- Back up the database\n- Rotate the SSH key\n- Restart nginx\n"
_PROSE_REPLY = "The server has been up for 14 days and load is nominal. Nothing needs attention."


class _Router:
    @staticmethod
    async def route_message(**kw):
        return "ok"

    @staticmethod
    def flush_conv_agents():
        pass


class _Gateway:
    def __init__(self):
        self.router = _Router()


def _channel():
    from navig.gateway.channels.telegram import create_telegram_channel

    ch = create_telegram_channel(_Gateway(), {"bot_token": "123456:FAKE", "allowed_users": [7]})
    ch.api: list[tuple[str, dict]] = []

    async def _api_call(method, params=None, **kw):
        ch.api.append((method, dict(params or {})))
        return {"ok": True, "result": {"message_id": 1}}

    ch._api_call = _api_call
    return ch


def _send(ch, text, *, enabled, keyboard=None):
    cfg = {"checklist_enabled": enabled}
    with patch(
        "navig.gateway.channels.telegram_checklist.TelegramChecklistMixin._get_checklist_config",
        return_value=cfg,
    ):
        asyncio.run(ch._send_response(1, text, original_text="what should I do", user_id=7,
                                      is_group=False, prebuilt_keyboard=keyboard))
    return [m for m, _ in ch.api]


def test_a_list_reply_goes_up_as_a_checklist_when_enabled():
    ch = _channel()
    methods = _send(ch, _LIST_REPLY, enabled=True)
    assert "sendChecklist" in methods, f"expected a native checklist, got {methods}"
    assert "sendMessage" not in methods, "the text must not ALSO be sent as a plain message"
    payload = next(p for m, p in ch.api if m == "sendChecklist")
    assert [t["text"] for t in payload["tasks"]] == [
        "Back up the database", "Rotate the SSH key", "Restart nginx",
    ], "the bullets must be stripped and each line become one task"


def test_the_replys_keyboard_rides_along_on_the_checklist():
    """The explore buttons must survive the upgrade -- the mixin's own helper dropped them."""
    ch = _channel()
    kb = [[{"text": "❓ Why nginx?", "callback_data": "explore:1"}]]
    _send(ch, _LIST_REPLY, enabled=True, keyboard=kb)
    payload = next(p for m, p in ch.api if m == "sendChecklist")
    assert payload.get("reply_markup") == {"inline_keyboard": kb}, (
        f"the keyboard was dropped from the checklist: {payload.get('reply_markup')}"
    )


def test_prose_is_never_turned_into_a_checklist():
    ch = _channel()
    methods = _send(ch, _PROSE_REPLY, enabled=True)
    assert "sendChecklist" not in methods
    assert "sendMessage" in methods


def test_it_is_off_unless_the_operator_turned_it_on():
    ch = _channel()
    methods = _send(ch, _LIST_REPLY, enabled=False)
    assert "sendChecklist" not in methods, "opt-in: a list reply stays a plain message by default"
    assert "sendMessage" in methods


def test_the_shipped_default_is_off():
    """Pin the default itself, independent of the patched config above."""
    from navig.gateway.channels.telegram_checklist import TelegramChecklistMixin
    ch = _channel()
    with patch("navig.config.get_config_manager") as cm:
        cm.return_value.get.return_value = {}          # nothing configured
        cfg = TelegramChecklistMixin._get_checklist_config(ch)
    assert cfg["checklist_enabled"] is False


def test_an_unreadable_config_fails_closed():
    from navig.gateway.channels.telegram_checklist import TelegramChecklistMixin
    ch = _channel()
    with patch("navig.config.get_config_manager", side_effect=RuntimeError("unreadable")):
        cfg = TelegramChecklistMixin._get_checklist_config(ch)
    assert cfg["checklist_enabled"] is False, "an unreadable config switched an opt-in feature on"


def test_a_failed_checklist_api_still_delivers_the_reply_as_text():
    """`sendChecklist` needs a recent Bot API; an older server must not lose the reply."""
    ch = _channel()

    async def _api_call(method, params=None, **kw):
        ch.api.append((method, dict(params or {})))
        if method == "sendChecklist":
            raise RuntimeError("Bad Request: method not found")
        return {"ok": True, "result": {"message_id": 1}}

    ch._api_call = _api_call
    methods = _send(ch, _LIST_REPLY, enabled=True)
    assert "sendChecklist" in methods, "it must at least have been attempted"
    assert "sendMessage" in methods, "and the reply must still reach the user as text"
