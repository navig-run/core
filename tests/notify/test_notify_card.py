"""A sectioned body becomes a card; a two-line alert stays one message.

The daily brief is dispatched by a producer as plain text
(``navig notify send briefing "Brief 2026-10-26" -b "OUTPOST  all green…"``), and
until now it arrived exactly as written: columns held together by spaces, so on a
phone nothing lined up. ``notify_card`` reads that shape back into a table.
"""

from navig.notify import notify_card

BRIEF = """OUTPOST  all green (15-min probes)
AUTOMATION  20 task(s) ok (+2 by design)
NEXT
  1. Reply to Rakib (Telegram 1131897) - send GAZE invite (4d late)
  2. Send the organiser messages (2d late)
HABITS   ship 1/7 \u00b7 out 0/7"""


def test_the_brief_becomes_a_table_of_its_sections():
    html = notify_card.render("briefing", "Brief 2026-10-26", BRIEF)
    assert html is not None
    assert "<table" in html
    assert "<td>Outpost</td>" in html
    assert "<td>all green (15-min probes)</td>" in html
    assert "<td>Habits</td>" in html


def test_the_next_list_is_a_list_and_its_lateness_is_bold():
    html = notify_card.render("briefing", "Brief 2026-10-26", BRIEF) or ""
    assert "<h3>Next</h3>" in html
    assert "<li>Reply to Rakib (Telegram 1131897) - send GAZE invite <b>(4d late)</b></li>" in html


def test_a_two_line_alert_stays_a_plain_message():
    """An alert has no sections; a table around two lines is noise."""
    body = (
        "The scheduled job did not finish successfully.\n\n"
        "error: python was killed by SIGSEGV (exit code -11)"
    )
    assert notify_card.render("system_alert", "Cron job failed: NAVIG tasks tick", body) is None


def test_two_sections_are_not_a_report():
    body = "OUTPOST  all green\nTASKS  1 open"
    assert notify_card.render("briefing", "Brief", body) is None


def test_a_vertical_bar_inside_a_cell_reads_as_one_separator():
    """``a | b | c`` is how these bodies separate facts; a cell edge is already a border."""
    body = "AAAA  one | two\nBBBB  three\nCCCC  four"
    html = notify_card.render("briefing", "Brief", body) or ""
    assert "<td>one \u00b7 two</td>" in html
    assert "one | two" not in html


def test_the_weekday_comes_from_the_date_in_the_title():
    """The title already carries the ISO date; the meta line adds what a person needs."""
    card = notify_card.build("briefing", "Brief 2026-10-26", BRIEF)
    assert card is not None
    assert card.meta == "Mon 26 Oct"


def test_a_title_without_a_date_has_no_meta():
    card = notify_card.build("briefing", "Weekly report", BRIEF)
    assert card is not None
    assert card.meta == ""


def test_a_preamble_line_is_not_repeated_inside_the_card():
    body = "Brief 2026-10-26\nOUTPOST  green\nTASKS  one open\nHABITS  ship 1/7"
    html = notify_card.render("briefing", "Brief 2026-10-26", body) or ""
    assert html.count("Brief 2026-10-26") == 1


def test_a_lead_line_survives_when_the_title_does_not_carry_it():
    """The SCHEMA ping leads with which post it is; the title is only the invite."""
    body = (
        "TX tx 009 \u00b7 day 2026-10-26\n"
        "STATUS  waiting for your review\n"
        "CAPTION  The Council refuses to recognise Mondays.\n"
        "APPROVE  python telegram_post.py --approve"
    )
    html = (
        notify_card.render("approval", "SCHEMA channel \u2014 today's post is waiting", body) or ""
    )
    assert "TX tx 009 \u00b7 day 2026-10-26" in html
    assert "<td>Status</td>" in html
    assert "<td>Caption</td>" in html


def test_a_value_that_wraps_keeps_its_second_line():
    """A caption is two lines in the real ping; half a sentence is worse than none."""
    body = (
        "STATUS  waiting for your review\n"
        "CAPTION  NOTICE\n"
        "\n"
        "The Council refuses to recognise Mondays.\n"
        "APPROVE  python telegram_post.py --approve"
    )
    html = notify_card.render("approval", "SCHEMA channel", body) or ""
    assert "NOTICE The Council refuses to recognise Mondays." in html


def test_a_producers_own_markup_is_dropped_not_shown_as_a_tag():
    """The caption arrives as HTML; a literal ``<b>`` inside a table cell reads as a bug."""
    body = (
        "STATUS  waiting\n"
        "CAPTION  <b>NOTICE</b> the Council refuses to recognise Mondays.\n"
        "APPROVE  python telegram_post.py --approve"
    )
    html = notify_card.render("approval", "SCHEMA channel", body) or ""
    assert "<b>NOTICE</b>" not in html
    assert "<td>NOTICE the Council refuses to recognise Mondays.</td>" in html


def test_a_caveat_moves_below_the_table_instead_of_burying_its_row():
    """``Empire`` held a headline and a hedge in one cell; the hedge is the note."""
    body = (
        "OUTPOST  all green (15-min probes)\n"
        "AUTOMATION  20 task(s) ok (+2 by design)\n"
        "EMPIRE  all instruments ok \u00b7 2,000.00 EUR (1 row(s)) outstanding "
        "\u00b7 directionally-positive (keep; not significant at this n)\n"
    )
    html = notify_card.render("briefing", "Brief 2026-10-26", body) or ""
    assert "<td>all instruments ok \u00b7 \u20ac2,000.00 (1 row) outstanding</td>" in html
    assert "directionally-positive" in html
    assert (
        "<td>all instruments ok \u00b7 \u20ac2,000.00 (1 row) outstanding \u00b7 directionally"
        not in html
    )


def test_the_producers_plural_and_currency_read_as_a_person_writes_them():
    assert notify_card._readable("20 task(s) ok") == "20 tasks ok"
    assert notify_card._readable("2,000.00 EUR (1 row(s))") == "\u20ac2,000.00 (1 row)"
    assert notify_card._readable("7 row(s)") == "7 row(s)"


def test_a_brief_carries_buttons_that_lead_to_the_work_behind_it():
    """A report with no way to act on it is a wall of text with a nice table."""
    keyboard = notify_card.keyboard(
        "briefing", "TASKS  56 open \u00b7 19 due \u00b7 0 closed today"
    )
    assert keyboard, "the brief must offer the two places its numbers come from"
    targets = [button["callback_data"] for row in keyboard for button in row]
    assert "cd:tsk:review:review" in targets
    assert "hb:st:7" in targets


def test_the_task_button_says_how_much_work_is_waiting():
    """The count is the decision; the body already carries it."""
    keyboard = (
        notify_card.keyboard("briefing", "TASKS  56 open \u00b7 19 due \u00b7 0 closed today") or []
    )
    labels = [button["text"] for row in keyboard for button in row]
    assert "☑ Tasks · 19 due" in labels


def test_a_brief_without_a_count_still_offers_the_button():
    keyboard = notify_card.keyboard("briefing", "OUTPOST  all green") or []
    labels = [button["text"] for row in keyboard for button in row]
    assert "☑ Tasks" in labels


def test_a_type_with_no_live_handler_gets_no_button():
    """A button that answers nothing is worse than no button."""
    assert notify_card.keyboard("cron_failed") is None
    assert notify_card.keyboard("custom") is None


def test_a_markdown_body_does_not_arrive_with_its_asterisks():
    # A producer that writes for a markdown renderer ("**LE MOTIF:** clean") must not
    # leave the markers on screen: a card draws its own emphasis.
    body = "\n".join(
        [
            "OUTPOST  all green",
            "MAIL  **LE MOTIF:** clean · `appointments/2026-10-26`",
            "HABITS  ship 1/7",
        ]
    )
    card = notify_card.build("briefing", "Brief", body)
    assert card is not None
    html = card.rich_html()
    assert "**" not in html
    assert "`" not in html
    assert "LE MOTIF:" in html
