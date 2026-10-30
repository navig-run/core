"""The menu: one card that opens the other cards in the same message.

Before this, every surface was its own message, so getting from the reminders to the
tasks meant scrolling back to an old card. These pin the hub: a button per surface, the
press edits the message it was pressed in, and every surface carries a way back.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from navig.telegram import menu_card
from navig.telegram.card_doc import Card, Para

CHAT = 7
MESSAGE = 99
USER = 7


class _RichChannel:
    """A channel that can edit rich messages, like the live Telegram one."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[dict[str, Any]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        return {"ok": True}

    async def edit_rich_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        html: str | None = None,
        fallback_html: str | None = None,
        keyboard: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.edits.append(
            {
                "chat": chat_id,
                "message": message_id,
                "html": html,
                "fallback": fallback_html,
                "keyboard": keyboard,
            }
        )
        return {"message_id": message_id}


def _fake_surface(title: str = "Fake surface"):
    def builder(user_id: int, now: Any) -> tuple[Card, dict[str, Any]]:
        card = Card(title)
        card.add(Para("a body", style="italic"))
        return card, {"inline_keyboard": [[{"text": "own", "callback_data": "x:y"}]]}

    return builder


@pytest.fixture
def two_surfaces(monkeypatch):
    """Two known surfaces, and no store reads behind the counts."""
    surfaces = {
        "tasks": ("📋 Tasks", _fake_surface("Tasks card")),
        "agenda": ("🗓 Agenda", _fake_surface("Agenda card")),
    }
    monkeypatch.setattr(menu_card, "_SURFACES", dict(surfaces))
    monkeypatch.setattr(menu_card, "_ORDER", ("tasks", "agenda"))
    monkeypatch.setattr(menu_card, "_counts", lambda user_id, now: [])
    return surfaces


def _datas(rows: list[list[dict[str, str]]]) -> list[str]:
    return [button["callback_data"] for row in rows for button in row]


def _labels(rows: list[list[dict[str, str]]]) -> list[str]:
    return [button["text"] for row in rows for button in row]


def test_the_menu_offers_a_button_per_surface_and_a_refresh(two_surfaces) -> None:
    card, rows = menu_card.home_card(USER)

    assert len(rows) == 3  # surfaces, then the tour, then refresh
    assert _datas(rows) == [
        "hm:open:tasks",
        "hm:open:agenda",
        "hm:tour:0",
        "hm:home",
    ]
    assert _labels(rows)[:2] == ["📋 Tasks", "🗓 Agenda"]
    assert _labels(rows)[2] == "🎞 Card tour"
    assert "NAVIG" in card.classic_html()
    assert all(len(data.encode()) <= 64 for data in _datas(rows))


def test_the_counts_are_a_sheet_of_what_waits(two_surfaces, monkeypatch) -> None:
    monkeypatch.setattr(
        menu_card,
        "_counts",
        lambda user_id, now: [("Open tasks", "4"), ("Reminders", "3 · next 26.10 14:00")],
    )

    card, _rows = menu_card.home_card(USER)

    html = card.rich_html()
    assert "<td>Open tasks</td><td>4</td>" in html
    assert "next 26.10 14:00" in html


def test_a_surface_opens_in_the_message_it_was_pressed_in(two_surfaces) -> None:
    channel = _RichChannel()

    toast = asyncio.run(menu_card.handle_callback(channel, "hm:open:tasks", CHAT, MESSAGE, USER))

    assert toast == ""
    assert channel.calls == []  # nothing was re-sent as a new message
    (edit,) = channel.edits
    assert (edit["chat"], edit["message"]) == (CHAT, MESSAGE)
    assert "Tasks card" in edit["html"]
    assert _datas(edit["keyboard"]) == ["x:y", "hm:home", "hm:refresh"]


def test_every_surface_carries_a_way_back(two_surfaces) -> None:
    for key in ("tasks", "agenda"):
        made = menu_card.surface_card(key, USER)
        assert made is not None
        _card, rows = made
        assert rows[-1][0]["callback_data"] == "hm:home"


def test_the_home_button_returns_to_the_menu(two_surfaces) -> None:
    channel = _RichChannel()

    asyncio.run(menu_card.handle_callback(channel, "hm:home", CHAT, MESSAGE, USER))

    (edit,) = channel.edits
    assert "hm:open:tasks" in _datas(edit["keyboard"])
    assert MESSAGE == edit["message"]


def test_a_surface_that_cannot_render_is_an_honest_toast(two_surfaces) -> None:
    def broken(user_id: int, now: Any) -> Any:
        raise RuntimeError("store is down")

    menu_card.register_surface("broken", "💥 Broken", broken)
    channel = _RichChannel()

    toast = asyncio.run(menu_card.handle_callback(channel, "hm:open:broken", CHAT, MESSAGE, USER))

    assert toast  # the operator is told, and the menu is not left half-drawn
    assert channel.edits == []


def test_an_unknown_surface_is_named_as_such(two_surfaces) -> None:
    toast = asyncio.run(
        menu_card.handle_callback(_RichChannel(), "hm:open:nope", CHAT, MESSAGE, USER)
    )

    assert "not available" in toast.lower()


def test_a_channel_without_rich_editing_still_gets_the_card(two_surfaces) -> None:
    class _Plain:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((method, payload))
            return {"ok": True}

    channel = _Plain()

    asyncio.run(menu_card.handle_callback(channel, "hm:open:agenda", CHAT, MESSAGE, USER))

    method, payload = channel.calls[0]
    assert method == "editMessageText"
    assert payload["message_id"] == MESSAGE
    assert "Agenda card" in payload["text"]


# ── the tour: every surface, one press apart, in one message ──


def test_the_tour_walks_the_surfaces_in_menu_order(two_surfaces) -> None:
    first = menu_card.tour_card(0, USER)
    second = menu_card.tour_card(1, USER)

    assert first is not None and second is not None
    assert "Tasks card" in first[0].classic_html()
    assert "Agenda card" in second[0].classic_html()
    assert first[1][0][1]["text"] == "1/2 · 📋 Tasks"
    assert second[1][0][1]["text"] == "2/2 · 🗓 Agenda"


def test_the_tour_position_row_sits_above_the_surfaces_own_buttons(two_surfaces) -> None:
    _card, rows = menu_card.tour_card(0, USER)

    assert [button["text"] for button in rows[0]] == ["◀", "1/2 · 📋 Tasks", "▶"]
    assert rows[0][0]["callback_data"] == "hm:tour:-1"
    assert rows[0][2]["callback_data"] == "hm:tour:1"
    assert rows[1][0]["callback_data"] == "x:y"  # the surface's own button, untouched
    assert rows[-1][0]["callback_data"] == "hm:home"  # and its way back
    assert all(len(button["callback_data"].encode()) <= 64 for row in rows for button in row)


def test_the_tour_wraps_at_both_ends(two_surfaces) -> None:
    # -1 is the last stop, 2 is the first: no press is ever a dead end.
    assert "Agenda card" in menu_card.tour_card(-1, USER)[0].classic_html()
    assert "Tasks card" in menu_card.tour_card(2, USER)[0].classic_html()


def test_a_press_moves_the_tour_inside_the_same_message(two_surfaces) -> None:
    channel = _RichChannel()

    toast = asyncio.run(menu_card.handle_callback(channel, "hm:tour:1", CHAT, MESSAGE, USER))

    assert toast == ""
    assert channel.calls == []  # the tour never posts a second message
    (edit,) = channel.edits
    assert (edit["chat"], edit["message"]) == (CHAT, MESSAGE)
    assert "Agenda card" in edit["html"]
    assert edit["keyboard"][0][1]["text"] == "2/2 · 🗓 Agenda"


def test_a_garbled_tour_index_starts_over_instead_of_failing(two_surfaces) -> None:
    channel = _RichChannel()

    toast = asyncio.run(menu_card.handle_callback(channel, "hm:tour:", CHAT, MESSAGE, USER))

    assert toast == ""
    (edit,) = channel.edits
    assert "Tasks card" in edit["html"]
    assert edit["keyboard"][0][1]["text"] == "1/2 · 📋 Tasks"
