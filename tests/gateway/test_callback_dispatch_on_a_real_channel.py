"""Press the buttons on a REAL TelegramChannel, through the REAL dispatch.

Every other test in this area builds the channel with ``TelegramChannel.__new__`` and asserts
``hasattr``. That proves a name resolves; it does not prove the button works. This file
constructs the channel the way the gateway does (``create_telegram_channel``), lets
``__init__`` run, and drives ``CallbackHandler.handle()`` with the same dict Telegram would
POST. Only the network boundary (``_api_call`` / ``send_message``) is stubbed.

It exists because the bugs this session fixed all shared one property: the handler either
raised or fell through ``if handler:`` -- and a ``hasattr`` assertion cannot tell "resolves"
from "runs to completion and produces the panel". These tests assert the SIDE EFFECT.

A fake bot token constructs a channel in ~0ms with no network; nothing here needs a bot.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


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


def _real_channel():
    from navig.gateway.channels.telegram import create_telegram_channel

    ch = create_telegram_channel(_Gateway(), {"bot_token": "123456:FAKE", "allowed_users": [7]})
    assert ch is not None
    assert ch._cb_handler is not None, "the callback handler is wired in __init__"
    ch.calls: list[str] = []

    async def _api_call(method, params=None, **kw):
        ch.calls.append(method)
        if method == "getFile":
            return {"file_path": "x.mp3"}
        return {"ok": True, "result": {"message_id": 1}}

    async def send_message(chat_id, text, **kw):
        ch.calls.append(f"send:{text[:40]}")
        return {"message_id": 2}

    ch._api_call = _api_call
    ch.send_message = send_message
    return ch


def _press(data: str, *, chat: int = -100, user: int = 7, msg: int = 5) -> dict:
    """The dict Telegram POSTs for an inline-button press."""
    return {
        "id": f"cb-{data}",
        "data": data,
        "from": {"id": user},
        "message": {"chat": {"id": chat}, "message_id": msg},
    }


def _effects(ch) -> list[str]:
    """Everything the press did apart from acknowledging itself."""
    return [c for c in ch.calls if c != "answerCallbackQuery"]


def test_a_real_channel_constructs_with_every_binding_present():
    ch = _real_channel()
    assert [c.__name__ for c in type(ch).__mro__] == ["TelegramChannel", "object"]
    assert getattr(ch, "gateway", None) is not None, "the gateway reference must be stored"


@pytest.mark.parametrize(
    "button, expects",
    [
        ("st_goto_settings", "editMessageText"),
        ("st_goto_voice_provider", "editMessageText"),
        ("st_goto_providers", "send:"),
        ("st_goto_focus", "send:"),
    ],
)
def test_a_settings_nav_button_renders_its_panel(button, expects):
    """These four were dead: their names sat in a dispatch table nothing could resolve."""
    ch = _real_channel()
    asyncio.run(ch._cb_handler.handle(_press(button)))
    fx = _effects(ch)
    assert any(f.startswith(expects) for f in fx), (
        f"{button} answered the tap and then did nothing — effects: {fx}"
    )


def test_kill_confirm_reaches_its_handler():
    """It used to answer 'Killing…' and kill nothing. A bogus id must now be REJECTED,
    which proves the handler ran rather than the branch falling through."""
    ch = _real_channel()
    asyncio.run(ch._cb_handler.handle(_press("kill_confirm:not-a-real-id")))
    fx = _effects(ch)
    assert any("Invalid kill confirmation" in f for f in fx), (
        f"the kill handler did not run — effects: {fx}"
    )


def test_an_unknown_button_is_still_reported_not_swallowed():
    """The dispatch's own fallthrough must stay visible."""
    ch = _real_channel()
    answers = []

    async def _answer(cb_id, text="", show_alert=False):
        answers.append(text)

    ch._cb_handler._answer = _answer
    asyncio.run(ch._cb_handler.handle(_press("no_such_button_xyz")))
    # An unrecognised key is treated as an expired card ("Button expired") or an unknown
    # action -- either is fine. What is NOT fine is an empty acknowledgement, which is what
    # the dead buttons this file guards against used to produce.
    told = [a for a in answers if a.strip()]
    assert told, f"an unknown button was acknowledged with nothing said: {answers}"
