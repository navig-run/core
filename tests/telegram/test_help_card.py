"""Tests for the help cards: the hub, a category with neighbours, and one command."""

from __future__ import annotations

import pytest

from navig.telegram import help_card

HOME = {
    "kind": "home",
    "command_count": 132,
    "categories": [
        {"key": "getting_started", "emoji": "🚀", "label": "Getting Started", "count": 12},
        {"key": "monitoring", "emoji": "📊", "label": "Monitoring", "count": 7},
        {"key": "poll", "emoji": "🗳", "label": "Polls", "count": 1},
    ],
    "keywords_label": "✨ Reply keywords",
    "extensions_label": "🔌 Extensions",
    "close_label": "✕ Close",
}

CATEGORY = {
    "kind": "category",
    "emoji": "📊",
    "label": "Monitoring",
    "commands": [
        {"name": "disk", "description": "Disk usage", "callback": "help:d:disk:monitoring"},
        {"name": "kill", "description": "Kill processes", "callback": "help:d:kill:monitoring"},
        {"name": "ports", "description": "", "callback": "help:d:ports:monitoring"},
    ],
    "index": 4,
    "total": 16,
    "prev": "docker",
    "next": "database",
    "categories_label": "🗂 Categories",
    "close_label": "✕ Close",
}

COMMAND = {
    "kind": "command",
    "name": "kill",
    "description": "Kill processes by group",
    "usage": "/kill <group|name> [--force] [--dry-run]",
    "category_label": "Monitoring",
    "back_callback": "help:c:monitoring",
    "categories_label": "🗂 Categories",
    "close_label": "✕ Close",
}


def test_the_hub_counts_what_it_holds() -> None:
    card = help_card.home_card(HOME)
    html = card.rich_html()
    assert "<h2>❓ Help</h2>" in html
    assert "132 commands · 3 categories" in html
    assert "type /help &lt;topic&gt; to search" in html or "/help <topic>" in html
    assert "Reply to any message" in html


def test_the_hub_buttons_are_the_categories_two_per_row() -> None:
    card = help_card.home_card(HOME)
    rows = card.keyboard()
    assert rows[0][0]["callback_data"] == "help:c:getting_started"
    assert rows[1][0]["callback_data"] == "help:c:poll"  # odd category keeps its own row
    assert rows[-2][0]["callback_data"] == "help:t"
    assert rows[-1][0]["callback_data"] == "help:close"


def test_a_category_is_a_table_not_a_bullet_list() -> None:
    card = help_card.category_card(CATEGORY)
    html = card.rich_html()
    assert "<h2>📊 Monitoring</h2>" in html
    assert "<th>Command</th><th>What it does</th>" in html
    assert "<td><code>/disk</code></td><td>Disk usage</td>" in html
    assert "<td><code>/ports</code></td><td>no description yet</td>" in html
    assert "• /disk" not in html  # the old bullet form is gone


def test_the_position_is_in_the_meta_line() -> None:
    html = help_card.category_card(CATEGORY).rich_html()
    assert "3 commands · 4 of 16" in html


def test_the_neighbours_are_presses_that_stay_in_place() -> None:
    rows = help_card.category_card(CATEGORY).keyboard()
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert "help:c:docker" in callbacks
    assert "help:c:database" in callbacks
    assert callbacks.index("help:c:docker") < callbacks.index("help:c:database")


def test_the_first_category_has_no_previous_and_the_last_no_next() -> None:
    first = dict(CATEGORY, index=1, prev="", next="docker")
    rows = help_card.category_card(first).keyboard()
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert "help:c:" not in callbacks
    assert "help:c:docker" in callbacks
    assert "‹ Prev" not in str(rows)


def test_each_command_keeps_its_detail_press() -> None:
    rows = help_card.category_card(CATEGORY).keyboard()
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert "help:d:disk:monitoring" in callbacks
    assert "help:d:kill:monitoring" in callbacks


def test_a_command_card_shows_its_usage_as_a_copyable_block() -> None:
    card = help_card.command_card(COMMAND)
    html = card.rich_html()
    assert "<h2>/kill</h2>" in html
    assert "<i>Monitoring</i>" in html
    assert "Kill processes by group" in html
    assert "/kill &lt;group|name&gt;" in html
    assert "copy_text" in html


def test_a_command_card_can_get_back_to_where_it_came_from() -> None:
    rows = help_card.command_card(COMMAND).keyboard()
    assert rows[1][0]["callback_data"] == "help:c:monitoring"
    assert rows[1][1]["callback_data"] == "help:home"
    assert rows[2][0]["callback_data"] == "help:close"


def test_a_missing_description_is_named_rather_than_blank() -> None:
    card = help_card.command_card(dict(COMMAND, description=""))
    assert "no description yet" in card.rich_html()


@pytest.mark.parametrize(
    "data",
    [None, {}, {"kind": "subcategory"}, {"kind": ""}],
)
def test_an_unknown_screen_builds_no_card(data) -> None:
    assert help_card.card_from(data) is None


def test_no_help_card_ships_rich_tags_in_its_classic_projection() -> None:
    for card in (
        help_card.home_card(HOME),
        help_card.category_card(CATEGORY),
        help_card.command_card(COMMAND),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<aside", "<footer", "<pre><code"):
            assert tag not in classic, (tag, classic[:160])
