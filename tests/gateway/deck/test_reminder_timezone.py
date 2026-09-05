"""Reminder creation must honor a real UTC offset in the submitted timestamp.

`apps.py` parsed `fire_at` with `fromisoformat(s.rstrip("Z")).replace(tzinfo=utc)`, which
DISCARDS any offset and reinterprets the wall-clock as UTC — so `2026-07-22T18:00:00-04:00`
(= 22:00 UTC) was stored as 18:00 UTC and the reminder fired 4 hours early, silently. Both
reminder-create routes now parse offset-aware, default naive to UTC, and normalize to UTC
(RuntimeStore compares `remind_at` lexicographically against `_utcnow()`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from navig.gateway.deck.routes import apps

UTC = timezone.utc


class _FakeReq:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _CapturingStore:
    def __init__(self):
        self.remind_at = None
        self.chat_id = None
        self.user_id = None

    def create_reminder(self, user_id=0, chat_id=0, message="", remind_at=None):
        self.remind_at = remind_at
        self.chat_id = chat_id
        self.user_id = user_id
        return 1


def _mock_resolve(monkeypatch, value):
    # apps.py lazily imports _resolve_default_user_chat from the schedule route.
    from navig.gateway.deck.routes import schedule

    monkeypatch.setattr(schedule, "_resolve_default_user_chat", lambda: value)


async def _stored_remind_at(monkeypatch, fire_at: str):
    _mock_resolve(monkeypatch, (0, 0))  # isolate from the real telegram.allowed_users config
    store = _CapturingStore()
    monkeypatch.setattr(apps, "_get_runtime_store", lambda: store)
    await apps.handle_deck_apps_reminders_add(_FakeReq({"message": "x", "fire_at": fire_at}))
    return store.remind_at


async def test_negative_offset_converts_to_correct_utc_instant(monkeypatch):
    got = await _stored_remind_at(monkeypatch, "2026-07-22T18:00:00-04:00")
    assert got == datetime(2026, 7, 22, 22, 0, tzinfo=UTC)  # 18:00 -04:00 == 22:00 UTC
    assert got.utcoffset().total_seconds() == 0  # stored as UTC, not an offset


async def test_positive_offset_converts_to_correct_utc_instant(monkeypatch):
    got = await _stored_remind_at(monkeypatch, "2026-07-22T18:00:00+05:30")
    assert got == datetime(2026, 7, 22, 12, 30, tzinfo=UTC)  # 18:00 +05:30 == 12:30 UTC
    assert got.utcoffset().total_seconds() == 0


async def test_z_suffix_is_utc(monkeypatch):
    got = await _stored_remind_at(monkeypatch, "2026-07-22T18:00:00Z")
    assert got == datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
    assert got.utcoffset().total_seconds() == 0


async def test_naive_defaults_to_utc(monkeypatch):
    got = await _stored_remind_at(monkeypatch, "2026-07-22T18:00:00")
    assert got == datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
    assert got.utcoffset().total_seconds() == 0


async def test_invalid_fire_at_is_a_clean_400_not_a_crash(monkeypatch):
    store = _CapturingStore()
    monkeypatch.setattr(apps, "_get_runtime_store", lambda: store)
    resp = await apps.handle_deck_apps_reminders_add(_FakeReq({"message": "x", "fire_at": "not-a-date"}))
    assert resp.status == 400
    assert store.remind_at is None  # nothing stored on a bad timestamp


# ── deck-app reminders push to Telegram (resolve a real chat, not chat_id=0) ─────
# A deck-app reminder is created with a resolved Telegram chat_id so it PUSHES to Telegram
# (not just the deck feed). With no Telegram user configured it falls back to 0 → deck-only
# delivery (#623). user_id stays 0: the deck's get returns all reminders and delete has a
# no-user fallback, so the deck's list/cancel stay consistent.


async def test_add_resolves_telegram_chat_so_it_pushes(monkeypatch):
    _mock_resolve(monkeypatch, (5, 5))  # first allowed Telegram user → chat_id 5
    store = _CapturingStore()
    monkeypatch.setattr(apps, "_get_runtime_store", lambda: store)
    resp = await apps.handle_deck_apps_reminders_add(
        _FakeReq({"message": "x", "fire_at": "2030-01-01T12:00:00Z"})
    )
    assert resp.status == 201
    assert store.chat_id == 5  # real chat → the poller sends to Telegram (was hardcoded 0)
    assert store.user_id == 0  # deck scope preserved


async def test_add_falls_back_to_deck_only_without_a_telegram_user(monkeypatch):
    _mock_resolve(monkeypatch, (0, 0))  # no allowed_users configured
    store = _CapturingStore()
    monkeypatch.setattr(apps, "_get_runtime_store", lambda: store)
    await apps.handle_deck_apps_reminders_add(
        _FakeReq({"message": "x", "fire_at": "2030-01-01T12:00:00Z"})
    )
    assert store.chat_id == 0  # chat-less → delivers via the deck feed (#623), not dropped
