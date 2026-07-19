"""Disk collection must never hang — and must not come back empty on first use.

`psutil.disk_partitions()` blocks FOREVER on a cold mapped network drive
(measured on the operator's machine: U:\ never returned) and does not release
the GIL, so calling it anywhere in the daemon froze the whole gateway. Windows
now enumerates drive letters itself (a mount-table lookup) and skips the drive
types whose probe hangs — REMOTE (network) and CDROM.

The list is refreshed in a pool, but the FIRST call waits a bounded moment for
it: returning [] there is what made "no disks" the honest-looking answer on a
freshly started daemon.
"""

from __future__ import annotations

import os

import pytest

from navig.commands import monitor

pytestmark = pytest.mark.skipif(
    not monitor._psutil_available(), reason="psutil not installed"
)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(monitor, "_partitions_cache", None)
    monkeypatch.setattr(monitor, "_partitions_future", None)
    yield


def test_first_call_is_not_empty():
    """The bug this closes: the first call returned [] (the scan was still in the
    pool), so a freshly restarted daemon reported "no disks" to every surface."""
    disks = monitor.get_disk_info()
    assert disks, "the first get_disk_info() call must not come back empty"
    assert any(d["is_system"] for d in disks), "the system drive must be in the list"
    for d in disks:
        assert 0.0 <= d["percent"] <= 100.0
        assert d["total_gb"] >= 0


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-type filtering")
def test_windows_enumeration_skips_the_drive_types_that_hang(monkeypatch):
    """REMOTE (network) and CDROM are exactly the drives whose usage probe hangs —
    they must be filtered out BEFORE anything touches them."""
    import ctypes

    kinds = {
        "C:\\": monitor._DRIVE_FIXED,
        "N:\\": monitor._DRIVE_REMOVABLE,
        "U:\\": 4,  # DRIVE_REMOTE — the one that hung this machine
        "Z:\\": 5,  # DRIVE_CDROM
    }
    monkeypatch.setattr(os, "listdrives", lambda: list(kinds))
    monkeypatch.setattr(
        ctypes.windll.kernel32, "GetDriveTypeW", lambda d: kinds[str(d)], raising=False
    )

    def _never(*a, **kw):
        raise AssertionError("disk_partitions() must not be called on Windows")

    monkeypatch.setattr(monitor.psutil, "disk_partitions", _never)

    mounts = [p.mountpoint for p in monitor._enumerate_partitions()]
    assert mounts == ["C:\\", "N:\\"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-type filtering")
def test_enumeration_survives_a_drive_that_errors(monkeypatch):
    import ctypes

    monkeypatch.setattr(os, "listdrives", lambda: ["C:\\", "Q:\\"])

    def _get_type(drive):
        if str(drive) == "Q:\\":
            raise OSError("device not ready")
        return monitor._DRIVE_FIXED

    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", _get_type, raising=False)
    mounts = [p.mountpoint for p in monitor._enumerate_partitions()]
    assert mounts == ["C:\\"]


def test_system_disk_path_needs_no_enumeration(monkeypatch):
    """get_system_disk is the fast headline path — it must never enumerate."""

    def _never(*a, **kw):
        raise AssertionError("get_system_disk() must not call disk_partitions()")

    monkeypatch.setattr(monitor.psutil, "disk_partitions", _never)
    rows = monitor.get_system_disk()
    assert len(rows) == 1
    assert rows[0]["is_system"] is True
