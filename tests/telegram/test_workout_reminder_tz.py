"""`/workout HH:MM` one-time reminder must store remind_at in UTC, not naive local.

The reminder poller compares remind_at lexicographically against a UTC-canonical now
(RuntimeStore `_utcnow`), so a naive-local remind_at fired off by the server's UTC offset —
the exact bug the `/remindme at HH:MM` sibling already avoids via `.astimezone(timezone.utc)`.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from navig.gateway.channels.telegram_commands import TelegramCommandsMixin


class _FakeStore:
    def __init__(self):
        self.created: list[dict] = []

    def create_reminder(self, *, user_id, chat_id, message, remind_at):
        self.created.append(
            dict(user_id=user_id, chat_id=chat_id, message=message, remind_at=remind_at)
        )
        return 42


class _FakeSelf:
    def __init__(self):
        self.send_message = AsyncMock()


async def _run_workout(monkeypatch, arg):
    import navig.store.runtime as rt

    store = _FakeStore()
    monkeypatch.setattr(rt, "get_runtime_store", lambda: store)
    me = _FakeSelf()
    await TelegramCommandsMixin._handle_workout(me, chat_id=100, user_id=200, text=f"/workout {arg}")
    return store, me


def _sent_text(me):
    # send_message(chat_id, text, parse_mode=...)
    return me.send_message.await_args.args[1]


async def test_workout_hhmm_stores_utc(monkeypatch):
    store, _me = await _run_workout(monkeypatch, "07:30")
    assert len(store.created) == 1
    remind_at = store.created[0]["remind_at"]
    # Stored as an AWARE UTC datetime (pre-fix it was a NAIVE local datetime → mis-sorted).
    assert remind_at.tzinfo is not None, "remind_at must be timezone-aware, not naive local"
    assert remind_at.utcoffset() == timedelta(0), "remind_at must be stored in UTC"
    # And it round-trips to the LOCAL time the user entered.
    assert remind_at.astimezone().strftime("%H:%M") == "07:30"


async def test_workout_hhmm_confirmation_shows_local_time(monkeypatch):
    _store, me = await _run_workout(monkeypatch, "07:30")
    # The confirmation shows the LOCAL time the user typed, not the UTC-shifted time.
    assert "07:30" in _sent_text(me)


async def test_workout_invalid_time_is_rejected_not_crashed(monkeypatch):
    # Pre-fix, "25:99" reached `now.replace(hour=25)` → ValueError (uncaught). Now it's
    # validated like the /remindme sibling.
    store, me = await _run_workout(monkeypatch, "25:99")
    assert store.created == []  # no reminder created
    assert "24h" in _sent_text(me)
