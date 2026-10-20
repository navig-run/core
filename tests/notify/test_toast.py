"""The Windows desktop toast channel — opt-in, injection-safe, isolated from other channels."""

from __future__ import annotations

import subprocess
import sys

import pytest

from navig.notify import toast
from navig.notify.types import CHANNEL_KEYS, NOTIFICATION_TYPES, TYPE_KEYS

AGENT_TYPES = ("agent_done", "agent_failed", "agent_needs_input", "agent_stalled")


def test_toast_is_a_channel_and_no_type_defaults_to_it():
    assert "toast" in CHANNEL_KEYS
    assert [t["key"] for t in NOTIFICATION_TYPES if "toast" in t["default_channels"]] == []


def test_agent_monitor_types_are_registered_and_deck_only_by_default():
    for key in AGENT_TYPES:
        assert key in TYPE_KEYS
        row = next(t for t in NOTIFICATION_TYPES if t["key"] == key)
        assert row["default_channels"] == ["deck"], "Telegram/toast must be opted in, never default"


def test_non_windows_reports_unsupported_instead_of_pretending(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    ok, detail = toast.send_toast("t", "b")
    assert ok is False and "Windows" in detail


@pytest.fixture()
def toasts_allowed(monkeypatch):
    monkeypatch.setattr(toast, "_windows_blocks_toasts", lambda: "")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_windows_notifications_switched_off_is_reported_not_claimed_shown(monkeypatch):
    """Show() succeeds even when the user turned app notifications off - 'shown' would be a lie."""
    monkeypatch.setattr(toast, "_windows_blocks_toasts", lambda: toast._OFF_HINT)

    def must_not_run(*a, **k):
        raise AssertionError("must not spawn PowerShell for a toast Windows will drop")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    ok, detail = toast.send_toast("t", "b")
    assert ok is False and "turned off" in detail


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_the_real_registry_probe_returns_a_string_and_never_raises():
    assert isinstance(toast._windows_blocks_toasts(), str)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_user_text_travels_in_the_environment_never_the_command_line(monkeypatch, toasts_allowed):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evil = "x'); Remove-Item C:\\ -Recurse; ('"
    ok, _ = toast.send_toast(evil, "body $(whoami)")
    assert ok is True
    assert all(evil not in a and "whoami" not in a for a in seen["argv"])
    assert seen["env"]["NAVIG_TOAST_TITLE"] == evil[:120]
    assert "body $(whoami)" == seen["env"]["NAVIG_TOAST_BODY"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_a_failing_or_hanging_powershell_is_reported_not_raised(monkeypatch, toasts_allowed):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom\nmore"))
    assert toast.send_toast("t", "b") == (False, "boom")

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", hang)
    assert toast.send_toast("t", "b") == (False, "toast timed out")

    def missing(argv, **kw):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(subprocess, "run", missing)
    ok, detail = toast.send_toast("t", "b")
    assert ok is False and "unavailable" in detail


async def test_router_delivers_to_toast_and_a_toast_failure_does_not_block_deck(monkeypatch):
    from navig.notify import feed, prefs
    from navig.notify.router import NotificationRouter

    monkeypatch.setattr(prefs, "get_settings", lambda: {
        "master_enabled": True, "quiet_hours_enabled": False, "quiet_hours_start": 22, "quiet_hours_end": 7})
    monkeypatch.setattr(prefs, "enabled_channels", lambda t: ["deck", "toast"])
    monkeypatch.setattr(feed, "append", lambda *a, **k: {"id": "n1"})
    monkeypatch.setattr(toast, "send_toast", lambda title, body: (_ for _ in ()).throw(RuntimeError("no shell")))
    from navig.notify import health

    monkeypatch.setattr(health, "record", lambda *a, **k: None)

    out = await NotificationRouter().dispatch("agent_failed", "build failed", "exit 3", priority="high")
    by = {c["channel"]: c for c in out["channels"]}
    assert by["deck"]["ok"] is True
    assert by["toast"]["ok"] is False and "no shell" in by["toast"]["detail"]


async def test_quiet_hours_mute_the_toast_but_keep_the_deck(monkeypatch):
    from navig.notify import feed, health, prefs
    from navig.notify import router as router_mod

    monkeypatch.setattr(prefs, "get_settings", lambda: {
        "master_enabled": True, "quiet_hours_enabled": True, "quiet_hours_start": 0, "quiet_hours_end": 24})
    monkeypatch.setattr(router_mod, "_in_quiet_hours", lambda *a: True)
    monkeypatch.setattr(prefs, "enabled_channels", lambda t: ["deck", "toast"])
    monkeypatch.setattr(feed, "append", lambda *a, **k: {"id": "n1"})
    monkeypatch.setattr(health, "record", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(toast, "send_toast", lambda t, b: called.append(t) or (True, "shown"))

    out = await router_mod.NotificationRouter().dispatch("agent_stalled", "t", "b", priority="high")
    assert [c["channel"] for c in out["channels"]] == ["deck"] and called == []
