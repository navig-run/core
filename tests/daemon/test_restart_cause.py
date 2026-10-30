"""The supervisor's boot line must say WHY the daemon (re)started.

Measured 2026-09-27: seven full daemon boots in ~3.5 h, each logged only as
``=== NAVIG Daemon starting (pid=…) ===`` with no stop line before it, and the one
restart that named a caller wrote it into the OLD process's log. Every boot opens a
"Brain offline" gap in the Telegram deck, so the cause mattered and was unknowable.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from navig.daemon import restart_cause as rc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_dir", lambda: tmp_path)
    return tmp_path


def test_a_recorded_restart_is_named_and_consumed(tmp_path):
    rc.record("service restart")
    cause = rc.boot_cause(had_previous_pid=True)
    assert cause.startswith("requested: service restart")
    assert not (tmp_path / rc._INTENT).exists(), "an intent must be consumed exactly once"
    # The NEXT boot must not re-blame the same request.
    assert "service restart" not in rc.boot_cause(had_previous_pid=True)


def test_hops_chain_into_one_path(monkeypatch):
    """The API hands off to the scheduled task, which runs `service restart`:
    the boot line shows the whole path, not only the last hop."""
    rc.record("api /api/daemon/restart", actor="127.0.0.1")
    rc.record("service restart")
    cause = rc.boot_cause(had_previous_pid=True)
    assert "api /api/daemon/restart (actor=127.0.0.1" in cause
    assert cause.index("api /api/daemon/restart") < cause.index("service restart")


def test_agent_session_is_identified(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "3d4baa0c-1111-2222-3333-444455556666")
    rc.record("service restart")
    assert "agent_session=3d4baa0c" in rc.boot_cause(had_previous_pid=True)


def test_an_old_intent_is_not_blamed_for_a_later_boot(tmp_path):
    (tmp_path / rc._INTENT).write_text(
        json.dumps({"at": time.time() - rc.INTENT_MAX_AGE_S - 60, "chain": [{"source": "service restart"}]}),
        encoding="utf-8",
    )
    cause = rc.boot_cause(had_previous_pid=True)
    assert "service restart" not in cause
    assert cause.startswith("UNEXPLAINED")


def test_a_hard_kill_is_distinguished_from_a_clean_stop():
    # No intent, previous pid file left behind, no clean-exit marker → killed/crashed.
    assert rc.boot_cause(had_previous_pid=True).startswith("UNEXPLAINED")
    # A clean shutdown leaves a marker instead.
    rc.mark_clean_exit()
    assert "stopped cleanly" in rc.boot_cause(had_previous_pid=False)
    # And a machine with no previous daemon at all.
    assert rc.boot_cause(had_previous_pid=False).startswith("first start")


def test_only_the_first_argv_tokens_are_kept(monkeypatch):
    """A command line can carry a secret; the record keeps the verb only."""
    monkeypatch.setattr(sys, "argv", ["navig", "service", "restart", "--x", "SECRET_TOKEN_VALUE"])
    rc.record("service restart")
    raw = (rc._dir() / rc._INTENT).read_text(encoding="utf-8")
    assert "SECRET_TOKEN_VALUE" not in raw
    assert "navig service restart" in raw


def test_record_never_raises_when_the_dir_is_unusable(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(rc, "_dir", lambda: blocker / "daemon")  # mkdir under a FILE fails
    rc.record("service restart")  # must not raise — a diagnostic can't block a restart
    rc.mark_clean_exit()
    assert rc.boot_cause(had_previous_pid=False).startswith("first start")


def test_a_reaped_dead_pid_still_reads_unexplained_at_the_next_boot(monkeypatch, tmp_path):
    """The running-guard (``is_running``) reaps a dead daemon's pid file BEFORE the boot
    line checks for it — the reap must leave the evidence behind, or a crash reads as
    "first start"."""
    import subprocess

    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda *_a, **_k: None)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (tmp_path / "supervisor.pid").write_text(str(p.pid), encoding="utf-8")

    assert sup.NavigDaemon.is_running() is False  # reaps the pid file
    assert not (tmp_path / "supervisor.pid").exists()

    had_previous_pid = (tmp_path / "supervisor.pid").exists()  # what run() computes
    assert rc.boot_cause(had_previous_pid=had_previous_pid).startswith("UNEXPLAINED")
    # Consumed: the boot after THAT one is not blamed on the same death.
    assert rc.boot_cause(had_previous_pid=False).startswith("first start")


def _bare_run_daemon(monkeypatch, tmp_path, loop):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(sup.NavigDaemon, "_enumerate_navig_pids", staticmethod(lambda: []))
    monkeypatch.setattr(sup.NavigDaemon, "_kill_orphan_daemons", staticmethod(lambda **kw: []))
    monkeypatch.setattr(sup.signal, "signal", lambda *a, **kw: None)
    d = sup.NavigDaemon(health_port=0)
    monkeypatch.setattr(d, "_booting_sibling", lambda candidates=None: None)
    monkeypatch.setattr(d, "_supervisor_loop", loop(d))
    return d


def test_a_crashed_supervisor_loop_is_not_marked_clean(monkeypatch, tmp_path):
    """`run()`'s finally ran `_shutdown()`, which wrote the clean marker even while a
    crash propagated — the next boot then said "stopped cleanly" over a crash."""

    def loop(_d):
        async def _boom():
            raise RuntimeError("loop crashed")

        return _boom

    d = _bare_run_daemon(monkeypatch, tmp_path, loop)
    with pytest.raises(RuntimeError):
        d.run()

    assert not (tmp_path / rc._CLEAN).exists(), "a crash must not leave a clean marker"
    assert rc.boot_cause(had_previous_pid=False).startswith("UNEXPLAINED")


def test_an_orderly_supervisor_stop_is_still_marked_clean(monkeypatch, tmp_path):
    def loop(d):
        async def _stop():
            d._running = False

        return _stop

    d = _bare_run_daemon(monkeypatch, tmp_path, loop)
    d.run()

    assert "stopped cleanly" in rc.boot_cause(had_previous_pid=False)
