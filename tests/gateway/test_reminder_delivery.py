"""Reminder delivery settles on VERIFIED delivery — complete only when a channel actually
delivered, otherwise retry (then fail after the budget).

Regression (Finding 1): the non-Telegram fan-out result from the notify router was discarded
and the reminder was completed UNCONDITIONALLY, so a reminder routed to a failing channel
(email SMTP error / adapter-not-enabled / master-off) was silently lost with no retry — the
retry machinery only existed on the Telegram branch. Finding 2: the secondary fan-out ran on
every Telegram retry (duplicate deck/email copies). Finding 3: one raising reminder aborted the
whole batch (head-of-line block).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import navig.notify
import navig.notify.prefs as _prefs
from navig.gateway.channels.telegram import (
    _REMINDER_MAX_RETRIES,
    TelegramChannel,
    _settle_reminder,
)


class _FakeStore:
    def __init__(self):
        self.completed: list[int] = []
        self.failed: list[int] = []
        self.retried: list[tuple[int, int]] = []

    def complete_reminder(self, rid):
        self.completed.append(rid)

    def fail_reminder(self, rid):
        self.failed.append(rid)

    def increment_reminder_retry(self, rid, delay):
        self.retried.append((rid, delay))


class _FakeChannel:
    """Minimal stand-in for TelegramChannel: only `send_message` is used by the method."""

    def __init__(self, send_result=True):
        self._send_result = send_result
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return self._send_result


def _reminder(**over):
    base = dict(
        id=7,
        chat_id=123,
        message="take meds",
        retry_count=0,
        remind_at=datetime.now(timezone.utc).isoformat(),
    )
    base.update(over)
    return base


def _patch_prefs(monkeypatch, *, telegram_on, others):
    monkeypatch.setattr(
        _prefs, "is_enabled", lambda t, c: telegram_on if c == "telegram" else (c in others)
    )
    monkeypatch.setattr(
        _prefs,
        "enabled_channels",
        lambda t: (["telegram"] if telegram_on else []) + list(others),
    )


def _patch_dispatch(monkeypatch, result, counter=None):
    async def _fake(*_a, **_k):
        if counter is not None:
            counter["n"] += 1
        return result

    monkeypatch.setattr(navig.notify, "dispatch", _fake)


async def _deliver(channel, store, reminder):
    # Unbound call so we don't have to construct a full TelegramChannel (bot token / session).
    await TelegramChannel._deliver_one_reminder(channel, store, reminder)


# ── _settle_reminder (pure decision) ─────────────────────────────────────────


async def test_settle_delivered_completes():
    store = _FakeStore()
    _settle_reminder(store, 7, True, 0, 123, "x")
    assert store.completed == [7] and not store.retried and not store.failed


async def test_settle_failed_under_budget_retries():
    store = _FakeStore()
    _settle_reminder(store, 7, False, 0, 123, "x")
    assert store.retried and not store.completed and not store.failed


async def test_settle_failed_at_budget_fails():
    store = _FakeStore()
    _settle_reminder(store, 7, False, _REMINDER_MAX_RETRIES, 123, "x")
    assert store.failed == [7] and not store.completed and not store.retried


# ── non-Telegram delivery is VERIFIED before completion (the core fix) ────────


async def test_nontelegram_delivery_success_completes(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=False, others=["email"])
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "email", "ok": True, "detail": "sent"}]})
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder())
    assert store.completed == [7] and not store.retried


async def test_nontelegram_delivery_failure_retries_not_completes(monkeypatch):
    # THE BUG: a failed non-Telegram delivery used to be marked complete → silent loss.
    _patch_prefs(monkeypatch, telegram_on=False, others=["email"])
    _patch_dispatch(
        monkeypatch, {"channels": [{"channel": "email", "ok": False, "detail": "smtp error"}]}
    )
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder())
    assert store.retried and not store.completed  # retried, NOT silently completed


async def test_nontelegram_failure_at_budget_fails(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=False, others=["email"])
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "email", "ok": False, "detail": "x"}]})
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder(retry_count=_REMINDER_MAX_RETRIES))
    assert store.failed == [7] and not store.completed


async def test_nothing_attempted_completes_without_churn(monkeypatch):
    # master-off / quiet-hours → dispatch returns an empty channel list → complete, no retry churn.
    _patch_prefs(monkeypatch, telegram_on=False, others=["email"])
    _patch_dispatch(monkeypatch, {"skipped": "master_off", "channels": []})
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder())
    assert store.completed == [7] and not store.retried


async def test_no_reminder_channels_at_all_completes(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=False, others=[])
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder())
    assert store.completed == [7] and not store.retried


# ── Telegram-primary path + no duplicate secondary fan-out on retry (Finding 2) ──


async def test_telegram_send_success_completes_and_fans_out_once(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=["deck"])
    counter = {"n": 0}
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "deck", "ok": True}]}, counter)
    store = _FakeStore()
    ch = _FakeChannel(send_result={"message_id": 1})
    await _deliver(ch, store, _reminder(retry_count=0))
    assert store.completed == [7] and ch.sent
    assert counter["n"] == 1  # secondary fan-out fired on the first attempt


async def test_telegram_retry_does_not_refanout_secondary(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=["deck"])
    counter = {"n": 0}
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "deck", "ok": True}]}, counter)
    store = _FakeStore()
    ch = _FakeChannel(send_result={"message_id": 1})
    await _deliver(ch, store, _reminder(retry_count=1))  # a Telegram retry
    assert counter["n"] == 0  # NO duplicate deck copy on a retry
    assert store.completed == [7]


async def test_telegram_send_failure_retries(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=[])
    store = _FakeStore()
    ch = _FakeChannel(send_result=None)  # Telegram send failed
    await _deliver(ch, store, _reminder(retry_count=0))
    assert store.retried and not store.completed


# ── malformed + stale rows ───────────────────────────────────────────────────


async def test_malformed_row_completed():
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder(message=""))
    assert store.completed == [7]


async def test_stale_reminder_failed():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    store = _FakeStore()
    await _deliver(_FakeChannel(), store, _reminder(remind_at=old))
    assert store.failed == [7] and not store.completed


# ── chat-less reminders (deck-app / chat_id=0) deliver via notify, not dropped ──
# A deck-app reminder is created with chat_id=0; the malformed check used to treat 0 as a bad
# row and complete_reminder() it BEFORE the fan-out — so it delivered nowhere. It must instead
# deliver via the notify channels (deck feed / email), and NEVER attempt a Telegram send.


async def test_chatless_reminder_delivers_via_notify_not_dropped(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=["deck"])  # matrix has telegram, but no chat
    counter = {"n": 0}
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "deck", "ok": True}]}, counter)
    store = _FakeStore()
    ch = _FakeChannel()
    await _deliver(ch, store, _reminder(chat_id=0))
    assert counter["n"] == 1  # delivered via notify — pre-fix it was dropped, never dispatched
    assert store.completed == [7]
    assert ch.sent == []  # NO Telegram send attempted (no chat to send to)


async def test_chatless_reminder_delivery_failure_retries(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=["deck"])
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "deck", "ok": False, "detail": "x"}]})
    store = _FakeStore()
    ch = _FakeChannel()
    await _deliver(ch, store, _reminder(chat_id=0))
    assert store.retried and not store.completed  # pre-fix: completed (dropped), never retried
    assert ch.sent == []


async def test_chatless_reminder_with_no_other_channels_completes(monkeypatch):
    # Only Telegram enabled but no chat → nothing to deliver to → complete without churn.
    _patch_prefs(monkeypatch, telegram_on=True, others=[])
    store = _FakeStore()
    ch = _FakeChannel()
    await _deliver(ch, store, _reminder(chat_id=0))
    assert store.completed == [7] and not store.retried
    assert ch.sent == []


async def test_none_chat_reminder_also_delivers_via_notify(monkeypatch):
    _patch_prefs(monkeypatch, telegram_on=True, others=["deck"])
    counter = {"n": 0}
    _patch_dispatch(monkeypatch, {"channels": [{"channel": "deck", "ok": True}]}, counter)
    store = _FakeStore()
    ch = _FakeChannel()
    await _deliver(ch, store, _reminder(chat_id=None))
    assert counter["n"] == 1 and store.completed == [7] and ch.sent == []
