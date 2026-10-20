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


# ── the READ side: a failed read must never become a destructive write ───────
#
# The flags above were only half of the story. `service_install` read the config
# with a bare `Path.read_text()` and fell back to `DEFAULT_DAEMON_CONFIG.copy()`
# on OSError too — so an UNREADABLE-BUT-VALID config (an antivirus/backup agent
# holding the file, a read landing mid-replace) reached
# `_resolved_daemon_components` with no `gateway` key and got written back as
# `gateway: false`. Measured on the operator's machine: the config was rewritten
# 2026-10-19 22:55 and `daemon.log` shows no `Registered gateway` after
# 2026-10-18 13:04 — two days without an uplink, every webhook 503 at the edge,
# while `navig service status` reported the daemon RUNNING.


def test_an_unreadable_config_is_never_replaced_with_defaults(tmp_path, monkeypatch) -> None:
    import json

    import typer

    from navig.commands import service as svc
    from navig.core import yaml_io
    from navig.daemon import entry, service_manager

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_existing(), indent=2), encoding="utf-8")
    before = cfg_path.read_bytes()

    def _sharing_violation(*_a, **_k):
        raise OSError(32, "The process cannot access the file because it is being used")

    # Both readers the install path goes through: `save_default_config()`'s own check
    # and the one under test. (It already refuses to repair on OSError — the bug was
    # the second read disagreeing with it.)
    monkeypatch.setattr(entry, "_daemon_config_path", lambda: cfg_path)
    monkeypatch.setattr(yaml_io, "read_text_retrying", _sharing_violation)
    monkeypatch.setattr(entry, "read_text_retrying", _sharing_violation)
    # Never let a regression reach the real Task Scheduler from a test.
    monkeypatch.setattr(service_manager, "detect_best_method", lambda: "task")
    monkeypatch.setattr(service_manager, "install", lambda **_k: (True, "ok"))

    with pytest.raises(typer.Exit) as exc:
        svc.service_install(
            method="task", no_start=True, bot=None, gateway=None,
            scheduler=None, health_port=None,
        )

    assert exc.value.exit_code == 1, "a failed read must refuse, not proceed blind"
    assert cfg_path.read_bytes() == before, (
        "a READ that failed wrote defaults over the operator's config — which is how "
        "`gateway` silently became false and the brain lost its uplink"
    )
