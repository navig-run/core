"""/status and /briefing rendering: the brain's own machine as Host, no deck-only
buttons, no Progression (that's a Deck/OS surface), and a working Reminders button.
"""

from __future__ import annotations

import platform

import pytest

pytestmark = pytest.mark.integration


def _flat(keyboard):
    return [b for row in (keyboard or []) for b in row]


async def test_status_snapshot_render(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    captured: dict = {}

    async def _cap(chat_id, text, **kw):
        captured["text"] = text
        captured["keyboard"] = kw.get("keyboard")
        return {"message_id": 1}

    channel.send_command_output = _cap
    await channel._handle_status(chat_id=1, user_id=2)

    text = captured["text"]
    buttons = _flat(captured["keyboard"])
    labels = " ".join(b["text"] for b in buttons)
    callbacks = [b["callback_data"] for b in buttons]

    # Host is the machine the brain runs on, not the active SSH target.
    assert platform.node() in text
    # Progression belongs to the Deck/OS surface — gone from the session card.
    assert "Progression" not in text
    # Deck-only buttons removed.
    assert "Switch AI" not in labels
    assert "Spaces" not in labels
    assert "Home" not in labels
    # Reminders button now points at the registered command id (was slash:/reminders,
    # a leading-slash typo that matched nothing).
    assert "slash:reminders" in callbacks
    assert "slash:/reminders" not in callbacks


async def test_briefing_names_the_host_and_trims_deck_buttons(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    captured: dict = {}

    async def _send(chat_id, text, **kw):
        captured["text"] = text
        captured["keyboard"] = kw.get("keyboard")
        return {"message_id": 1}

    channel.send_message = _send
    await channel._handle_briefing(chat_id=1, user_id=2, metadata={})

    text = captured["text"]
    labels = " ".join(b["text"] for b in _flat(captured["keyboard"]))

    # The briefing now says which host its telemetry describes.
    assert "Host:" in text
    assert platform.node() in text
    assert "Switch AI" not in labels
    assert "Spaces" not in labels
    assert "Home" not in labels
    # Daemon status is truthful on non-systemd hosts (Windows/macOS): the briefing
    # is served BY the daemon, so it must not claim "status unavailable".
    import shutil

    if shutil.which("systemctl") is None:
        assert "Daemon: <code>running" in text
        assert "status unavailable" not in text


async def test_status_briefing_and_plans_sends_are_tagged_system(monkeypatch):
    """The daily system sweep only removes what was tagged at send time —
    an untagged card stays in the chat forever (incident 2026-10-13)."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.telegram import chat_tidy

    channel = TelegramChannel(bot_token="1:FAKE")
    seen: list[tuple[str, str | None]] = []

    async def _cap(chat_id, text, **kw):
        seen.append(("status", chat_tidy._KIND.get()))
        return {"message_id": 1}

    async def _send(chat_id, text, **kw):
        seen.append((kw.get("command", "send"), chat_tidy._KIND.get()))
        return {"message_id": 1}

    channel.send_command_output = _cap
    channel.send_message = _send

    token = chat_tidy._ROUTINE.set(False)
    kind_token = chat_tidy._KIND.set("notice")
    try:
        await channel._handle_status(chat_id=1, user_id=2)
        await channel._handle_briefing(chat_id=1, user_id=2, metadata={})
        await channel._handle_plans_cmd(chat_id=1, user_id=2)
    finally:
        chat_tidy._KIND.reset(kind_token)
        chat_tidy._ROUTINE.reset(token)

    assert len(seen) == 3, f"expected all three cards to send, got {seen}"
    assert [k for _, k in seen] == ["system", "system", "system"], seen
