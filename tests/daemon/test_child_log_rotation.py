"""Supervised child logs are size-rotated like the supervisor's own daemon.log.

`ChildProcess.start` used to `open(child_log, "a")` forever: child stdout+stderr
both land there and nothing ever trimmed it — while `daemon.log` itself has a
RotatingFileHandler (5 MB x 3). The fix is `_rotate_child_log(path, cap, keep)`:

* rotated at every (re)start, AFTER closing the previous handle (Windows
  refuses to rename a file any process holds open — measured WinError 32);
* swept periodically for DEAD children only (a live child's fd follows the file,
  not the path, so a mid-run rename would sever the tail);
* never raises — a locked file skips and retries next start.
"""

from __future__ import annotations

import logging
import os
import sys
import time

import pytest

import navig.daemon.supervisor as supervisor
from navig.daemon.supervisor import (
    CHILD_LOG_KEEP,
    ChildProcess,
    NavigDaemon,
    _rotate_child_log,
)

_LOGGER = logging.getLogger("test.child_log_rotation")
_BIG = "X" * 2000  # > the 1000-byte cap used in these tests
_CAP = 1000


# ── _rotate_child_log: the helper ──────────────────────────────────────────


def test_rotate_moves_live_content_to_the_backup(tmp_path) -> None:
    log = tmp_path / "child.log"
    log.write_text(_BIG, encoding="utf-8")
    assert _rotate_child_log(log, cap=_CAP, keep=CHILD_LOG_KEEP) is True
    # The live file is renamed away; the next open() recreates it fresh.
    assert not log.exists()
    assert (tmp_path / "child.log.1").read_text(encoding="utf-8") == _BIG


def test_rotate_is_a_noop_under_the_cap(tmp_path) -> None:
    log = tmp_path / "child.log"
    log.write_text("small", encoding="utf-8")
    assert _rotate_child_log(log, cap=_CAP, keep=3) is False
    assert log.read_text(encoding="utf-8") == "small"
    assert not (tmp_path / "child.log.1").exists()


def test_rotate_shifts_generations_and_bounds_the_count(tmp_path) -> None:
    log = tmp_path / "child.log"
    log.write_text(_BIG, encoding="utf-8")
    (tmp_path / "child.log.1").write_text("GEN1", encoding="utf-8")
    (tmp_path / "child.log.2").write_text("GEN2", encoding="utf-8")

    assert _rotate_child_log(log, cap=_CAP, keep=3) is True

    assert (tmp_path / "child.log.1").read_text(encoding="utf-8") == _BIG
    assert (tmp_path / "child.log.2").read_text(encoding="utf-8") == "GEN1"
    assert (tmp_path / "child.log.3").read_text(encoding="utf-8") == "GEN2"
    assert not (tmp_path / "child.log.4").exists()  # bounded at `keep`


def test_rotate_drops_generations_past_keep(tmp_path) -> None:
    log = tmp_path / "child.log"
    log.write_text(_BIG, encoding="utf-8")
    (tmp_path / "child.log.1").write_text("GEN1", encoding="utf-8")
    (tmp_path / "child.log.2").write_text("GEN2", encoding="utf-8")  # oldest — dropped

    assert _rotate_child_log(log, cap=_CAP, keep=2) is True

    assert (tmp_path / "child.log.1").read_text(encoding="utf-8") == _BIG
    assert (tmp_path / "child.log.2").read_text(encoding="utf-8") == "GEN1"
    assert not (tmp_path / "child.log.3").exists()


def test_rotate_skips_without_raising_when_rename_is_refused(tmp_path, monkeypatch) -> None:
    """The Windows sharing-violation path: rename denied → skip, no exception."""
    log = tmp_path / "child.log"
    log.write_text(_BIG, encoding="utf-8")

    def _denied(src, dst):  # noqa: ANN001 — os.rename signature
        raise PermissionError(13, "The process cannot access the file (sharing violation)")

    monkeypatch.setattr(supervisor.os, "rename", _denied)

    assert _rotate_child_log(log, cap=_CAP, keep=CHILD_LOG_KEEP) is False
    assert log.read_text(encoding="utf-8") == _BIG  # untouched
    assert not (tmp_path / "child.log.1").exists()


@pytest.mark.skipif(os.name != "nt", reason="POSIX renames an open file happily")
def test_rotate_skips_a_file_we_hold_open(tmp_path) -> None:
    """Real lock: our own append handle (like a child's inherited stdout) blocks
    the rename on Windows — must skip, never raise."""
    log = tmp_path / "child.log"
    log.write_text(_BIG, encoding="utf-8")
    with open(log, "a", encoding="utf-8") as fh:
        assert _rotate_child_log(log, cap=_CAP, keep=CHILD_LOG_KEEP) is False
        assert fh.closed is False  # caller's handle untouched by a failed rotation
    assert log.read_text(encoding="utf-8") == _BIG


def test_rotate_handles_a_missing_file(tmp_path) -> None:
    assert _rotate_child_log(tmp_path / "absent.log", cap=_CAP, keep=3) is False


# ── ChildProcess.start: rotate-on-(re)start, close-before-open ─────────────


def _isolate_log_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(supervisor, "_resolve_log_dir", lambda: tmp_path)


def test_child_start_rotates_an_oversized_log_before_reopening(tmp_path, monkeypatch) -> None:
    _isolate_log_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "CHILD_LOG_CAP", _CAP)  # read at call time

    (tmp_path / "probe.log").write_text(_BIG, encoding="utf-8")

    child = ChildProcess("probe", [sys.executable, "-c", "print('fresh-line')"])
    try:
        assert child.start(_LOGGER) is True
        child.process.wait(timeout=30)
        assert (tmp_path / "probe.log.1").read_text(encoding="utf-8") == _BIG
        live = (tmp_path / "probe.log").read_text(encoding="utf-8")
        assert "fresh-line" in live
        assert "X" not in live  # old content is gone from the live file
    finally:
        child.stop(_LOGGER)


def test_child_start_closes_the_previous_log_handle(tmp_path, monkeypatch) -> None:
    """The pre-fix restart path overwrote `_log_fh` without closing it — one leaked
    fd per restart, AND (on Windows) a lingering handle that blocks rotation."""
    _isolate_log_dir(monkeypatch, tmp_path)

    child = ChildProcess("leak", [sys.executable, "-c", "print('a')"])
    try:
        assert child.start(_LOGGER) is True
        first_fh = child._log_fh
        child.process.wait(timeout=30)

        assert child.start(_LOGGER) is True
        second_fh = child._log_fh
        assert second_fh is not first_fh
        assert first_fh.closed, "the previous life's log handle must be closed before reopen"
        assert second_fh.closed is False
        child.process.wait(timeout=30)
    finally:
        child.stop(_LOGGER)


# ── NavigDaemon: the periodic sweep ────────────────────────────────────────


def test_periodic_sweep_rotates_dead_children_and_never_touches_live_ones(
    tmp_path, monkeypatch
) -> None:
    _isolate_log_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "CHILD_LOG_CAP", _CAP)

    dead_log = tmp_path / "dead.log"
    dead_log.write_text(_BIG, encoding="utf-8")
    live_log = tmp_path / "live.log"
    live_log.write_text(_BIG, encoding="utf-8")

    dead = ChildProcess("dead", [sys.executable, "-c", "pass"])  # process is None → not alive
    live = ChildProcess("live", [sys.executable, "-c", "pass"])
    monkeypatch.setattr(live, "is_alive", lambda: True)  # pretend the child is running

    daemon = NavigDaemon()
    daemon.children = [dead, live]
    daemon._last_log_sweep = time.monotonic() - supervisor.HEARTBEAT_S - 1

    daemon._maybe_rotate_child_logs()

    # Dead child: rotated (content moved to .1, live path gone until next start).
    assert not dead_log.exists()
    assert (tmp_path / "dead.log.1").read_text(encoding="utf-8") == _BIG
    # Live child: untouched — its fd follows the file, a rename would sever the tail.
    assert live_log.exists()
    assert live_log.read_text(encoding="utf-8") == _BIG
    assert not (tmp_path / "live.log.1").exists()


def test_periodic_sweep_throttles_to_one_pass_per_heartbeat(tmp_path, monkeypatch) -> None:
    _isolate_log_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "CHILD_LOG_CAP", _CAP)

    log = tmp_path / "quiet.log"
    log.write_text(_BIG, encoding="utf-8")

    child = ChildProcess("quiet", [sys.executable, "-c", "pass"])
    daemon = NavigDaemon()
    daemon.children = [child]
    daemon._last_log_sweep = time.monotonic()  # a pass just ran → within the beat window

    daemon._maybe_rotate_child_logs()

    # Throttled: the oversized file waits for the next heartbeat window.
    assert log.exists()
    assert log.read_text(encoding="utf-8") == _BIG
