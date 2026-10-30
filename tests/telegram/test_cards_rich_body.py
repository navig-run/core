"""A Screen may author a rich body; the transport picks the projection.

The legacy ``Screen`` is the paging shell most plugins use (tasks, reminders,
habits). It used to carry one body, sent with ``parse_mode=HTML`` — which
rejects the blocks a rich message draws (``<ol>``, ``<table>``, ``<details>``,
``<tg-time>``). A Screen now carries both, and every path that can deliver a
card has to try the rich one first with the plain one as its fallback.

The fallback is the load-bearing part: ``sendRichMessage`` can be answered
"unknown method" by an older Bot API server, and retrying that call with the
rich HTML fails the parse outright — the card never arrives at all.
"""

from __future__ import annotations

import itertools

import pytest

from navig.telegram import cards

_KEYS = itertools.count()


def _ctx() -> cards.Context:
    """A key of its own: an identical card inside the window is withheld as a retry."""
    return cards.Context("t", f"rich-{next(_KEYS)}")


class FakeChannel:
    """Records what the transport asked for; ``rich`` decides which path answers."""

    def __init__(self, rich: bool = True) -> None:
        self.rich = rich
        self.calls: list[tuple[str, dict]] = []

    async def send_rich_message(self, chat_id, *, html, fallback_html, keyboard=None, **kw):
        self.calls.append(("rich", {"html": html, "fallback": fallback_html, "rows": keyboard}))
        return {"message_id": 7} if self.rich else None

    async def edit_rich_message(self, chat_id, message_id, *, html, fallback_html, keyboard=None):
        self.calls.append(("edit_rich", {"html": html, "fallback": fallback_html}))
        return {"message_id": message_id} if self.rich else None

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": 8}


def _screen(**kw) -> cards.Screen:
    return cards.Screen(title="Review & go", body="plain body", **kw)


def test_a_screen_without_a_rich_body_has_no_rich_rendering():
    assert _screen().rich() is None


def test_the_rich_rendering_heads_the_title_and_escapes_it():
    rich = _screen(rich_body="<p>hi</p>").rich()
    assert rich is not None
    assert rich.startswith("<h2>Review &amp; go</h2>")
    assert "<p>hi</p>" in rich


@pytest.mark.asyncio
async def test_a_rich_screen_is_sent_rich_first_and_keeps_the_plain_twin():
    channel = FakeChannel()
    sent = await cards.send_card(
        1, _screen(rich_body="<ol><li>x</li></ol>"), _ctx(), channel=channel
    )
    kind, payload = channel.calls[0]
    assert kind == "rich"
    assert "<ol>" in payload["html"]
    # The fallback is the classic projection, never the rich body.
    assert payload["fallback"] == "Review & go\n\nplain body"
    assert sent == {"message_id": 7}


@pytest.mark.asyncio
async def test_a_refused_rich_send_still_delivers_the_plain_card():
    channel = FakeChannel(rich=False)
    await cards.send_card(1, _screen(rich_body="<ol><li>x</li></ol>"), _ctx(), channel=channel)
    assert channel.calls[0][0] == "rich"
    assert channel.calls[-1][0] == "sendMessage"
    assert channel.calls[-1][1]["text"] == "Review & go\n\nplain body"


@pytest.mark.asyncio
async def test_a_card_without_a_rich_body_never_asks_for_a_rich_send():
    channel = FakeChannel()
    await cards.send_card(1, _screen(), _ctx(), channel=channel)
    assert [call[0] for call in channel.calls] == ["sendMessage"]


def test_the_message_object_is_unwrapped_from_whichever_envelope_came_back():
    # The channel answers with the message; a direct api_call answers with ok/result.
    assert cards._sent({"message_id": 9}) == {"message_id": 9}
    assert cards._sent({"ok": True, "result": {"message_id": 9}}) == {"message_id": 9}
    assert cards._sent(None) == {}


@pytest.mark.asyncio
async def test_a_cli_send_reports_the_message_id_it_can_later_edit(monkeypatch):
    async def fake_api(channel, method, payload):
        assert method == "sendRichMessage"
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr(cards, "_api", fake_api)
    sent = await cards.send_card(1, _screen(rich_body="<ol><li>x</li></ol>"), _ctx(), channel=None)
    assert sent == {"message_id": 42}


@pytest.mark.asyncio
async def test_updating_in_place_keeps_the_rich_body():
    channel = FakeChannel()
    assert await cards.update_card(
        1,
        5,
        _screen(rich_body="<details><summary>a</summary>b</details>"),
        _ctx(),
        channel=channel,
    )
    kind, payload = channel.calls[0]
    assert kind == "edit_rich"
    assert "<details>" in payload["html"]
    assert payload["fallback"] == "Review & go\n\nplain body"
