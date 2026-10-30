"""`/menu` — the hub is only real if the operator can get to it.

The menu card could open every surface in place, but nothing on the phone pointed at it:
no slash command, no registry entry. `/habits` and `/status` existed and the hub did not,
which meant the one card built for navigation was reachable only by scrolling back to a
reminders empty state. These pin the way in.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from navig.gateway.channels import telegram_commands as tc
from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
from navig.telegram import menu_card
from navig.telegram.card_doc import Card

CHAT = 7
USER = 7

# What the live dispatcher is able to pass to a handler.
DISPATCHER_KWARGS = {"chat_id", "user_id", "username", "metadata", "is_group", "text", "session"}


def _entry(command: str):
    return next((e for e in tc._SLASH_REGISTRY if e.command == command), None)


class _Channel:
    """Just enough channel: a rich send that succeeds, and nothing else."""

    def __init__(self, *, rich: bool = True) -> None:
        self.rich = rich
        self.rich_sent: list[dict[str, Any]] = []
        self.classic_sent: list[dict[str, Any]] = []

    async def send_rich_message(self, chat_id: int, **kwargs: Any) -> dict[str, Any] | None:
        if not self.rich:
            return None
        self.rich_sent.append({"chat": chat_id, **kwargs})
        return {"message_id": 11}

    async def send_command_output(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.classic_sent.append({"chat": chat_id, "text": text, **kwargs})


@pytest.fixture
def two_surfaces(monkeypatch):
    surfaces = {
        "tasks": ("📋 Tasks", lambda user_id, now: (Card("Tasks"), [])),
        "agenda": ("🗓 Agenda", lambda user_id, now: (Card("Agenda"), [])),
    }
    monkeypatch.setattr(menu_card, "_SURFACES", dict(surfaces))
    monkeypatch.setattr(menu_card, "_ORDER", ("tasks", "agenda"))
    monkeypatch.setattr(menu_card, "_counts", lambda user_id, now: [])
    return surfaces


def test_the_menu_command_is_registered() -> None:
    entry = _entry("menu")

    assert entry is not None, "/menu has no _SLASH_REGISTRY entry — it cannot be dispatched"
    assert entry.handler == "_handle_menu"
    assert entry.usage == "/menu"


def test_the_handler_lives_where_the_dispatcher_looks() -> None:
    assert callable(getattr(TelegramCommandsMixin, "_handle_menu", None))


def test_every_parameter_can_actually_be_supplied() -> None:
    sig = inspect.signature(TelegramCommandsMixin._handle_menu)
    for name, param in sig.parameters.items():
        if name == "self" or param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if param.default is inspect.Parameter.empty:
            assert name in DISPATCHER_KWARGS, (
                f"{name} is required and the dispatcher never fills it"
            )


def test_the_command_sends_the_card_rich(two_surfaces) -> None:
    channel = _Channel()

    asyncio.run(TelegramCommandsMixin._handle_menu(channel, CHAT, USER))

    (rich,) = channel.rich_sent
    assert "NAVIG" in rich["html"]
    assert rich["fallback_html"]  # a classic projection always rides along
    datas = [b["callback_data"] for row in rich["keyboard"] for b in row]
    assert datas == ["hm:open:tasks", "hm:open:agenda", "hm:tour:0", "hm:home"]
    assert channel.classic_sent == []


def test_without_rich_messages_the_card_still_arrives(two_surfaces) -> None:
    channel = _Channel(rich=False)

    asyncio.run(TelegramCommandsMixin._handle_menu(channel, CHAT, USER))

    (classic,) = channel.classic_sent
    assert "NAVIG" in classic["text"]
    assert classic["parse_mode"] == "HTML"
    assert classic["keyboard"][0][0]["callback_data"] == "hm:open:tasks"
