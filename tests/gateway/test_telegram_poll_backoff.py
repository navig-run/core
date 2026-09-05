"""`TelegramChannel._poll_updates` must back off when getUpdates fails, not hot-spin.

`_api_call` swallows every failure and returns None — a fast 409 Conflict / 401 / connection
refused returns in milliseconds, NOT after the 30s long-poll. The loop used to re-issue
getUpdates immediately on that None → 100% CPU hot-spin + one ERROR log per iteration. It now
sleeps with the TELEGRAM_POLLING backoff on the None path and resets on the next good poll.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels import telegram as tg

pytestmark = pytest.mark.unit


class _FakeChannel:
    def __init__(self, results):
        self._running = True
        self._last_update_id = 0
        self._last_event_at = 0.0
        self._results = list(results)
        self.api_calls = 0

    async def _api_call(self, method, params):
        self.api_calls += 1
        return self._results.pop(0) if self._results else None

    async def _process_update(self, update):
        self._last_update_id = update["update_id"]


async def test_poll_updates_backs_off_on_none_instead_of_hot_spinning(monkeypatch):
    ch = _FakeChannel(results=[None, None, None])  # every getUpdates fails fast
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 3:
            ch._running = False  # let it back off a few times, then break

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await asyncio.wait_for(tg.TelegramChannel._poll_updates(ch), timeout=5.0)

    # The loop paused (backed off) on the None path rather than re-issuing getUpdates in a
    # tight 100%-CPU loop — every recorded sleep is a real, positive backoff.
    assert len(sleeps) >= 1
    assert all(s > 0 for s in sleeps)
    # One getUpdates per iteration — never many calls between backoffs (the hot-spin signature).
    assert ch.api_calls <= len(sleeps) + 1


async def test_poll_updates_resets_backoff_after_a_good_poll(monkeypatch):
    # fail, fail, then an empty-but-successful poll ([]), then fail again.
    ch = _FakeChannel(results=[None, None, [], None])
    attempts_seen: list[float] = []

    async def fake_sleep(secs):
        attempts_seen.append(secs)
        if len(attempts_seen) >= 3:
            ch._running = False

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    await asyncio.wait_for(tg.TelegramChannel._poll_updates(ch), timeout=5.0)

    # The 3rd backoff (after the good [] poll reset fail_attempts to 0) must be the SAME small
    # initial delay as the 1st — i.e. the good poll reset the escalation, it didn't keep growing.
    assert attempts_seen[0] == pytest.approx(attempts_seen[2], rel=0.5)
    # A successful poll stamps liveness for the health monitor.
    assert ch._last_event_at > 0.0
