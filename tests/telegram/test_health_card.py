"""How ``/health`` reads as a card: a sheet, a sparkline, a callout, three actions.

The numbers come from ``body_metrics.summarize``; these tests pass that shape in
directly, so they describe the projection without a body record on disk.
"""

from __future__ import annotations

from navig.telegram import health_card as hc

SUMMARY = {
    "field": "weight_kg",
    "days": 30,
    "latest": 87.4,
    "latest_date": "2026-10-26",
    "average": 87.1,
    "trend": 0.3,
    "recorded": 18,
    "sparkline": "▂▃▄▅▆▇█",
    "points": [],
}


def _rich(**overrides):
    summary = dict(SUMMARY, **overrides) if overrides.pop("summary", True) else None
    classic, rich, keyboard = hc.build(summary, habits=3, unit="kg", no_prior="no prior week")
    return classic, rich, keyboard


def test_the_numbers_are_a_sheet_not_column_padding():
    _, rich, _ = _rich()
    assert "<table" in rich
    assert "<td>Latest</td><td><b>87.4 kg</b>" in rich
    assert "7-day average" in rich and "<b>87.1 kg</b> (+0.3 kg)" in rich
    assert "<b>18/30</b> days" in rich
    assert "<td>Active habits</td><td><b>3</b></td>" in rich


def test_the_sparkline_stays_preformatted():
    _, rich, _ = _rich()
    assert "<pre><code>▂▃▄▅▆▇█</code></pre>" in rich


def test_a_rate_worth_naming_is_a_callout():
    classic, rich, _ = hc.build(
        SUMMARY,
        habits=3,
        warning="Losing faster than 1 kg/week — worth a word with your doctor.",
    )
    assert "<aside>Losing faster than 1 kg/week" in rich
    assert "<blockquote>Losing faster than 1 kg/week" in classic


def test_an_unreadable_record_is_a_warning_never_a_zero():
    classic, rich, keyboard = hc.build(None, habits=None)
    assert "Could not read the body record" in rich
    assert "Unknown is not zero" in rich
    assert "<table" not in rich  # nothing to tabulate, so nothing claims a number
    assert "<blockquote>" in classic
    # Habits could not be counted either, so that button is not offered.
    assert "slash:habits" not in str(keyboard)


def test_the_first_weighing_offers_the_one_next_step():
    classic, rich, _ = hc.build(dict(SUMMARY, latest=None, latest_date=None), habits=2)
    assert "No weight recorded yet" in rich
    assert "<td>Latest</td>" not in rich  # no measurement is invented
    assert "<td>Active habits</td><td><b>2</b></td>" in rich  # the streak still counts
    assert "No weight recorded yet" in classic


def test_a_missing_average_omits_the_row_instead_of_inventing_one():
    _, rich, _ = hc.build(dict(SUMMARY, average=None, trend=None), habits=3)
    assert "7-day average" not in rich
    assert "<td>Latest</td>" in rich  # the rest of the sheet still reads


def test_the_actions_are_inside_the_card_and_coloured():
    _, rich, keyboard = _rich()
    assert '<tg-button-row align="left">' in rich
    assert 'style="primary">⚖️ Weigh in' in rich
    assert '<tg-button type="callback_data" data="slash:body">📊 Weekly' in rich
    assert keyboard == [
        [{"text": "⚖️ Weigh in", "callback_data": "slash:weigh", "style": "primary"}],
        [
            {"text": "📊 Weekly", "callback_data": "slash:body"},
            {"text": "💪 Habits", "callback_data": "slash:habits"},
        ],
    ]


def test_the_classic_projection_carries_no_rich_only_tag():
    classic, _, _ = _rich()
    for tag in ("<table", "<aside", "<tg-button-row", "<h2", "<footer"):
        assert tag not in classic
    assert "🏥 <b>Health</b>" in classic


def test_the_banner_rides_the_meta_line_as_plain_text():
    _, rich, _ = hc.build(SUMMARY, habits=3, banner="<i>Extension off</i>")
    assert rich.startswith("<h2>🏥 Health</h2>\n<p><i>Extension off</i></p>")
