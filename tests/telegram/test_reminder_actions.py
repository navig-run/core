"""The two buttons on a reminder ping actually do something.

A ping used to be a statement: reading it told you a reminder had arrived and left you
to open the app to do anything about it. ``rem:done`` closes the row, ``rem:snooze``
pushes it an hour out, and both redraw the message the button lives on so the chat
holds the record instead of a card whose buttons no longer mean anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler
from navig.store.runtime import get_runtime_store

CHAT = 4242
USER = 4242


class _Channel:
    """A channel that remembers what it was asked to draw and says it worked."""

    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []
        self.sends: list[dict[str, Any]] = []
        self.answers: list[str] = []

    async def edit_rich_message(self, chat_id, message_id, **kwargs):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, **kwargs})
        return {"message_id": message_id}

    async def send_message(self, chat_id, text, **kwargs):
        self.sends.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": 99}

    async def _api_call(self, method: str, payload: dict[str, Any]):
        if method == "answerCallbackQuery":
            self.answers.append(str(payload.get("text") or ""))
        return {"ok": True}


def _reminder(message: str = "Dentist — 1 day before (Mon 26 Oct · 14:00)") -> int:
    store = get_runtime_store()
    return store.create_reminder(
        USER, CHAT, message, datetime.now(timezone.utc) + timedelta(minutes=5)
    )


async def _press(data: str, channel: _Channel) -> None:
    handler = CallbackHandler(channel)
    await handler._handle_reminder_callback("cb1", data, CHAT, 555, USER)


@pytest.mark.asyncio
async def test_done_closes_the_row_and_redraws_the_ping_in_place() -> None:
    reminder_id = _reminder()
    channel = _Channel()

    await _press(f"rem:done:{reminder_id}", channel)

    row = get_runtime_store().get_reminder(reminder_id)
    assert int(row["completed"]) == 1
    assert len(channel.edits) == 1
    edit = channel.edits[0]
    assert edit["message_id"] == 555  # the message the button lives on
    assert "✅ done" in edit["html"]
    assert "callback_data" not in str(edit["keyboard"])
    assert channel.answers == ["✅ Done"]


@pytest.mark.asyncio
async def test_snooze_pushes_the_same_row_an_hour_out() -> None:
    reminder_id = _reminder()
    store = get_runtime_store()
    before = datetime.now(timezone.utc)
    channel = _Channel()

    await _press(f"rem:snooze:{reminder_id}", channel)

    row = store.get_reminder(reminder_id)
    assert int(row["completed"]) == 0
    new_at = datetime.fromisoformat(str(row["remind_at"]).rstrip("Z")).replace(tzinfo=timezone.utc)
    assert new_at >= before + timedelta(minutes=59)
    assert channel.edits and "snoozed an hour" in channel.edits[0]["html"]
    assert channel.answers == ["⏰ Back in an hour"]


@pytest.mark.asyncio
async def test_a_second_press_on_a_done_ping_says_so_without_touching_it() -> None:
    reminder_id = _reminder()
    store = get_runtime_store()
    store.complete_reminder(reminder_id)
    channel = _Channel()

    await _press(f"rem:done:{reminder_id}", channel)

    assert channel.edits == []  # nothing left to redraw
    assert channel.answers == ["Already handled"]


@pytest.mark.asyncio
async def test_an_unreadable_id_answers_instead_of_raising() -> None:
    channel = _Channel()

    await _press("rem:done:not-a-number", channel)

    assert channel.edits == [] and channel.answers == ["That reminder is gone"]
