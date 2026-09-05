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

import re
from datetime import datetime, timedelta, timezone

from navig.store.base import _to_utc_iso, _utcnow
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


# ─── _to_utc_iso: naive datetimes are stored in UTC, not as-if-UTC ──────────────
# create_reminder used remind_at.isoformat() → a naive local wall-clock time (e.g. the habit
# reminder's datetime.now(), the /workout HH:MM path before #632) was stored as-if-UTC and,
# compared lexicographically against a UTC now, fired off by the server's UTC offset. It also
# wrote aware-UTC as '…+00:00' rather than the canonical '…Z' the rest of the lifecycle uses.


class TestToUtcIso:
    def test_aware_utc_becomes_canonical_z(self):
        assert (
            _to_utc_iso(datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc))
            == "2026-07-27T13:00:00.000000Z"
        )

    def test_aware_offset_is_converted_to_utc(self):
        tz = timezone(timedelta(hours=5, minutes=30))
        assert (
            _to_utc_iso(datetime(2026, 7, 27, 13, 0, tzinfo=tz)) == "2026-07-27T07:30:00.000000Z"
        )

    def test_naive_treated_as_local_and_converted(self):
        naive = datetime(2026, 7, 27, 13, 0)
        # Same conversion the code performs — tz-independent (correct on any machine's tz).
        expected = naive.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        assert _to_utc_iso(naive) == expected

    def test_shape_matches_utcnow(self):
        # Must be byte-compatible with _utcnow so plain string compares stay homogeneous.
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", _utcnow())
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
            _to_utc_iso(datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)),
        )


class TestReminderTimezoneNormalization:
    def test_naive_local_future_is_stored_as_correct_utc(self, tmp_path):
        # THE FIX: a naive local time is converted to UTC + written canonically ('…Z').
        # Pre-fix the stored value was `naive.isoformat()` ("2030-06-01T15:30:00") — wrong
        # instant AND wrong shape (no offset, no Z) — so this fails pre-fix on ANY machine.
        store = RuntimeStore(tmp_path / "runtime.db")
        naive = datetime(2030, 6, 1, 15, 30, 0)
        rid = store.create_reminder(1, 1, "x", naive)
        stored = store.get_user_reminders(1)[0]["remind_at"]
        assert stored == naive.astimezone().astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        assert stored.endswith("Z")
        # And it's not due now (it's in the future in every timezone).
        assert [r["id"] for r in store.get_user_reminders(1)] == [rid]
        assert store.get_due_reminders() == []

    def test_naive_now_is_due_immediately(self, tmp_path):
        # The habit-reminder case: a cron fires and queues create_reminder(remind_at=now()).
        # It must be due right away, not offset hours into the future.
        store = RuntimeStore(tmp_path / "runtime.db")
        rid = store.create_reminder(1, 1, "workout", datetime.now())  # naive LOCAL now
        assert [r["id"] for r in store.get_due_reminders()] == [rid]

    def test_aware_utc_stored_canonically_not_plus_offset(self, tmp_path):
        # Pre-fix an aware-UTC datetime was stored '…+00:00' (isoformat), not '…Z'.
        store = RuntimeStore(tmp_path / "runtime.db")
        store.create_reminder(1, 1, "x", datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc))
        stored = store.get_user_reminders(1)[0]["remind_at"]
        assert stored.endswith("Z") and "+" not in stored

    def test_naive_and_equivalent_aware_store_identically(self, tmp_path):
        # A naive-local time and the SAME instant expressed as aware-UTC store the same string.
        store = RuntimeStore(tmp_path / "runtime.db")
        naive = datetime(2030, 6, 1, 15, 0)
        aware = naive.astimezone().astimezone(timezone.utc)
        store.create_reminder(1, 1, "a", naive)
        store.create_reminder(2, 2, "b", aware)
        assert (
            store.get_user_reminders(1)[0]["remind_at"]
            == store.get_user_reminders(2)[0]["remind_at"]
        )
