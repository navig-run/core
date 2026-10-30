"""The reminder ping as one card, rendered both ways.

The ping is the most-seen card in the product, and it has two projections: the
classic one every chat can parse, and the rich one a modern client gets. These
tests pin that they say the same thing and that neither can break the delivery.
"""

from __future__ import annotations

from navig.telegram import reminder_card

MSG = (
    "Dentist — 1 day before (Mon 26 Oct · 14:00)\n"
    "Clinic, Dr Sokolova. LE MOTIF: cleaning. EMPORTER: card and policy."
)


def test_the_rich_ping_opens_with_a_real_heading() -> None:
    html = reminder_card.render_rich("⏰ Reminder", MSG)

    assert html.startswith("<h1>Dentist</h1>")
    # The caption joins the "when" line: its own paragraph above the headline said
    # what the notification already says (owner report, 2026-10-26).
    assert "<i>⏰ Reminder · 1 day before · Mon 26 Oct · 14:00</i>" in html


def test_the_classic_ping_carries_no_rich_only_tag() -> None:
    text = reminder_card.render("⏰ Reminder", MSG)

    assert "<h1>" not in text
    assert text.startswith("<b>Dentist</b>")
    assert "<i>⏰ Reminder · 1 day before · Mon 26 Oct · 14:00</i>" in text


def test_a_note_becomes_labelled_lines_in_both_renders() -> None:
    rich = reminder_card.render_rich("⏰ Reminder", MSG)
    classic = reminder_card.render("⏰ Reminder", MSG)

    assert "<b>LE MOTIF:</b>" in rich and "<b>LE MOTIF:</b>" in classic
    assert "<b>EMPORTER:</b>" in rich and "<b>EMPORTER:</b>" in classic


def test_a_bare_time_message_still_gets_a_title() -> None:
    # A one-off /remindme row has no lead tail at all — the card must not open blank.
    html = reminder_card.render_rich("⏰ Reminder", "Call the bank")

    assert "<h1>Call the bank</h1>" in html


def test_an_empty_header_never_leaves_an_empty_title() -> None:
    html = reminder_card.render_rich("", "")

    assert "<h1>⏰</h1>" in html


def test_the_ping_card_is_one_paragraph_for_the_note() -> None:
    card = reminder_card.ping_card("", MSG)

    assert card.title == "Dentist"
    assert card.meta.startswith("1 day before")
    assert len(card.blocks) == 1  # the note, as a single paragraph block


def test_the_countdown_is_a_live_tg_time_not_a_frozen_word() -> None:
    # "in 2 h" is wrong the moment it is sent; a <tg-time> keeps counting in the client.
    html = reminder_card.render_rich("⏰ Reminder", MSG, due_unix=1761480000)

    assert '<tg-time unix="1761480000" format="R">due</tg-time>' in html


def test_a_note_that_is_really_a_sheet_of_labels_becomes_a_table() -> None:
    msg = (
        "Dentist — 1 day before (Mon 26 Oct · 14:00)\n"
        "Clinic: 14 Lenin St\nDoctor: Dr Sokolova\nBring: card and policy"
    )
    html = reminder_card.render_rich("⏰ Reminder", msg)

    assert "<table" in html
    assert "14 Lenin St" in html and "Dr Sokolova" in html
    # Classic has no table element: the same words stay readable there.
    classic = reminder_card.render("⏰ Reminder", msg)
    assert "<table" not in classic and "Dr Sokolova" in classic


def test_the_keyboard_offers_done_and_an_hour() -> None:
    rows = reminder_card.keyboard(7)

    datas = [button["callback_data"] for row in rows for button in row]
    assert datas == ["rem:done:7", "rem:snooze:7"]


def test_a_pressed_ping_keeps_its_words_and_drops_its_buttons() -> None:
    card = reminder_card.state_card("⏰ Reminder", MSG, state="✅ done")

    assert card.title == "Dentist"  # the record of what was done, not a new sentence
    assert card.meta.endswith("✅ done")
    assert card.rows == ()  # both actions are spent
