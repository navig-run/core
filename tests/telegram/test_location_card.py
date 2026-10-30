"""Tests for the shared-location card."""

from __future__ import annotations

from navig.telegram import location_card

CARD = dict(lat=48.858370, long=2.294481, accuracy=12, zoom=16)


def _callbacks(card) -> list[str]:
    return [b["callback_data"] for row in (card.keyboard() or []) for b in row if b.get("callback_data")]


def test_the_pin_renders_a_map_node_in_the_rich_body() -> None:
    html = location_card.location_card(**CARD).rich_html()
    assert "<h2>📍 Shared location</h2>" in html
    assert '<tg-map lat="48.85837" long="2.294481" zoom="16"/>' in html
    assert "<figcaption>" in html


def test_the_sheet_states_six_decimals_and_the_accuracy() -> None:
    html = location_card.location_card(**CARD).rich_html()
    assert "<code>48.858370</code>" in html
    assert "<code>2.294481</code>" in html
    assert "12 m" in html


def test_a_live_location_says_how_long_it_is_live() -> None:
    html = location_card.location_card(**{**CARD, "live_period": 3600}).rich_html()
    assert "Live for" in html
    assert "60 min" in html


def test_a_venue_carries_its_name_and_address() -> None:
    card = location_card.location_card(
        **CARD, venue_title="Café de Flore", venue_address="172 Bd Saint-Germain"
    )
    html = card.rich_html()
    assert "<i>Café de Flore</i>" in html  # the meta line names the place
    assert "<td>Place</td><td>Café de Flore</td>" in html
    assert "172 Bd Saint-Germain" in html


def test_the_two_buttons_work_for_anyone_and_open_a_map() -> None:
    card = location_card.location_card(**CARD)
    rows = card.keyboard()
    assert rows[0][0]["url"].startswith("https://www.openstreetmap.org/?mlat=48.85837")
    assert rows[1][0]["copy_text"]["text"] == "48.858370, 2.294481"
    assert _callbacks(card) == []  # no callback buttons on a location card


def test_a_message_without_coordinates_says_so_instead_of_drawing_a_map() -> None:
    card = location_card.location_card()
    html = card.rich_html()
    assert "No coordinates" in html
    assert "<tg-map" not in html
    assert card.keyboard() is None


def test_the_classic_projection_links_out_and_carries_no_rich_tags() -> None:
    classic = location_card.location_card(**CARD).classic_html()
    assert "openstreetmap.org" in classic
    for tag in ("<h2", "<table", "<tg-map", "<figure", "<footer"):
        assert tag not in classic, (tag, classic[:160])


def test_the_accuracy_prefers_horizontal_when_both_are_present() -> None:
    html = location_card.location_card(
        **{**CARD, "horizontal_accuracy": 30, "accuracy": 12}
    ).rich_html()
    assert "30 m" in html
    assert "12 m" not in html
