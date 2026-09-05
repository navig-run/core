"""`navig service install` must not silently disable components you had enabled.

It is not only a first-time installer. `navig service status` prints it as the FIX
for an unhealthy autostart task::

    Task Scheduler: Installed but NOT healthy
      ! no next run scheduled — it cannot restart a dead daemon
      Fix: navig service install   (re-registers with a watchdog trigger)

so operators run it on a WORKING install. It used to write every component from the
CLI flag defaults -- and those default to off:

    cfg["gateway"] = gateway        # --gateway defaults to False

So re-registering a task to repair its trigger set `gateway: false`.

That is not cosmetic. The gateway holds the Lighthouse uplink, so the next daemon
start from the task came up with the Telegram bot and NO uplink: the edge answered
`503 x-navig-brain: offline`, the Deck could not reach the brain, and `navig service
status` still said "Daemon is RUNNING". Measured on the operator's machine
2026-09-04 after exactly that sequence -- the printed repair advice caused it.
"""

from __future__ import annotations

import pytest

from navig.commands.service import _resolved_daemon_components


def _existing() -> dict:
    """A working install: the operator turned the gateway and scheduler ON."""
    return {
        "telegram_bot": True,
        "gateway": True,
        "gateway_port": 8789,
        "scheduler": True,
        "health_port": 7777,
        "engagement": True,
    }


def test_unspecified_flags_keep_the_existing_config() -> None:
    """THE regression: a bare `navig service install` must change nothing here."""
    out = _resolved_daemon_components(
        _existing(), bot=None, gateway=None, scheduler=None, health_port=None
    )
    assert out["gateway"] is True, (
        "a bare `navig service install` disabled the gateway -- which is what holds "
        "the Lighthouse uplink, so the Deck loses the brain on the next daemon start"
    )
    assert out["scheduler"] is True
    assert out["telegram_bot"] is True
    assert out["health_port"] == 7777


def test_unrelated_keys_are_untouched() -> None:
    out = _resolved_daemon_components(
        _existing(), bot=None, gateway=None, scheduler=None, health_port=None
    )
    assert out["gateway_port"] == 8789
    assert out["engagement"] is True


@pytest.mark.parametrize("flag,key", [("gateway", "gateway"), ("scheduler", "scheduler")])
def test_an_explicit_off_still_turns_it_off(flag: str, key: str) -> None:
    """Preserving must not become "impossible to disable"."""
    kwargs = {"bot": None, "gateway": None, "scheduler": None, "health_port": None}
    kwargs[flag] = False
    out = _resolved_daemon_components(_existing(), **kwargs)
    assert out[key] is False


def test_an_explicit_on_turns_it_on() -> None:
    cfg = {"telegram_bot": True, "gateway": False, "scheduler": False, "health_port": 0}
    out = _resolved_daemon_components(
        cfg, bot=None, gateway=True, scheduler=None, health_port=None
    )
    assert out["gateway"] is True


def test_a_first_install_falls_back_to_the_documented_defaults() -> None:
    """No config on disk yet: the defaults still apply, so behaviour is unchanged
    for a genuine first-time install."""
    from navig.daemon.entry import DEFAULT_DAEMON_CONFIG

    out = _resolved_daemon_components(
        {}, bot=None, gateway=None, scheduler=None, health_port=None
    )
    assert out["telegram_bot"] == DEFAULT_DAEMON_CONFIG.get("telegram_bot", True)
    assert out["gateway"] == DEFAULT_DAEMON_CONFIG.get("gateway", False)
    assert out["scheduler"] == DEFAULT_DAEMON_CONFIG.get("scheduler", False)
