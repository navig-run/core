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
    def __init__(self, results, webhook_url: str | None = None, *, webhook_unreachable=False):
        self._running = True
        self._last_update_id = 0
        self._last_event_at = 0.0
        self._results = list(results)
        self.api_calls = 0
        self.probes = 0
        # What Telegram would report for getWebhookInfo. A URL means "a
        # webhook is registered, getUpdates is refused"; None means no webhook;
        # `webhook_unreachable` means the probe itself fails (network down).
        self.webhook_url = webhook_url
        self.webhook_unreachable = webhook_unreachable

    async def _api_call(self, method, params):
        self.api_calls += 1
        if method == "getWebhookInfo":
            self.probes += 1
            if self.webhook_unreachable:
                return None
            return {"url": self.webhook_url or ""}
        return self._results.pop(0) if self._results else None

    # The real method, bound to the fake: the probe is what is under test.
    _registered_webhook_url = tg.TelegramChannel._registered_webhook_url

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
    # One getUpdates per iteration plus, on the first failure of a streak and every
    # third after, one getWebhookInfo probe — never MANY calls between backoffs (the
    # hot-spin signature). Two per backoff is a probe; twenty is a spin.
    assert ch.api_calls <= 2 * len(sleeps) + 1
    assert ch.probes >= 1, "a failing poll never asked whether a webhook is the cause"


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


# ── a registered webhook is not a transient failure ──────────────────────────


async def test_poll_updates_parks_while_a_webhook_is_registered(monkeypatch):
    """The bug, measured: ~300 ERRORs/day for eight days from a poller that could
    never succeed. Telegram refuses getUpdates for as long as a webhook exists, so
    the backoff below was a permanent five-minute error generator."""
    ch = _FakeChannel(results=[None, None, None, None], webhook_url="https://edge.example/tg/abc")
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 3:
            ch._running = False

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    await asyncio.wait_for(tg.TelegramChannel._poll_updates(ch), timeout=5.0)

    # Every sleep is the long park, not the escalating backoff.
    assert sleeps == [tg._WEBHOOK_RECHECK_S] * 3
    # And getUpdates was NOT re-issued between parks — that is the whole point.
    # First iteration: 1 getUpdates + 1 probe. Each further iteration: probe only.
    getupdates_calls = ch.api_calls - ch.probes
    assert getupdates_calls == 1, (
        f"the poller kept calling getUpdates ({getupdates_calls}x) while parked — "
        "each of those is a guaranteed 409 and an ERROR line"
    )


async def test_poll_updates_resumes_when_the_webhook_is_removed(monkeypatch):
    """Parked is not dead. A webhook is removed by a deliberate action, and the
    poller must come back on its own rather than need a restart."""
    ch = _FakeChannel(results=[None, None, [], None], webhook_url="https://edge.example/tg/abc")
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) == 1:
            ch.webhook_url = None  # operator deleted the webhook during the park
        if len(sleeps) >= 3:
            ch._running = False

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    await asyncio.wait_for(tg.TelegramChannel._poll_updates(ch), timeout=5.0)

    assert sleeps[0] == tg._WEBHOOK_RECHECK_S, "did not park on the registered webhook"
    # After the webhook went away the loop polled again and reached the good [] poll,
    # which stamps liveness — proof it left the park and resumed real polling.
    assert ch._last_event_at > 0.0
    assert ch.api_calls - ch.probes >= 2, (
        "getUpdates was never re-issued after the webhook was removed"
    )


async def test_poll_updates_keeps_backing_off_when_it_cannot_ask(monkeypatch):
    """⚠ "Could not ask" must NOT be read as "no webhook" — nor as "webhook".

    During a network outage getWebhookInfo fails exactly like getUpdates does.
    Parking on that would leave a bot deaf for ten minutes at a time after every
    blip; treating it as "no webhook" is the existing backoff, which is right.
    """
    ch = _FakeChannel(results=[None, None, None], webhook_unreachable=True)
    sleeps: list[float] = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 3:
            ch._running = False

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    await asyncio.wait_for(tg.TelegramChannel._poll_updates(ch), timeout=5.0)

    assert all(s < tg._WEBHOOK_RECHECK_S for s in sleeps), (
        f"an unanswerable probe was treated as a registered webhook: {sleeps}"
    )
    assert ch.probes >= 1
