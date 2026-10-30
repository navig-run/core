"""Tests for the plans cards (`/plans` board and the three `/plan` answers)."""

from __future__ import annotations

import pytest

from navig.telegram import plans_card


class _Space:
    def __init__(self, name, *, scope="global", goal="", pct=0.0, updated="—"):
        self.name = name
        self.scope = scope
        self.goal = goal
        self.completion_pct = pct
        self.last_updated = updated


def test_the_board_puts_the_bar_in_its_own_column() -> None:
    card = plans_card.spaces_card(
        spaces=[
            _Space("dev-space", goal="Ship the cards", pct=62.0, updated="11:40"),
            _Space("human-space", pct=0.0),
        ],
        active="dev-space",
    )
    html = card.rich_html()
    assert "<th>Space</th><th>Progress</th><th>Updated</th>" in html
    assert "██████░░░░" in html  # 62% of ten cells
    assert "<b>62%</b>" in html
    assert "▸ <b>dev-space</b>" in html
    assert "<i>Ship the cards</i>" in html
    assert "• " not in html  # the old bullet marker is gone


def test_the_bar_is_clamped_so_a_wild_number_cannot_blow_the_column() -> None:
    assert plans_card.progress_bar(140) == "█" * 10
    assert plans_card.progress_bar(-20) == "░" * 10
    assert plans_card.progress_bar(50, width=4) == "██░░"


def test_only_the_active_space_carries_the_marker() -> None:
    rows = plans_card.space_rows(
        [_Space("a"), _Space("b")], active="b"
    )
    flat = " ".join(str(cell) for row in rows for cell in row)
    assert flat.count("▸") == 1
    assert "▸ <b>b</b>" in flat


def test_a_global_scope_is_not_printed_and_a_real_one_is() -> None:
    rows = plans_card.space_rows([_Space("a", scope="global"), _Space("b", scope="work")])
    flat = " ".join(str(cell) for row in rows for cell in row)
    assert "<code>global</code>" not in flat
    assert "<code>work</code>" in flat


def test_no_spaces_is_a_callout_with_the_way_out() -> None:
    card = plans_card.spaces_card(spaces=[], active="dev-space")
    html = card.rich_html()
    assert "<aside>" in html
    assert "run /intake" in html
    assert "<table" not in html


def test_the_board_keeps_the_shared_navigation_rows() -> None:
    card = plans_card.spaces_card(
        spaces=[_Space("a")], active="a", buttons=plans_card.nav_rows(message_id=True)
    )
    callbacks = [b["callback_data"] for row in card.keyboard() for b in row]
    assert callbacks == ["nav:open:intake", "nav:cmd:/plans", "nav:back", "nav:home"]


def test_the_usage_card_renders_its_examples_as_an_ordered_list() -> None:
    html = plans_card.goal_usage_card().rich_html()
    assert "<h2>📝 Add a Plan Goal</h2>" in html
    assert '<code class="language-text">/plan &lt;goal text&gt;</code>' in html
    assert "<ol>" in html
    assert html.count("<li>") == len(plans_card.EXAMPLES)
    assert "nothing is written twice" in html


def test_the_added_card_is_a_space_and_goal_sheet() -> None:
    card = plans_card.goal_added_card(
        space="dev-space",
        goal="Launch MVP",
        buttons=[[("📋 View Plans", "nav:cmd:/plans", "primary")]],
    )
    html = card.rich_html()
    assert "<h2>✅ Goal added</h2>" in html
    assert "<td>Space</td><td><code>dev-space</code></td>" in html
    assert "<b>Launch MVP</b>" in html
    assert card.keyboard()[0][0]["callback_data"] == "nav:cmd:/plans"


def test_the_duplicate_card_says_nothing_was_written() -> None:
    card = plans_card.goal_duplicate_card(goal="Launch MVP")
    html = card.rich_html()
    assert "<h2>⚠️ Duplicate goal</h2>" in html
    assert "already in your plan" in html
    assert "<code>Launch MVP</code>" in html


def test_no_plan_card_ships_rich_tags_in_its_classic_projection() -> None:
    for card in (
        plans_card.spaces_card(spaces=[_Space("a", pct=40)], active="a"),
        plans_card.goal_usage_card(),
        plans_card.goal_added_card(space="a", goal="g"),
        plans_card.goal_duplicate_card(goal="g"),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<ol", "<aside", "<br/>", "<footer"):
            assert tag not in classic, (tag, classic[:160])


@pytest.mark.asyncio
async def test_plans_handler_edits_the_pressed_message(monkeypatch) -> None:
    import navig.commands.plans as plans_mod
    import navig.commands.space as space_mod
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    recorded: dict[str, list] = {"sends": [], "edits": []}

    async def fake_send(channel, chat_id, card, **kw):
        recorded["sends"].append(card)
        return {"message_id": 1}

    async def fake_edit(channel, chat_id, message_id, card, **kw):
        recorded["edits"].append((message_id, card))
        return True

    monkeypatch.setattr(card_transport, "send_card", fake_send)
    monkeypatch.setattr(card_transport, "edit_card", fake_edit)
    monkeypatch.setattr(
        plans_mod, "collect_spaces_progress", lambda: [_Space("dev-space", pct=30.0)]
    )
    monkeypatch.setattr(space_mod, "get_active_space", lambda: "dev-space")

    await TelegramCommandsMixin._handle_plans_cmd(object(), 1, 2, "/plans", message_id=44)

    assert recorded["sends"] == []
    assert recorded["edits"][0][0] == 44
    assert "Spaces Progress" in recorded["edits"][0][1].rich_html()


@pytest.mark.asyncio
async def test_plan_without_an_argument_sends_the_usage_card(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    recorded: list = []

    async def fake_send(channel, chat_id, card, **kw):
        recorded.append(card)
        return {"message_id": 2}

    monkeypatch.setattr(card_transport, "send_card", fake_send)

    await TelegramCommandsMixin._handle_plan_cmd(object(), 1, 2, "/plan")

    assert len(recorded) == 1
    assert "Add a Plan Goal" in recorded[0].rich_html()
