"""A test run must never resolve to the OPERATOR'S daemon state.

This is the guard for a measured outage, not a hypothetical. On 2026-09-04 the
operator's real ``%LOCALAPPDATA%/navig/logs/daemon.log`` contained::

    09:22:57 [WARNING] Stale PID file (pid=7) - removing and starting fresh
    09:23:00 [INFO] Swept 1 orphan daemon PID(s): [56800]

``pid=7`` is a value that exists only inside a test stub, and 56800 was the
operator's LIVE supervisor -- killed three seconds later, which an independent
process-watcher recorded at 09:23:07. The same log carried lines naming pytest
tmp dirs (``.../pytest-of-subdose/popen-gw3/test_add_telegram_bot0/...``)
interleaved with genuine boot records.

The mechanism, end to end:

1. ``paths.config_dir()`` falls back to the operator's real ``~/.navig`` when a
   test isolates nothing -- and ``tests/ops/test_daemon.py`` constructs a bare
   ``NavigDaemon()``, so ``_pid_file()`` resolved to their live
   ``supervisor.pid``.
2. A test overwrites or removes that file. The running daemon is now no longer
   *recorded* as running.
3. The next daemon start reads the stale pid file and classifies the live daemon
   as an orphan of a previous generation -- so it ``taskkill /F /T``s it.

Note what does NOT save you: :meth:`_kill_orphan_daemons` correctly scopes every
candidate by its effective ``NAVIG_CONFIG_DIR``, and that scoping is working as
designed here. The daemon really IS "ours"; it was simply erased from the file
that vouches for it. Config-dir scoping cannot rescue a process whose identity
record a test deleted.

Consequence for the operator: the bot goes silent with no traceback, no crash
dump and no shutdown line -- the daily check-ins simply stop arriving.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from navig.daemon import supervisor
from navig.platform import paths


def _real_home_state() -> tuple[Path, Path]:
    """The paths the OPERATOR's daemon uses -- what a test must never touch."""
    env = {k: os.environ.pop(k) for k in ("NAVIG_CONFIG_DIR", "NAVIG_LOG_DIR") if k in os.environ}
    try:
        return (paths.config_dir() / "daemon").resolve(), paths.log_dir().resolve()
    finally:
        os.environ.update(env)


def test_pid_file_under_pytest_is_not_the_operators(monkeypatch) -> None:
    """The pid file is the identity record whose corruption killed the daemon."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    real_daemon_dir, _ = _real_home_state()

    resolved = supervisor._pid_file().resolve()

    assert real_daemon_dir not in resolved.parents and resolved.parent != real_daemon_dir, (
        f"a test resolved the pid file to the operator's real daemon dir: {resolved}\n"
        "Writing or removing it makes their LIVE daemon unrecorded, and the next "
        "daemon start sweeps it as an orphan (measured: supervisor 56800, 2026-09-04)."
    )


def test_log_dir_under_pytest_is_not_the_operators(monkeypatch) -> None:
    """NavigDaemon.__init__ opens daemon.log before any test body runs."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
    _, real_log_dir = _real_home_state()

    assert supervisor._resolve_log_dir().resolve() != real_log_dir, (
        "a test run writes into the operator's real daemon.log -- the one file "
        "you would read to find out why their daemon died"
    )


def test_the_scheduled_task_does_NOT_move_the_real_daemons_logs(monkeypatch, tmp_path) -> None:
    """A CORRECTION to this file's first version -- kept because the mistake is the lesson.

    The original fix also keyed on ``NAVIG_CONFIG_DIR``, reasoning that it means "an
    isolated brain". It does not: the Windows scheduled task sets it on EVERY launch
    (``_task_bootstrap_args`` bakes ``NAVIG_CONFIG_DIR=<home>`` in), so keying on it
    moved the REAL daemon's logs. Measured within minutes of shipping:
    ``~/.navig/logs/daemon.log`` was live at 17:40 while
    ``%LOCALAPPDATA%/navig/logs/daemon.log`` -- the file ``navig service logs`` and the
    deck viewer actually read -- sat frozen at 17:35. That is precisely the split-brain
    this whole line of work exists to remove, reintroduced by the fix for it.

    So: a NON-test process that sets ``NAVIG_CONFIG_DIR`` keeps the canonical log dir.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "brain"))
    monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor, "_under_pytest", lambda: False)
    _, real_log_dir = _real_home_state()

    assert supervisor._resolve_log_dir().resolve() == real_log_dir, (
        "the scheduled task sets NAVIG_CONFIG_DIR, so keying the log dir on it splits "
        "the real daemon's logs away from where `navig service logs` reads"
    )


def test_a_test_run_is_still_isolated_even_with_NAVIG_CONFIG_DIR_set(monkeypatch, tmp_path) -> None:
    """The isolation guarantee must survive that correction.

    `"pytest" in sys.modules` is true for the whole test process, so it applies however
    late a test sets its env -- which is why the pytest check alone is sufficient and
    the NAVIG_CONFIG_DIR branch was never needed for isolation in the first place.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "brain"))
    monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
    _, real_log_dir = _real_home_state()

    resolved = supervisor._resolve_log_dir().resolve()
    assert resolved != real_log_dir, "a test run reached the operator's real log dir"
    assert str(os.getpid()) in str(resolved), "expected the per-process pytest temp dir"


def test_an_explicit_NAVIG_LOG_DIR_still_wins(monkeypatch, tmp_path) -> None:
    """An explicit env var is a deliberate statement of intent -- never overridden."""
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "explicit"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "brain"))

    assert supervisor._resolve_log_dir().resolve() == (tmp_path / "explicit").resolve()


def test_the_REAL_daemon_still_uses_the_real_paths(monkeypatch) -> None:
    """The anti-regression floor: the operator's own daemon must be UNCHANGED.

    A fix that quarantined test runs by also moving the real daemon's logs would
    silently break ``navig service logs`` and ``navig doctor``, which read
    ``paths.log_dir()``. This asserts the redirect is conditional, not global --
    which is why ``_under_pytest`` is a function rather than an inline check.
    """
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
    monkeypatch.setattr(supervisor, "_under_pytest", lambda: False)
    real_daemon_dir, real_log_dir = _real_home_state()

    assert supervisor._resolve_log_dir().resolve() == real_log_dir
    assert supervisor._pid_file().resolve().parent == real_daemon_dir


def test_workers_do_not_share_one_state_dir() -> None:
    """xdist runs many workers at once; one shared path would be a new race."""
    assert str(os.getpid()) in str(supervisor._pytest_state_dir())


@pytest.mark.parametrize("name", ["_pid_file", "_state_file", "_resolve_log_dir"])
def test_every_state_path_is_resolved_at_call_time(name) -> None:
    """A frozen module constant cannot honour isolation set after import."""
    assert callable(getattr(supervisor, name))
