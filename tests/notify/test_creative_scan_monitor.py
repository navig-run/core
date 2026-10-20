"""The creative_scan monitor: pacing, loop, mode arming, dual registration.

Owner scenario rows covered here: "Creative actually activates capability
intelligence" and "Standard scans nothing" — proven through the same
``scan_once`` entry point the gateway spawns.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from navig.notify.monitors import creative_scan as monitor


def _ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _rec(command: str, minutes_ago: float):
    return SimpleNamespace(
        command=command, timestamp=_ts(minutes_ago), status=SimpleNamespace(value="success")
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh config/data dirs + fresh tracker + fresh notify store schema."""
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.notify import store as notify_store

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    monkeypatch.setattr(notify_store, "_initialised", False)
    yield
    reset_config_manager()
    ConfigSingleton._instance = None


# ── pacing ───────────────────────────────────────────────────────────────────


def test_seconds_until_due():
    assert monitor.seconds_until_due(None) == 0.0  # never scanned → due now
    now = time.time()
    fresh = monitor.seconds_until_due(now, now=now)
    assert monitor.INTERVAL_S - 1 <= fresh <= monitor.INTERVAL_S
    stale = monitor.seconds_until_due(now - monitor.INTERVAL_S - 5, now=now)
    assert stale == 0.0


# ── one pass ─────────────────────────────────────────────────────────────────


async def test_scan_once_returns_the_summary(monkeypatch):
    from navig.creative import signals

    sentinel = {"created": [], "dispatched": [], "skipped": True}
    monkeypatch.setattr(signals, "run_scan", lambda **kw: sentinel)
    assert await monitor.scan_once() is sentinel


async def test_scan_once_swallows_failures_as_none(monkeypatch):
    from navig.creative import signals

    def _boom(**kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(signals, "run_scan", _boom)
    assert await monitor.scan_once() is None  # → the loop backs off an hour


# ── the loop ─────────────────────────────────────────────────────────────────


async def test_monitor_loop_runs_a_pass_then_paces(monkeypatch):
    calls: list[dict] = []

    async def _fake_once():
        calls.append({})
        return {"created": [], "dispatched": [], "skipped": True}

    monkeypatch.setattr(monitor, "BOOT_DELAY_S", 0.0)
    monkeypatch.setattr(monitor, "seconds_until_due", lambda *a, **k: 0.0)
    monkeypatch.setattr(monitor, "scan_once", _fake_once)

    task = asyncio.create_task(monitor.run_creative_scan_monitor())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 1, "the loop must run at least one pass"


# ── mode arming (owner scenarios) ────────────────────────────────────────────


def test_creative_arms_scan(monkeypatch):
    """Mode=creative + seeded history ⇒ grounded proposal + dispatch."""
    from navig.creative import proposals as prop
    from navig.creative import signals

    seeded = [_rec("navig frobnicate reports", i * 90) for i in range(6)]
    monkeypatch.setattr(signals, "_read_operations", lambda limit=500: seeded)
    dispatched: list[dict] = []
    monkeypatch.setattr(signals, "_default_dispatch", dispatched.append)

    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", "creative") is True

    summary = asyncio.run(monitor.scan_once())
    assert summary is not None
    assert summary["mode"] == "creative"
    assert len(summary["created"]) == 1
    assert len(dispatched) == 1
    row = prop.get_proposal(summary["created"][0])
    assert row["evidence"][0]["count"] == 6


def test_standard_skips_scan(monkeypatch):
    """Mode=standard ⇒ the scheduled pass reads nothing and proposes nothing."""
    from navig.creative import signals

    def _explode(limit=500):
        raise AssertionError("Standard mode must not read history")

    monkeypatch.setattr(signals, "_read_operations", _explode)
    dispatched: list[dict] = []
    monkeypatch.setattr(signals, "_default_dispatch", dispatched.append)

    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", "standard") is True

    summary = asyncio.run(monitor.scan_once())
    assert summary is not None
    assert summary["skipped"] is True
    assert summary["created"] == [] and dispatched == []


# ── dual registration (the wiring the gate demands) ──────────────────────────


def test_creative_scan_is_registered_on_both_sides():
    from navig.gateway.deck.routes import notify as deck_notify
    from navig.gateway.server import NavigGateway

    assert "creative_scan" in NavigGateway.MONITOR_KEYS
    assert "creative_scan" in NavigGateway.MONITORS_DEFAULT_ON
    assert "creative_scan" in deck_notify._MONITOR_KEYS
    assert "creative_scan" in deck_notify._DEFAULT_ON
    # The spawn dispatch must know the name or enabling it silently no-ops.
    import inspect

    source = inspect.getsource(NavigGateway._start_monitor)
    assert 'name == "creative_scan"' in source
