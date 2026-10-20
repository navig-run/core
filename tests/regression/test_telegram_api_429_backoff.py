"""Regression: the Telegram transport honours a 429 ``retry_after`` once.

`_api_call` is a TelegramApiMixin method, but at runtime it is bound onto
TelegramChannel via functools.partial, so ``self`` is the channel — a plain
object that inherits NOTHING from the mixin class. The first version of the 429
backoff read its cap from a mixin class attribute (``self._MAX_RETRY_AFTER_S``),
which raised AttributeError on the first real 429; the surrounding ``except``
swallowed it, so the call returned None and the backoff never ran.

These tests call `_api_call` with a bare object as ``self`` — the shape the
channel actually has — so a mixin-only attribute fails here, not in production.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels import telegram_api
from navig.gateway.channels.telegram_api import TelegramApiMixin


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Session:
    """Serves scripted Bot API payloads in order and records each POST."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.posts: list[str] = []

    def post(self, url: str, json=None) -> _Resp:
        self.posts.append(url)
        return _Resp(self._payloads.pop(0))


class _ChannelShape:
    """What `self` is at runtime: NOT a TelegramApiMixin subclass."""

    def __init__(self, session: _Session) -> None:
        self._session = session
        self.base_url = "https://api.telegram.org/botTEST"


def _rate_limited(retry_after: float) -> dict:
    return {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after N",
        "parameters": {"retry_after": retry_after},
    }


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(telegram_api.asyncio, "sleep", _fake_sleep)
    return recorded


async def test_429_within_cap_is_retried_once_and_succeeds(sleeps):
    session = _Session([_rate_limited(1), {"ok": True, "result": {"message_id": 7}}])
    channel = _ChannelShape(session)

    result = await TelegramApiMixin._api_call(channel, "editMessageText", {"text": "x"})

    assert result == {"message_id": 7}, "the retried call's result must be returned"
    assert sleeps == [1.0], "must wait exactly the server-mandated retry_after"
    assert len(session.posts) == 2


async def test_429_beyond_cap_is_not_waited_out(sleeps):
    over = telegram_api._MAX_RETRY_AFTER_S + 25
    session = _Session([_rate_limited(over)])
    channel = _ChannelShape(session)

    result = await TelegramApiMixin._api_call(channel, "editMessageText", {"text": "x"})

    assert result is None
    assert sleeps == [], "a long mandated cooldown must not stall the turn"
    assert len(session.posts) == 1


async def test_a_second_429_gives_up_after_one_retry(sleeps):
    session = _Session([_rate_limited(1), _rate_limited(1)])
    channel = _ChannelShape(session)

    result = await TelegramApiMixin._api_call(channel, "sendMessage", {"text": "x"})

    assert result is None
    assert sleeps == [1.0], "exactly one backoff, never a loop"
    assert len(session.posts) == 2


async def test_non_429_error_is_not_retried(sleeps):
    session = _Session([{"ok": False, "error_code": 400, "description": "Bad Request"}])
    channel = _ChannelShape(session)

    result = await TelegramApiMixin._api_call(channel, "sendMessage", {"text": "x"})

    assert result is None
    assert sleeps == []
    assert len(session.posts) == 1
