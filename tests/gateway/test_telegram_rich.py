"""Tests for the Bot API 10.2 rich-text formatting upgrades.

Covers:
  * expandable blockquotes in ``md_to_html`` (heuristic + ``>!`` marker),
  * the tag-safe HTML splitter ``split_html_for_telegram``,
  * ``TelegramChannel._md_to_html`` now delegating to the full converter,
  * the ``_reply_is_block_rich`` routing heuristic.

Pure logic — no network. The TelegramChannel-based tests import the channel class
but only exercise its static methods (no daemon, no bot token).
"""

from __future__ import annotations

import unittest

from navig.gateway.channels.base import utf16_len
from navig.gateway.channels.telegram_html import (
    MAX_MESSAGE_UTF16,
    md_to_html,
    split_html_for_telegram,
)


class TestExpandableBlockquote(unittest.TestCase):
    def test_short_quote_stays_plain(self):
        out = md_to_html("> a short quote")
        self.assertIn("<blockquote>", out)
        self.assertNotIn("expandable", out)

    def test_long_multiline_quote_is_expandable(self):
        out = md_to_html("\n".join(f"> line {i}" for i in range(6)))
        self.assertIn("<blockquote expandable>", out)

    def test_forced_marker_is_expandable(self):
        out = md_to_html(">! fold this")
        self.assertIn("<blockquote expandable>", out)
        self.assertIn("fold this", out)
        self.assertNotIn("!", out.split("fold this")[0][-3:])  # marker consumed

    def test_long_char_quote_is_expandable(self):
        out = md_to_html("> " + ("x" * 350))
        self.assertIn("<blockquote expandable>", out)

    def test_quote_content_preserved(self):
        out = md_to_html(">! important\n> second line")
        self.assertIn("important", out)
        self.assertIn("second line", out)


class TestFullConverterRendersRichSet(unittest.TestCase):
    """The consolidated converter renders what the old limited one dropped."""

    def test_inline_code(self):
        self.assertIn("<code>x</code>", md_to_html("a `x` b"))

    def test_link(self):
        self.assertIn('<a href="https://n.io">n</a>', md_to_html("[n](https://n.io)"))

    def test_strikethrough(self):
        self.assertIn("<s>gone</s>", md_to_html("~~gone~~"))

    def test_fenced_code_with_language(self):
        out = md_to_html("```py\nprint(1)\n```")
        self.assertIn('<pre><code class="language-py">', out)

    def test_no_regression_on_basics(self):
        out = md_to_html("# Title\n**b** and *i*\n- one\n+ sub")
        self.assertIn("<b>Title</b>", out)
        self.assertIn("<b>b</b>", out)
        self.assertIn("<i>i</i>", out)
        self.assertIn("• one", out)
        self.assertIn("◦ sub", out)


class TestSplitHtmlForTelegram(unittest.TestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(split_html_for_telegram(""), [])

    def test_short_html_single_part_unchanged(self):
        self.assertEqual(split_html_for_telegram("<b>hi</b>"), ["<b>hi</b>"])

    def test_large_pre_block_split_keeps_tag_balanced(self):
        body = "\n".join(f"row {i} " + "y" * 80 for i in range(120))
        parts = split_html_for_telegram(f"<pre>{body}</pre>", max_utf16=MAX_MESSAGE_UTF16)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(utf16_len(p), MAX_MESSAGE_UTF16)
            self.assertEqual(p.count("<pre>"), p.count("</pre>"))

    def test_split_preserves_payload(self):
        body = "\n".join(f"row {i} " + "y" * 80 for i in range(120))
        parts = split_html_for_telegram(f"<pre>{body}</pre>", max_utf16=MAX_MESSAGE_UTF16)
        rejoined = "".join(parts).replace("</pre>", "").replace("<pre>", "")
        self.assertEqual(rejoined, body)

    def test_nested_anchor_reopened_with_href(self):
        # a link that straddles a boundary must reopen with its href intact
        long_text = "z" * (MAX_MESSAGE_UTF16 + 500)
        html = f'<a href="https://x.io">{long_text}</a>'
        parts = split_html_for_telegram(html, max_utf16=MAX_MESSAGE_UTF16)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertEqual(p.count("<a "), p.count("</a>"))
        self.assertIn('href="https://x.io"', parts[1])


class TestBlockRichHeuristic(unittest.TestCase):
    def setUp(self):
        from navig.gateway.channels.telegram import TelegramChannel

        self.is_rich = TelegramChannel._reply_is_block_rich

    def test_table_is_rich(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        self.assertTrue(self.is_rich(md))

    def test_divider_is_rich(self):
        self.assertTrue(self.is_rich("above\n\n---\n\nbelow"))

    def test_heading_is_rich(self):
        self.assertTrue(self.is_rich("## Section\nbody"))

    def test_long_quote_is_rich(self):
        self.assertTrue(self.is_rich("\n".join("> q" for _ in range(5))))

    def test_forced_quote_marker_is_rich(self):
        self.assertTrue(self.is_rich(">! folded\nmore"))

    def test_plain_text_is_not_rich(self):
        self.assertFalse(self.is_rich("just a normal sentence reply."))

    def test_bold_only_is_not_rich(self):
        self.assertFalse(self.is_rich("here is **bold** and a `code` snippet"))

    def test_empty_is_not_rich(self):
        self.assertFalse(self.is_rich(""))


class TestMdToHtmlDelegates(unittest.TestCase):
    """TelegramChannel._md_to_html now renders the full rich set (was limited)."""

    def setUp(self):
        from navig.gateway.channels.telegram import TelegramChannel

        self.conv = TelegramChannel._md_to_html

    def test_renders_code_block(self):
        self.assertIn('<pre><code class="language-py">', self.conv("```py\nx=1\n```"))

    def test_renders_link(self):
        self.assertIn('<a href="https://n.io">n</a>', self.conv("[n](https://n.io)"))

    def test_renders_expandable_quote(self):
        self.assertIn("<blockquote expandable>", self.conv(">! folded quote here"))

    def test_never_raises_on_garbage(self):
        # must degrade to escaped text, never raise (reply path depends on it)
        self.assertIsInstance(self.conv("<weird & unclosed"), str)


# ── async: send_message length guard (pytest, asyncio_mode=auto) ──────────────

async def test_send_message_splits_over_length_html():
    """An over-length HTML body is split tag-safely into multiple ≤4096 sends."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)  # no daemon / session needed
    calls: list[tuple[str, dict]] = []

    async def fake_api(method, data):
        calls.append((method, dict(data)))
        return {"message_id": len(calls)}

    ch._api_call = fake_api  # type: ignore[method-assign]

    big = "<pre>" + "\n".join("y" * 90 for _ in range(200)) + "</pre>"
    assert utf16_len(big) > MAX_MESSAGE_UTF16
    res = await ch.send_message(12345, big, parse_mode="HTML")

    assert len(calls) > 1  # actually split
    for method, data in calls:
        assert method == "sendMessage"
        assert utf16_len(data["text"]) <= MAX_MESSAGE_UTF16
        assert data["text"].count("<pre>") == data["text"].count("</pre>")
    assert res == {"message_id": len(calls)}  # returns the last result


async def test_send_message_short_body_single_call():
    """A normal short reply is one send — the guard must not trigger."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)
    calls: list[tuple[str, dict]] = []

    async def fake_api(method, data):
        calls.append((method, dict(data)))
        return {"message_id": 1}

    ch._api_call = fake_api  # type: ignore[method-assign]

    await ch.send_message(12345, "<b>hi there</b>", parse_mode="HTML")
    assert len(calls) == 1


# ── in-body buttons vs reply_markup: a card must not draw its buttons twice ──

CARD_WITH_BODY_BUTTONS = (
    "<h2>Evening</h2>"
    "<p>Three things to close the day.</p>"
    '<tg-button-row align="left">'
    '<button data="eve:log_shipped" style="success">Log it</button>'
    "</tg-button-row>"
)
CARD_WITHOUT_BODY_BUTTONS = "<h2>Evening</h2><p>Three things to close the day.</p>"
KEYBOARD = [[{"text": "Log it", "callback_data": "eve:log_shipped"}]]


class _Resp:
    status = 200

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


class _Post:
    def __init__(self, resp: _Resp):
        self._resp = resp

    async def __aenter__(self) -> _Resp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


class _Session:
    """Records every ``post`` and answers with one canned Telegram result."""

    def __init__(self, sent: list, payload: dict):
        self.sent = sent
        self.payload = payload

    def post(self, url: str, json: dict | None = None) -> _Post:  # noqa: A002
        self.sent.append((url, json))
        return _Post(_Resp(self.payload))


def _rich_channel():
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)
    ch.base_url = "https://api.telegram.test"
    ch._rich_messages_enabled = lambda: True  # type: ignore[method-assign]
    return ch


async def test_rich_send_omits_the_keyboard_when_the_body_draws_its_buttons():
    """``<tg-button-row>`` renders in the bubble, so the keyboard would duplicate it."""
    ch = _rich_channel()
    sent: list = []
    ch._session = _Session(sent, {"ok": True, "result": {"message_id": 9}})

    out = await ch.send_rich_message(1, html=CARD_WITH_BODY_BUTTONS, keyboard=KEYBOARD)

    assert out == {"message_id": 9}
    assert sent[0][0].endswith("/sendRichMessage")
    assert "reply_markup" not in sent[0][1]


async def test_rich_send_keeps_the_keyboard_when_the_body_has_no_buttons():
    ch = _rich_channel()
    sent: list = []
    ch._session = _Session(sent, {"ok": True, "result": {"message_id": 9}})

    await ch.send_rich_message(1, html=CARD_WITHOUT_BODY_BUTTONS, keyboard=KEYBOARD)

    assert sent[0][1]["reply_markup"]["inline_keyboard"] == KEYBOARD


async def test_the_classic_fallback_still_carries_the_keyboard():
    """``sendMessage`` has no in-body buttons — dropping the keyboard would lose the actions."""
    ch = _rich_channel()
    ch._rich_messages_enabled = lambda: False  # type: ignore[method-assign]
    ch._session = object()  # send_rich_message bails out without one
    calls: list[dict] = []

    async def fake_send(chat_id, text, **kwargs):
        calls.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": 5}

    ch.send_message = fake_send  # type: ignore[method-assign]

    await ch.send_rich_message(
        1, html=CARD_WITH_BODY_BUTTONS, fallback_html="Evening", keyboard=KEYBOARD
    )

    assert calls[0]["text"] == "Evening"
    assert calls[0]["keyboard"] == KEYBOARD


async def test_editing_a_rich_card_drops_the_keyboard_its_body_already_draws():
    ch = _rich_channel()
    calls: list[tuple[str, dict]] = []

    async def fake_api(method, data):
        calls.append((method, dict(data)))
        return {"message_id": 7}

    ch._api_call = fake_api  # type: ignore[method-assign]

    await ch.edit_rich_message(
        1, 7, html=CARD_WITH_BODY_BUTTONS, fallback_html="Evening", keyboard=KEYBOARD
    )

    assert calls[0][0] == "editMessageText"
    assert calls[0][1]["rich_message"]["html"] == CARD_WITH_BODY_BUTTONS
    assert "reply_markup" not in calls[0][1]


async def test_editing_a_rich_card_keeps_the_keyboard_its_body_does_not_draw():
    ch = _rich_channel()
    calls: list[tuple[str, dict]] = []

    async def fake_api(method, data):
        calls.append((method, dict(data)))
        return {"message_id": 7}

    ch._api_call = fake_api  # type: ignore[method-assign]

    await ch.edit_rich_message(
        1, 7, html=CARD_WITHOUT_BODY_BUTTONS, fallback_html="Evening", keyboard=KEYBOARD
    )

    assert calls[0][1]["reply_markup"]["inline_keyboard"] == KEYBOARD


if __name__ == "__main__":
    unittest.main()
