"""Tests for the poll surface: the parser, the two cards, and the handler's two paths.

The Bot API refuses a poll for reasons a message never hits — one option, eleven options, a
300-character question, a quiz pointing at an option that does not exist. Each of those is
asserted here, because a refusal has to come back as a card that explains it instead of a
silent no-op.
"""

from __future__ import annotations

import pytest

from navig.telegram import poll_card

# ── the parser ─────────────────────────────────────────────────────────────────


def test_the_ordinary_shape_splits_question_and_options() -> None:
    poll = poll_card.parse_poll("/poll Ship it tonight? | yes | no")
    assert poll.error == ""
    assert poll.question == "Ship it tonight?"
    assert poll.options == ("yes", "no")
    assert poll.anonymous is True
    assert poll.multi is False
    assert poll.is_quiz is False


def test_flags_are_read_wherever_they_sit_including_on_the_last_option() -> None:
    poll = poll_card.parse_poll("/poll Where? | here | there --public --multi")
    assert poll.error == ""
    assert poll.anonymous is False
    assert poll.multi is True
    assert poll.options == ("here", "there")  # the flag is not an option


def test_a_quiz_marks_exactly_one_option() -> None:
    poll = poll_card.parse_poll("/poll 2+2? | 3 | 4 | 5 --quiz=2")
    assert poll.quiz_index == 1
    html = poll_card.created_card(poll).rich_html()
    assert "✅ 4" in html
    assert html.count("✅") == 1


def test_the_pipe_is_always_the_separator() -> None:
    """The format has no escape for a literal pipe: every pipe starts a new option."""
    poll = poll_card.parse_poll("/poll Which? | read|write | read only")
    assert poll.options == ("read", "write", "read only")


def test_one_option_is_refused_with_the_reason() -> None:
    poll = poll_card.parse_poll("/poll only one")
    assert poll.options == ()
    assert "at least two options" in poll.error


def test_eleven_options_are_refused() -> None:
    options = " | ".join(f"opt{i}" for i in range(11))
    poll = poll_card.parse_poll(f"/poll many? | {options}")
    assert "at most ten" in poll.error


def test_a_question_over_the_limit_is_refused() -> None:
    poll = poll_card.parse_poll(f"/poll {'q' * 301} | a | b")
    assert "300 characters" in poll.error


def test_an_over_long_option_is_refused() -> None:
    poll = poll_card.parse_poll(f"/poll q? | {'o' * 101} | b")
    assert "100 characters" in poll.error


def test_a_quiz_pointing_nowhere_is_refused() -> None:
    poll = poll_card.parse_poll("/poll q? | a | b --quiz=9")
    assert "pointing at one of its options" in poll.error


def test_a_quiz_cannot_also_be_multi() -> None:
    poll = poll_card.parse_poll("/poll q? | a | b --quiz=1 --multi")
    assert "one right answer" in poll.error


def test_no_pipe_at_all_asks_for_the_question_first() -> None:
    poll = poll_card.parse_poll("/poll")
    assert "question comes first" in poll.error


# ── the cards ─────────────────────────────────────────────────────────────────


def test_the_usage_card_tabulates_the_flags_and_shows_a_copyable_syntax() -> None:
    html = poll_card.usage_card().rich_html()
    assert "<h2>🗳️ Polls</h2>" in html
    assert '<code class="language-text">' in html
    assert "<th>Flag</th><th>What it does</th>" in html
    assert "<code>--multi</code>" in html and "<code>--quiz=N</code>" in html
    assert "Separate the question" in html


def test_a_refusal_is_a_callout_on_the_usage_card() -> None:
    card = poll_card.usage_card(note="A poll needs at least two options.")
    html = card.rich_html()
    assert "<aside>" in html
    assert "at least two options" in html


def test_the_created_card_states_the_mode_and_the_numbers() -> None:
    poll = poll_card.parse_poll("/poll Where? | here | there --public --multi")
    card = poll_card.created_card(poll, buttons=poll_card.created_rows())
    html = card.rich_html()
    assert "<h2>🗳️ Poll posted</h2>" in html
    assert "<i>regular poll</i>" in html
    assert "<td>Options</td><td><b>2</b></td>" in html
    assert "visible" in html
    assert "Results update inside the poll" in html
    assert card.keyboard()[0][0]["callback_data"] == "nav:cmd:/poll"


def test_no_poll_card_ships_rich_tags_in_its_classic_projection() -> None:
    poll = poll_card.parse_poll("/poll q? | a | b")
    for card in (
        poll_card.usage_card(),
        poll_card.created_card(poll),
        poll_card.usage_card(note="nope"),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<ol", "<aside", "<footer"):
            assert tag not in classic, (tag, classic[:160])


# ── the handler ───────────────────────────────────────────────────────────────


class _Channel:
    """A channel that records the poll it was asked to post."""

    def __init__(self, *, accepts: bool = True) -> None:
        self.accepts = accepts
        self.polls: list[dict] = []

    async def send_poll(self, chat_id, question, options, **kwargs):
        self.polls.append(
            {"chat_id": chat_id, "question": question, "options": options, **kwargs}
        )
        return {"message_id": 700, "poll": {"id": "p1"}} if self.accepts else None


def _record_cards(monkeypatch):
    from navig.telegram import card_transport

    cards: list = []

    async def fake_send(channel, chat_id, card, **kw):
        cards.append(card)
        return {"message_id": 1}

    async def fake_edit(channel, chat_id, message_id, card, **kw):
        cards.append(card)
        return True

    monkeypatch.setattr(card_transport, "send_card", fake_send)
    monkeypatch.setattr(card_transport, "edit_card", fake_edit)
    return cards


@pytest.mark.asyncio
async def test_a_valid_poll_is_posted_and_recorded(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    cards = _record_cards(monkeypatch)
    channel = _Channel()

    await TelegramCommandsMixin._handle_poll(
        channel, 42, 7, "/poll Ship it? | yes | no --public"
    )

    assert channel.polls[0]["question"] == "Ship it?"
    assert channel.polls[0]["options"] == ["yes", "no"]
    assert channel.polls[0]["is_anonymous"] is False
    assert channel.polls[0]["poll_type"] == "regular"
    assert "Poll posted" in cards[0].rich_html()


@pytest.mark.asyncio
async def test_a_quiz_is_posted_as_a_quiz_with_its_answer(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    _record_cards(monkeypatch)
    channel = _Channel()

    await TelegramCommandsMixin._handle_poll(channel, 42, 7, "/poll 2+2? | 3 | 4 --quiz=2")

    assert channel.polls[0]["poll_type"] == "quiz"
    assert channel.polls[0]["correct_option_id"] == 1


@pytest.mark.asyncio
async def test_an_invalid_poll_posts_nothing_and_explains(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    cards = _record_cards(monkeypatch)
    channel = _Channel()

    await TelegramCommandsMixin._handle_poll(channel, 42, 7, "/poll just one thing")

    assert channel.polls == []  # nothing reached Telegram
    html = cards[0].rich_html()
    assert "<aside>" in html and "at least two options" in html


@pytest.mark.asyncio
async def test_a_refused_poll_says_nothing_was_posted(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    cards = _record_cards(monkeypatch)
    channel = _Channel(accepts=False)

    await TelegramCommandsMixin._handle_poll(channel, 42, 7, "/poll q? | a | b")

    assert len(channel.polls) == 1  # it was attempted
    assert "Telegram refused that poll" in cards[0].rich_html()
