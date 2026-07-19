"""Regression tests for RuntimeStore reminder retry/backoff timestamp handling.

The retry path (``increment_reminder_retry``) once wrote ``remind_at`` with SQLite
``datetime('now', …)`` — a space-separated, Z-less string — while every reader compares
``remind_at`` (a plain string compare) against ``_utcnow()``'s ISO 'T'/'Z' shape. At
column 10 the stored space (0x20) sorts before the readers' 'T' (0x54), so a
freshly-rescheduled reminder looked ``<= now`` (due immediately): the backoff was
ignored, retries fired every poll tick, and a transient failure dropped the reminder.
These tests pin down the intended behaviour: a retried reminder is deferred into the
future and stays visible as pending until its new time arrives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from navig.store.runtime import RuntimeStore


def _make_due_reminder(store: RuntimeStore, *, user_id: int = 7, chat_id: int = 7) -> int:
    """Create a reminder whose time is already in the past (i.e. due now)."""
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    return store.create_reminder(user_id, chat_id, "ping", past)


class TestReminderRetryBackoff:
    def test_due_reminder_is_returned(self, tmp_path):
        # Sanity control: a past-due reminder is due (works regardless of the fix).
        store = RuntimeStore(tmp_path / "runtime.db")
        rid = _make_due_reminder(store)
        due = store.get_due_reminders()
        assert [r["id"] for r in due] == [rid]

    def test_retry_defers_reminder_out_of_due(self, tmp_path):
        # After a 60s backoff the reminder must NOT be due yet.
        store = RuntimeStore(tmp_path / "runtime.db")
        _make_due_reminder(store)
        # precondition: it is due before the retry
        assert len(store.get_due_reminders()) == 1
        store.increment_reminder_retry(store.get_due_reminders()[0]["id"], 60)
        assert store.get_due_reminders() == []  # backoff holds; not due for ~60s

    def test_retry_keeps_reminder_in_user_upcoming(self, tmp_path):
        # A retried (future) reminder is still pending, so it stays in the user's list.
        store = RuntimeStore(tmp_path / "runtime.db")
        rid = _make_due_reminder(store, user_id=42)
        store.increment_reminder_retry(rid, 60)
        upcoming = store.get_user_reminders(42)
        assert [r["id"] for r in upcoming] == [rid]

    def test_retry_counted_in_active_stats(self, tmp_path):
        # active_reminders = completed==0 AND remind_at > now; a retried reminder counts.
        store = RuntimeStore(tmp_path / "runtime.db")
        rid = _make_due_reminder(store)
        store.increment_reminder_retry(rid, 60)
        assert store.get_stats_summary()["active_reminders"] == 1

    def test_retry_increments_count(self, tmp_path):
        # The retry counter still advances (behaviour unchanged by the timestamp fix).
        store = RuntimeStore(tmp_path / "runtime.db")
        rid = _make_due_reminder(store)
        store.increment_reminder_retry(rid, 60)
        store.increment_reminder_retry(rid, 60)
        row = store.get_user_reminders(7)[0]
        assert row["retry_count"] == 2
