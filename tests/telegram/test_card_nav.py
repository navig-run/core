"""Card navigation: the pager, the close button, and the callbacks behind them."""

from __future__ import annotations

import pytest

from navig.telegram.card_nav import (
    CLOSE_LABEL,
    DISABLED_NOOP,
    NOOP,
    chip,
    close_row,
    menu_rows,
    pager,
    parse,
    sequence,
    settle,
    step,
    walk,
)


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.calls.append((chat_id, message_id, text, keyboard))
        return {"message_id": message_id}


# ── rows ────────────────────────────────────────────────────────────────────


def test_a_chip_reports_state_and_answers_its_own_press():
    cell = chip("2 / 5")
    assert cell["text"] == "2 / 5"
    assert cell["callback_data"] == NOOP
    # 10.3 marker + noop fallback: the button is inert either way.
    assert cell["disabled"] == {}


def test_a_step_carries_its_callback_only_when_it_can_move():
    assert step("▶", "todo:next")["callback_data"] == "todo:next"
    dead = step("▶", "todo:next", enabled=False)
    assert "callback_data" not in dead or dead["callback_data"] == NOOP
    assert dead["disabled"] == {}


def test_one_item_needs_no_pager():
    assert pager(0, 1, prefix="todo") == []
    assert pager(0, 0, prefix="todo") == []


def test_the_pager_walks_and_greys_the_ends():
    first = pager(0, 3, prefix="todo")
    assert [cell["text"] for cell in first] == ["◀", "1 / 3", "▶"]
    assert first[0]["callback_data"] == NOOP  # cannot step back from the first
    assert first[2]["callback_data"] == "todo:next"
    middle = pager(1, 3, prefix="todo")
    assert middle[0]["callback_data"] == "todo:prev"
    assert middle[2]["callback_data"] == "todo:next"
    last = pager(2, 3, prefix="todo")
    assert last[2]["callback_data"] == NOOP


def test_an_out_of_range_index_is_clamped_not_crashed():
    # A card drawn when the list had five items, pressed after it shrank to two.
    assert pager(9, 2, prefix="todo")[1]["text"] == "2 / 2"


def test_close_is_one_button_with_one_verb():
    row = close_row("todo")
    assert row == [{"text": CLOSE_LABEL, "callback_data": "todo:close"}]


def test_menu_rows_drop_the_empties_and_keep_the_order():
    rows = menu_rows([], [{"text": "a"}], [], [{"text": "b"}])
    assert rows == [[{"text": "a"}], [{"text": "b"}]]


# ── settle ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settling_a_card_strips_every_button():
    channel = FakeChannel()
    assert await settle(channel, 1, 42, title="Call the accountant") is True
    chat_id, message_id, text, keyboard = channel.calls[0]
    assert (chat_id, message_id) == (1, 42)
    assert text == "<i>✕ Call the accountant</i>"
    # An empty inline_keyboard is explicit: omitting reply_markup leaves the old
    # buttons live, which is the dead-button bug this kit exists to prevent.
    assert keyboard == []


@pytest.mark.asyncio
async def test_settling_without_a_message_or_an_edit_does_nothing():
    assert await settle(FakeChannel(), 1, None, title="x") is False

    class Bare:
        pass

    assert await settle(Bare(), 1, 42, title="x") is False


@pytest.mark.asyncio
async def test_a_failing_edit_never_breaks_the_dispatcher():
    class Broken(FakeChannel):
        async def edit_message(self, *args, **kwargs):
            raise RuntimeError("Telegram said no")

    assert await settle(Broken(), 1, 42, title="x") is False


@pytest.mark.asyncio
async def test_a_note_rides_under_the_close_line():
    channel = FakeChannel()
    await settle(channel, 1, 42, title="Reminder", note="dismissed")
    assert channel.calls[0][2] == "<i>✕ Reminder</i>\n<i>dismissed</i>"


# ── callbacks ───────────────────────────────────────────────────────────────


def test_parse_splits_verb_from_a_rest_that_may_hold_colons():
    assert parse("todo:prev", "todo") == ("prev", "")
    assert parse("ag:open:2026-10-25T14:00", "ag") == ("open", "2026-10-25T14:00")


def test_parse_refuses_another_cards_callback():
    assert parse("ag:open:x", "todo") == ("", "")
    assert parse("", "todo") == ("", "")


def test_walk_moves_one_step_and_stops_at_the_ends():
    assert walk(0, 3, "todo:next", "todo") == 1
    assert walk(1, 3, "todo:prev", "todo") == 0
    assert walk(2, 3, "todo:next", "todo") == 2
    assert walk(0, 3, "todo:prev", "todo") == 0


def test_walk_lets_a_stale_card_land_on_a_real_item():
    # Keyboard drawn for five items; the list now holds two.
    assert walk(4, 2, "todo:next", "todo") == 1
    assert walk(3, 2, "todo:prev", "todo") == 1


def test_walk_ignores_verbs_it_does_not_own():
    assert walk(1, 3, "todo:close", "todo") is None
    assert walk(1, 3, "ag:next", "todo") is None
    assert walk(0, 0, "todo:next", "todo") is None


def test_sequence_materialises_once_so_index_math_matches_what_is_drawn():
    generator = (n for n in range(3))
    items = sequence(generator)
    assert items == [0, 1, 2]
    assert items[2] == 2


def test_the_disabled_noop_spelling_is_shared():
    # card_doc emits this string for a disabled InlineKeyboardButton; one
    # spelling means one dispatcher branch answers it.
    assert DISABLED_NOOP.startswith("noop")
