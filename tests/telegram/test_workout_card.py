"""Tests for the /workout cards."""

from __future__ import annotations

import pytest

from navig.telegram import workout_card


def _callbacks(card) -> list[str]:
    return [b["callback_data"] for row in (card.keyboard() or []) for b in row]


def test_the_usage_card_names_both_forms_in_a_table_and_a_copy_block() -> None:
    card = workout_card.usage_card()
    html = card.rich_html()
    assert "<h2>💪 Workout reminder</h2>" in html
    assert "<code>/workout 07:30</code>" in html
    assert "<code>/workout daily 07:30</code>" in html
    assert "weekday habit" in html
    assert '<code class="language-text">/workout 07:30' in html
    assert card.keyboard()[0][0]["copy_text"]["text"] == "/workout 07:30"


def test_a_bad_time_is_the_same_card_with_the_reason_on_top() -> None:
    html = workout_card.usage_card(note="workout.card.bad_time_hint").rich_html()
    assert "Time must be 24h" in html
    assert "hours 0–23" in html
    assert "<b>Two forms</b>" in html  # the forms are still offered


def test_a_one_time_reminder_states_the_time_the_day_and_the_id() -> None:
    card = workout_card.reminder_card(when="07:30", rolled=False, reminder_id=4242)
    html = card.rich_html()
    assert "<h2>⏰ Workout reminder set</h2>" in html
    assert "<b>07:30</b>" in html
    assert "today" in html
    assert "<code>4242</code>" in html


def test_a_rolled_reminder_says_tomorrow() -> None:
    html = workout_card.reminder_card(when="06:00", rolled=True).rich_html()
    assert "tomorrow" in html
    assert ">today<" not in html


def test_the_one_time_card_offers_the_recurring_version_with_the_same_time() -> None:
    card = workout_card.reminder_card(when="07:30", rolled=False, reminder_id=1)
    assert "nav:cmd:/workout daily 07:30" in _callbacks(card)


def test_no_reminder_id_means_no_id_row() -> None:
    html = workout_card.reminder_card(when="07:30", rolled=False).rich_html()
    assert "Reminder</td>" not in html  # the sheet row is omitted, not blank


def test_the_habit_card_states_the_weekday_schedule() -> None:
    card = workout_card.habit_card(when="07:30")
    html = card.rich_html()
    assert "<h2>💪 Daily workout habit</h2>" in html
    assert "<b>07:30</b>" in html
    assert "weekdays (Mon–Fri)" in html
    assert "navig habit list" in html
    assert "nav:cmd:/habits" in _callbacks(card)


@pytest.mark.parametrize(
    "card",
    [
        workout_card.usage_card(),
        workout_card.usage_card(note="workout.card.bad_time_hint"),
        workout_card.reminder_card(when="07:30", rolled=False, reminder_id=1),
        workout_card.habit_card(when="07:30"),
    ],
)
def test_no_workout_card_ships_rich_tags_in_its_classic_projection(card) -> None:
    classic = card.classic_html()
    for tag in ("<h2", "<table", "<aside", "<footer", "<pre><code"):
        assert tag not in classic, (tag, classic[:160])


@pytest.mark.asyncio
async def test_the_handler_sends_a_card_for_the_usage_answer(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    recorded: list = []

    async def fake_send(channel, chat_id, card, **kw):
        recorded.append(card)
        return {"message_id": 1}

    monkeypatch.setattr(card_transport, "send_card", fake_send)

    await TelegramCommandsMixin._handle_workout(object(), 1, 2, "/workout nonsense")

    assert len(recorded) == 1
    assert "Workout reminder" in recorded[0].rich_html()
