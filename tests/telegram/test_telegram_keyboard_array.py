"""Regression: the inline keyboard must be an ARRAY of arrays, never a dict.

Production bug (2026-07-21): a research-tier Telegram reply ("Tell me about
what is Fight Club") generated its answer but never replaced the "Thinking…"
placeholder. The gateway logged, three times:

    Telegram API error: Bad Request: field "inline_keyboard" must be of type Array

Root cause: ``_finalize_streamed_message`` / ``_send_response`` received the
keyboard from ``ResponseKeyboardBuilder.build()`` as a BARE list-of-rows
(``list[list[dict]]``) — never a ``{"inline_keyboard": ...}`` markup dict. But
the ``extra_krow`` append branch guarded on ``isinstance(keyboard, dict)`` (which
is always False here), so it took the ``else`` path and set
``keyboard = {"inline_keyboard": [extra_krow]}`` — a dict. ``edit_message`` /
``send_message`` then wrap ``keyboard`` again as ``{"inline_keyboard": keyboard}``,
producing ``inline_keyboard = {"inline_keyboard": [...]}`` — a dict where Telegram
demands an array. The read-site buttons attached to web-search replies made
``extra_krow`` truthy, so every research answer with sources went stuck.

These tests drive both send paths with an ``extra_krow`` and assert the final
Telegram ``reply_markup.inline_keyboard`` payload is a list-of-lists (and that a
real builder keyboard is no longer silently discarded).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.integration

_EXTRA_ROW = [{"text": "🔎 Read example.com", "callback_data": "wf:deadbeef"}]
_BUILDER_ROW = [{"text": "📖 Go Deeper", "callback_data": "dig_deeper:cafe"}]


class _StubKeyboardBuilder:
    """Returns a fixed bare list-of-rows, exactly like ResponseKeyboardBuilder."""

    def build(self, **_kwargs):
        return [list(_BUILDER_ROW)]


def _assert_array_of_arrays(reply_markup: dict) -> list:
    """The Telegram contract Bot API enforces for inline keyboards."""
    assert isinstance(reply_markup, dict), f"reply_markup not a dict: {reply_markup!r}"
    ik = reply_markup.get("inline_keyboard")
    # This is the exact assertion that failed in production: a dict here is the bug.
    assert isinstance(ik, list), (
        f'inline_keyboard must be an Array (list), got {type(ik).__name__}: {ik!r}'
    )
    for row in ik:
        assert isinstance(row, list), f"each keyboard row must be an Array, got {row!r}"
        for btn in row:
            assert isinstance(btn, dict) and "text" in btn, f"bad button: {btn!r}"
    return ik


def _capture_api_call(ch) -> list[dict]:
    """Mock _api_call; return the list it appends every (method, data) into."""
    calls: list[dict] = []

    async def _api(method, data):
        calls.append({"method": method, "data": data})
        return {"message_id": 4242}

    ch._api_call = _api
    return calls


async def test_send_response_with_extra_krow_keeps_inline_keyboard_an_array(monkeypatch):
    """Non-stream path: _send_response(extra_krow=…) → sendMessage reply_markup is an Array."""
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._maybe_send_voice = AsyncMock(return_value=False)
    ch._rich_messages_enabled = lambda: False  # force the classic HTML path
    ch._kb_builder = _StubKeyboardBuilder()
    monkeypatch.setattr(tg, "HAS_TEMPLATES", False)

    calls = _capture_api_call(ch)

    await ch._send_response(
        chat_id=1,
        response="Fight Club is a 1999 film directed by David Fincher.",
        original_text="Tell me about what is Fight Club",
        extra_krow=list(_EXTRA_ROW),
    )

    sends = [c for c in calls if c["method"] == "sendMessage" and "reply_markup" in c["data"]]
    assert sends, f"expected a sendMessage carrying reply_markup, got: {calls!r}"
    ik = _assert_array_of_arrays(sends[-1]["data"]["reply_markup"])
    # The extra row must be present, AND the builder's own row must NOT be discarded.
    assert _EXTRA_ROW in ik, f"extra_krow row missing from keyboard: {ik!r}"
    assert list(_BUILDER_ROW) in ik, f"builder keyboard was discarded: {ik!r}"


async def test_send_response_extra_krow_only_is_an_array(monkeypatch):
    """extra_krow with no builder keyboard must still yield an Array-of-arrays."""
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._maybe_send_voice = AsyncMock(return_value=False)
    ch._rich_messages_enabled = lambda: False
    ch._kb_builder = None  # no builder → keyboard starts as None
    monkeypatch.setattr(tg, "HAS_TEMPLATES", False)

    calls = _capture_api_call(ch)

    await ch._send_response(chat_id=1, response="short reply", extra_krow=list(_EXTRA_ROW))

    sends = [c for c in calls if c["method"] == "sendMessage" and "reply_markup" in c["data"]]
    assert sends, f"expected a sendMessage carrying reply_markup, got: {calls!r}"
    ik = _assert_array_of_arrays(sends[-1]["data"]["reply_markup"])
    assert ik == [_EXTRA_ROW], f"expected exactly the extra row, got: {ik!r}"


async def test_finalize_streamed_message_with_extra_krow_is_an_array(monkeypatch):
    """The exact production path: streamed placeholder finalize → editMessageText.

    This is the path 'Tell me about what is Fight Club' took (REASON tier streams
    into the placeholder, then finalizes with read-site buttons as extra_krow).
    """
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._maybe_send_voice = AsyncMock(return_value=False)
    ch._rich_messages_enabled = lambda: False
    ch._kb_builder = _StubKeyboardBuilder()
    monkeypatch.setattr(tg, "HAS_TEMPLATES", False)

    calls = _capture_api_call(ch)

    used = await ch._finalize_streamed_message(
        chat_id=1,
        placeholder_id=99,
        response="Fight Club is a 1999 film directed by David Fincher.",
        original_text="Tell me about what is Fight Club",
        extra_krow=list(_EXTRA_ROW),
        is_deep=True,
    )

    assert used is True, "finalize should own the placeholder bubble"
    edits = [
        c for c in calls if c["method"] == "editMessageText" and "reply_markup" in c["data"]
    ]
    assert edits, f"expected an editMessageText carrying reply_markup, got: {calls!r}"
    ik = _assert_array_of_arrays(edits[-1]["data"]["reply_markup"])
    assert _EXTRA_ROW in ik, f"extra_krow row missing from keyboard: {ik!r}"
    assert list(_BUILDER_ROW) in ik, f"builder keyboard was discarded: {ik!r}"


# ---------------------------------------------------------------------------
# _reply_markup normalizer — defense-in-depth so a future append site that
# hands a {"inline_keyboard": ...} dict can't reintroduce the double-wrap.
# ---------------------------------------------------------------------------


def test_reply_markup_wraps_a_bare_list_of_rows():
    from navig.gateway.channels.telegram import TelegramChannel

    rows = [_EXTRA_ROW]
    assert TelegramChannel._reply_markup(rows) == {"inline_keyboard": rows}


def test_reply_markup_passes_through_an_existing_markup_dict():
    """A {"inline_keyboard": ...} dict must NOT be wrapped again."""
    from navig.gateway.channels.telegram import TelegramChannel

    markup = {"inline_keyboard": [_EXTRA_ROW]}
    out = TelegramChannel._reply_markup(markup)
    assert out is markup, "existing markup dict must pass through unchanged"
    assert isinstance(out["inline_keyboard"], list), "must stay an Array"


async def test_send_message_with_markup_dict_is_not_double_wrapped():
    """send_message(keyboard=<markup dict>) must yield a valid Array, not a nested dict."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    calls = _capture_api_call(ch)

    # A caller that (wrongly) hands an already-wrapped markup dict — the exact
    # shape the pre-fix append branches produced — must still send a valid keyboard.
    await ch.send_message(1, "hi", keyboard={"inline_keyboard": [_EXTRA_ROW]})

    sends = [c for c in calls if c["method"] == "sendMessage" and "reply_markup" in c["data"]]
    assert sends, f"expected a sendMessage carrying reply_markup, got: {calls!r}"
    ik = _assert_array_of_arrays(sends[-1]["data"]["reply_markup"])
    assert ik == [_EXTRA_ROW]
