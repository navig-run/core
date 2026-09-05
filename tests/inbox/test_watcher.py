"""The inbox polling watcher must survive files/dirs disappearing mid-scan and must not leak.

`_PollingBackend` is the DEFAULT inbox watcher (watchfiles is optional and not a declared
dependency). Two bugs were fixed here:

* TOCTOU crash — a file removed between ``iterdir()`` and ``stat()`` (temp files, another
  process, the router moving an item out) raised ``FileNotFoundError`` OUTSIDE the callback
  try/except, killing the daemon thread → the inbox silently stopped processing forever.
* ``_seen`` grew unbounded — keyed by ``path:mtime`` and never evicted, so a long-running
  daemon leaked memory for every file that ever passed through the inbox.
"""
from __future__ import annotations

from pathlib import Path

from navig.inbox import watcher

# ── _mtime_key ────────────────────────────────────────────────────────────────

def test_mtime_key_none_when_file_absent(tmp_path):
    assert watcher._mtime_key(tmp_path / "never.txt") is None


def test_mtime_key_for_real_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    key = watcher._mtime_key(p)
    assert key is not None and key.startswith(str(p))


# ── _scan resilience (the TOCTOU crash) ──────────────────────────────────────

def test_scan_skips_file_whose_key_cannot_be_read(tmp_path, monkeypatch):
    """A file that vanishes between listing and stat() yields a None change-key — it must be
    skipped (not fired, not crashed on). (Patching stat() directly can't model this: is_file()
    also stats, so it would mask the file first — so we drive the None-key path itself.)"""
    d = tmp_path / "inbox"
    d.mkdir()
    (d / "good.txt").write_text("x")
    (d / "ghost.txt").write_text("y")
    calls: list[Path] = []
    be = watcher._PollingBackend([d], calls.append)

    real_key = watcher._mtime_key
    monkeypatch.setattr(
        watcher, "_mtime_key",
        lambda p: None if p.name == "ghost.txt" else real_key(p),
    )
    be._scan()  # must NOT raise; ghost.txt (vanished) is skipped

    assert d / "good.txt" in calls
    assert d / "ghost.txt" not in calls


def test_scan_survives_watched_dir_removed_midscan(tmp_path, monkeypatch):
    d = tmp_path / "inbox"
    d.mkdir()
    (d / "a.txt").write_text("x")
    be = watcher._PollingBackend([d], lambda _p: None)

    def boom_iterdir(self):
        raise FileNotFoundError(2, "dir gone", str(self))

    monkeypatch.setattr(Path, "iterdir", boom_iterdir)
    be._scan()  # dir vanished mid-scan → skipped, no crash


def test_scan_skips_missing_dir_without_error(tmp_path):
    be = watcher._PollingBackend([tmp_path / "does-not-exist"], lambda _p: None)
    be._scan()  # is_dir() False → nothing happens, no crash


# ── _seen is bounded (the leak) ──────────────────────────────────────────────

def test_seen_evicts_routed_away_files(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    f = d / "a.txt"
    f.write_text("x")
    calls: list[Path] = []
    be = watcher._PollingBackend([d], calls.append)

    be._scan()
    assert len(calls) == 1 and len(be._seen) == 1

    f.unlink()  # routed out of the inbox / removed
    be._scan()
    assert be._seen == set()   # bounded — the stale key is evicted, not accumulated
    assert len(calls) == 1     # and not re-fired


def test_scan_does_not_refire_a_stable_file(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    (d / "a.txt").write_text("x")
    calls: list[Path] = []
    be = watcher._PollingBackend([d], calls.append)

    be._scan()
    be._scan()
    assert len(calls) == 1  # unchanged file fires exactly once


def test_dotfiles_are_ignored(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    (d / ".hidden").write_text("x")
    (d / "real.txt").write_text("y")
    calls: list[Path] = []
    watcher._PollingBackend([d], calls.append)._scan()
    assert calls == [d / "real.txt"]


# ── callback errors don't stop the scan ──────────────────────────────────────

def test_callback_error_does_not_stop_the_scan(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    (d / "a.txt").write_text("x")
    (d / "b.txt").write_text("y")
    attempted: list[str] = []

    def cb(p: Path) -> None:
        attempted.append(p.name)
        raise RuntimeError("boom")

    watcher._PollingBackend([d], cb)._scan()  # must not raise
    assert set(attempted) == {"a.txt", "b.txt"}  # both attempted despite each raising
