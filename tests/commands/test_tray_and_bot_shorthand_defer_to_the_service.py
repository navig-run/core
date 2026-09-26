"""The tray starts/stops the daemon through `navig service`; `navig bot --background` works.

Tray: `start_daemon` spawned the daemon as the TRAY's child — close the tray and
the daemon is orphan-shaped (the shape an hourly process sweep kills), with no
task env and no autostart. `stop_daemon` sent CTRL_BREAK to a windowless process
(a no-op), force-killed it after 8 s, deleted the pid file itself, and never
disabled the scheduled task — the daemon was back within five minutes.
"""

from __future__ import annotations

import types

import pytest
from typer.testing import CliRunner

from navig.commands import gateway as gw_cmd


@pytest.fixture(scope="module")
def tray_app():
    """Import the tray module WITHOUT letting it re-wrap pytest's captured stdout.

    `tray_app` rebinds `sys.stdout`/`sys.stderr` to UTF-8 `TextIOWrapper`s of
    the current buffers at import time (it runs under pythonw). Wrapping
    pytest's capture buffer closes it when the wrapper is collected, and every
    later test dies with "I/O operation on closed file" — which is why no test
    imported this module before. Import it against throwaway streams, then put
    pytest's back.
    """
    import io
    import sys

    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        from navig.desktop import tray_app as mod
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return mod


@pytest.fixture
def tray(monkeypatch, tray_app):
    """A NavigTray with the process-touching parts stubbed."""
    t = tray_app.NavigTray.__new__(tray_app.NavigTray)
    t._python = "python"
    t.daemon = tray_app.ProcessState(name="Daemon")
    t._update_icon = lambda: None
    calls: dict = {"verbs": [], "direct": 0, "graceful": 0}
    monkeypatch.setattr(
        t, "_run_service_verb", lambda verb, timeout=60.0: calls["verbs"].append(verb) or True
    )
    monkeypatch.setattr(
        t, "_stop_daemon_graceful", lambda: calls.__setitem__("graceful", calls["graceful"] + 1)
    )
    monkeypatch.setattr(t, "_kill_orphan_bots", lambda: None)
    t._calls = calls
    return t


def test_tray_start_goes_through_the_service_when_installed(tray, monkeypatch):
    monkeypatch.setattr(tray, "_service_is_installed", lambda: True)
    spawned: list = []
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **kw: spawned.append(a) or types.SimpleNamespace(pid=1)
    )

    tray.start_daemon()

    assert tray._calls["verbs"] == ["start"]
    assert spawned == [], "the tray must not spawn the daemon as its own child"
    assert tray.daemon.process is None, "not our child — the pid file is the handle"


def test_tray_stop_goes_through_the_service_when_installed(tray, monkeypatch):
    monkeypatch.setattr(tray, "_service_is_installed", lambda: True)

    tray.stop_daemon()

    assert tray._calls["verbs"] == ["stop"]
    assert tray._calls["graceful"] == 0, (
        "no CTRL_BREAK-then-taskkill; the task must be disabled first"
    )


def test_tray_falls_back_to_the_direct_path_without_a_service(tray, monkeypatch, tmp_path):
    monkeypatch.setattr(tray, "_service_is_installed", lambda: False)
    spawned: list = []

    class _P:
        pid = 7

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: spawned.append(a[0]) or _P())
    monkeypatch.setattr("navig.desktop.tray_app.LOG_DIR", tmp_path)

    tray.start_daemon()

    assert tray._calls["verbs"] == []
    assert spawned and "navig.daemon.entry" in " ".join(spawned[0])


def test_tray_falls_back_when_service_start_fails(tray, monkeypatch, tmp_path):
    monkeypatch.setattr(tray, "_service_is_installed", lambda: True)
    monkeypatch.setattr(tray, "_run_service_verb", lambda verb, timeout=60.0: False)
    spawned: list = []

    class _P:
        pid = 7

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: spawned.append(a[0]) or _P())
    monkeypatch.setattr("navig.desktop.tray_app.LOG_DIR", tmp_path)

    tray.start_daemon()

    assert spawned, "a failed service start must still get the operator a daemon"


def test_run_service_verb_reports_a_non_zero_exit(monkeypatch, tray_app):
    t = tray_app.NavigTray.__new__(tray_app.NavigTray)
    t._python = "python"

    class R:
        returncode, stdout = 1, b"Couldn't stop the daemon"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: R())
    assert t._run_service_verb("stop") is False

    R.returncode = 0
    assert t._run_service_verb("stop") is True


# ── `navig bot --background` ─────────────────────────────────────────────────


def test_bot_background_shorthand_reaches_the_launch_policy(monkeypatch):
    called: list = []
    monkeypatch.setattr(
        "navig.daemon.launch.start_bot_in_background",
        lambda **kw: called.append(kw) or "daemon-running",
    )
    monkeypatch.setattr("navig.messaging.secrets.resolve_telegram_bot_token", lambda *a, **k: "t")

    r = CliRunner().invoke(gw_cmd.bot_app, ["--background"])

    assert r.exit_code == 0, r.output
    assert called and called[0]["gateway"] is False


def test_bot_background_with_gateway_shorthand(monkeypatch):
    called: list = []
    monkeypatch.setattr(
        "navig.daemon.launch.start_bot_in_background",
        lambda **kw: called.append(kw) or "daemon-running",
    )
    monkeypatch.setattr("navig.messaging.secrets.resolve_telegram_bot_token", lambda *a, **k: "t")

    r = CliRunner().invoke(gw_cmd.bot_app, ["-g", "-b"])

    assert r.exit_code == 0, r.output
    assert called[0]["gateway"] is True


def test_bot_subcommands_are_unaffected_by_the_callback_options():
    r = CliRunner().invoke(gw_cmd.bot_app, ["status", "--help"])
    assert r.exit_code == 0
