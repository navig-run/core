"""A broker register that fails must RETRY, and a permanent failure must be RECORDED.

This was one-shot, and in lighthouse mode the failure was deliberately swallowed —
right about severity (the uplink is the data path, not the broker), wrong about
visibility. The Mini App resolves THROUGH the broker, so a failed register leaves
it pointing at whatever was registered last, typically a dead cloudflared URL from
a previous session. From the daemon that state is invisible: uplink online, every
light green. It persisted for weeks on a real install before anyone could see it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from navig.cloud import CloudManager
from navig.cloud.broker_client import BrokerError
from navig.core import incidents


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not make the suite wait for real backoff."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture
def recorded(monkeypatch):
    """Capture what reaches the incident log."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda event, **data: seen.append((event, data)))
    return seen


def _manager(mode: str = "lighthouse") -> CloudManager:
    m = CloudManager(api_key="k", broker_url="https://api.navig.run", gateway_port=8765)
    m.mode = mode
    m.state.tunnel_url = "https://navig-lighthouse.example.workers.dev"
    m._bind_telegram_users = AsyncMock()
    return m


async def test_a_transient_failure_is_retried_and_then_succeeds(recorded):
    m = _manager()
    m._broker = AsyncMock()
    m._broker.register.side_effect = [BrokerError(503, "upstream"), None]

    await m._register_current_url()

    assert m._broker.register.await_count == 2, "a 5xx must be retried"
    m._bind_telegram_users.assert_awaited_once()
    assert recorded == [], "a recovered failure is not an incident"


async def test_a_permanent_failure_is_recorded_not_just_logged(recorded):
    m = _manager()
    m._broker = AsyncMock()
    m._broker.register.side_effect = BrokerError(400, '{"reason":"bad_tunnel_url"}')

    await m._register_current_url()

    assert [e for e, _ in recorded] == [incidents.BROKER_REGISTER_FAILED]
    data = recorded[0][1]
    assert data["url"] == m.state.tunnel_url
    assert data["mode"] == "lighthouse"
    assert "bad_tunnel_url" in data["error"]


async def test_a_rejected_url_is_not_retried(recorded):
    """A 4xx is a verdict about the URL — retrying cannot change the answer."""
    m = _manager()
    m._broker = AsyncMock()
    m._broker.register.side_effect = BrokerError(400, "bad_tunnel_url")

    await m._register_current_url(attempts=5)

    assert m._broker.register.await_count == 1
    assert [e for e, _ in recorded] == [incidents.BROKER_REGISTER_FAILED]


async def test_429_and_408_are_still_retried(recorded):
    """Rate-limited / timed-out is transient even though it is a 4xx."""
    for status in (408, 429):
        m = _manager()
        m._broker = AsyncMock()
        m._broker.register.side_effect = [BrokerError(status, "slow down"), None]
        await m._register_current_url()
        assert m._broker.register.await_count == 2, f"{status} should be retried"


async def test_lighthouse_keeps_a_green_status_but_the_binds_do_not_run(recorded):
    """Severity is unchanged — the uplink is the data path — only visibility changes."""
    m = _manager("lighthouse")
    m._broker = AsyncMock()
    m._broker.register.side_effect = BrokerError(400, "bad_tunnel_url")

    await m._register_current_url()

    assert m.state.last_error is None, "a broker blip is not a brain error in lighthouse mode"
    # Binding needs the row that register never created.
    m._bind_telegram_users.assert_not_awaited()
    assert recorded, "…but it must not be invisible"


async def test_tunnel_mode_still_surfaces_it_as_a_real_error(recorded):
    """Tunnel/direct modes DEPEND on the broker, so there it is a system error."""
    m = _manager("tunnel")
    m._broker = AsyncMock()
    m._broker.register.side_effect = BrokerError(400, "bad_tunnel_url")

    await m._register_current_url()

    assert m.state.last_error and "register" in m.state.last_error
    assert recorded


async def test_nothing_is_recorded_when_there_is_no_broker_or_url(recorded):
    m = _manager()
    m._broker = None
    await m._register_current_url()
    m2 = _manager()
    m2._broker = AsyncMock()
    m2.state.tunnel_url = None
    await m2._register_current_url()
    assert recorded == []
