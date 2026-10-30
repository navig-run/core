"""The bot's ``setMyCommands`` payload must stay inside Telegram's 100-entry cap.

Telegram rejects a command list above 100 entries with
``400 Bad Request: BOT_COMMANDS_TOO_MUCH`` — the WHOLE payload, not the excess — so a
surface that grows past the cap leaves the bot serving an OLD menu and logs a 400 on
every boot (outpost, 2026-10-28: the registry sat at exactly 100, one command short of
the break). ``_register_commands`` clamps to :data:`MAX_BOT_COMMANDS` and names what it
left out; the deck command is pinned because ``navig miniapp deploy`` owns that button.

Runs on a REAL ``TelegramChannel``: its mixin methods are delegated with ``self`` = the
channel, which does not inherit the mixin at runtime.
"""

from __future__ import annotations

import logging

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_commands import MAX_BOT_COMMANDS, TelegramCommandsMixin


def _channel() -> TelegramChannel:
    return TelegramChannel.__new__(TelegramChannel)  # no network, no config reads


def _many(count: int) -> list[dict[str, str]]:
    return [{"command": f"cmd{i:03d}", "description": f"Command {i}"} for i in range(count)]


def _capture(ch: TelegramChannel) -> list[dict]:
    """Point the channel at a recorder and return the list of ``setMyCommands`` payloads."""

    payloads: list[dict] = []

    async def _api_call(method, payload=None):
        if method == "setMyCommands":
            payloads.append(payload or {})
        return {"ok": True}

    ch._api_call = _api_call
    return payloads


@pytest.mark.asyncio
async def test_the_registration_payload_never_exceeds_the_telegram_cap(monkeypatch, caplog):
    ch = _channel()
    ch._get_deck_url = lambda: "https://deck.example.dev/connect?key=K"
    monkeypatch.setattr(
        TelegramCommandsMixin,
        "_build_command_list_for_registration",
        staticmethod(lambda: _many(MAX_BOT_COMMANDS + 7)),
    )
    payloads = _capture(ch)

    with caplog.at_level(logging.WARNING):
        await ch._register_commands()

    assert payloads, "the command list is still registered"
    assert len(payloads[0]["commands"]) <= MAX_BOT_COMMANDS
    assert "at most" in caplog.text, "the commands left out are named in the log"


@pytest.mark.asyncio
async def test_the_clamp_keeps_the_deck_command(monkeypatch):
    """``navig miniapp deploy`` owns the menu button; dropping /deck from the popup
    would hide the only entry point to a keyed deck."""
    ch = _channel()
    ch._get_deck_url = lambda: "https://deck.example.dev/connect?key=K"
    monkeypatch.setattr(
        TelegramCommandsMixin,
        "_build_command_list_for_registration",
        staticmethod(lambda: _many(MAX_BOT_COMMANDS + 1)),
    )
    payloads = _capture(ch)

    await ch._register_commands()

    commands = payloads[0]["commands"]
    assert len(commands) <= MAX_BOT_COMMANDS
    assert any(c.get("command") == "deck" for c in commands)


@pytest.mark.asyncio
async def test_a_list_inside_the_cap_is_registered_unchanged(monkeypatch):
    ch = _channel()
    ch._get_deck_url = lambda: ""
    monkeypatch.setattr(
        TelegramCommandsMixin,
        "_build_command_list_for_registration",
        staticmethod(lambda: _many(3)),
    )
    payloads = _capture(ch)

    await ch._register_commands()

    assert [c["command"] for c in payloads[0]["commands"]] == ["cmd000", "cmd001", "cmd002"]
