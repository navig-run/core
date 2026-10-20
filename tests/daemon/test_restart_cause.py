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
