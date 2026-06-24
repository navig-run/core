"""Regression tests for transient-failure retry in TelegramChannel._setup_webhook.

A transient setWebhook failure (e.g. Telegram briefly failing to resolve the edge
host) must NOT permanently demote the brain to polling — it should retry with
backoff first, and only fall back to polling if the edge is genuinely unreachable.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.gateway.channels.telegram import TelegramChannel


def _bare_channel():
    """A TelegramChannel with only the attributes _setup_webhook touches
    (skips the heavy __init__)."""
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.webhook_url = "https://edge.example.dev/tg/abc123"
    ch.webhook_secret = "s3cr3t"
    ch._running = True
    ch._use_webhook = True
    ch._poll_task = None
    return ch


async def test_setup_webhook_retries_transient_then_succeeds(monkeypatch):
    ch = _bare_channel()
    calls = {"n": 0}

    async def fake_api(method, data=None, **kw):
        calls["n"] += 1
        # Fail once (transient), then succeed.
        return None if calls["n"] == 1 else {"url": ch.webhook_url}

    ch._api_call = fake_api
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.asyncio.sleep", AsyncMock()
    )

    await ch._setup_webhook()

    assert calls["n"] == 2          # retried exactly once before success
    assert ch._use_webhook is True  # stayed on webhook (no demotion)
    assert ch._poll_task is None    # never started polling


async def test_setup_webhook_falls_back_after_exhausting_retries(monkeypatch):
    ch = _bare_channel()
    calls = {"n": 0}

    async def fake_api(method, data=None, **kw):
        calls["n"] += 1
        return None  # always fails — edge genuinely unreachable

    ch._api_call = fake_api
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.asyncio.sleep", AsyncMock()
    )

    created = {}

    def fake_create_task(coro):
        created["coro"] = coro
        coro.close()  # don't actually run the poll loop

        class _Task:
            def done(self):
                return False

        return _Task()

    monkeypatch.setattr(
        "navig.gateway.channels.telegram.asyncio.create_task", fake_create_task
    )

    await ch._setup_webhook()

    assert calls["n"] == 3            # initial + 2 retries (backoffs == 2)
    assert ch._use_webhook is False   # fell back to polling
    assert "coro" in created          # a poll task was started


async def test_setup_webhook_abandons_when_channel_stopped(monkeypatch):
    ch = _bare_channel()
    ch._running = False  # channel stopped before/with setup

    api = AsyncMock(return_value=None)
    ch._api_call = api
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.asyncio.sleep", AsyncMock()
    )

    await ch._setup_webhook()

    api.assert_not_called()          # bailed out without calling Telegram
    assert ch._poll_task is None     # no polling started either
