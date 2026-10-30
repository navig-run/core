r"""Disk collection must never hang — and must not come back empty on first use.

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


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-type filtering")
def test_enumeration_without_listdrives_uses_the_drive_bitmask(monkeypatch):
    """Review #139: Python 3.10/3.11 has no os.listdrives(); it fell back to the
    disk_partitions() call that hangs on a cold mapped drive."""
    import ctypes

    monkeypatch.delattr(os, "listdrives", raising=False)

    def _never(*a, **kw):
        raise AssertionError("must not call disk_partitions() on Windows")

    monkeypatch.setattr(monitor.psutil, "disk_partitions", _never)
    # C: and N: present (bits 2 and 13); N: is a network drive.
    monkeypatch.setattr(ctypes.windll.kernel32, "GetLogicalDrives",
                        lambda: (1 << 2) | (1 << 13), raising=False)
    monkeypatch.setattr(
        ctypes.windll.kernel32, "GetDriveTypeW",
        lambda d: 4 if str(d).startswith("N") else monitor._DRIVE_FIXED, raising=False,
    )
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


# ── top processes are cached (the System app's list; ~280 ms per walk) ────────


def test_top_processes_walk_is_cached(monkeypatch):
    from navig.commands import monitor as m

    calls = []

    class _P:
        def __init__(self, pid):
            self.info = {"pid": pid, "name": f"p{pid}", "cpu_percent": float(pid)}

    def walk(attrs):
        calls.append(1)
        return [_P(i) for i in range(10)]

    monkeypatch.setattr(m.psutil, "process_iter", walk)
    monkeypatch.setattr(m, "_top_cache", None)
    first = m._top_processes(5)
    second = m._top_processes(5)
    assert [p["pid"] for p in first] == [9, 8, 7, 6, 5]
    assert second == first and len(calls) == 1, "a second read within the TTL must not walk again"

    second[0]["name"] = "mutated"
    assert m._top_processes(5)[0]["name"] == "p9", "callers get copies, not the cache"

    assert len(m._top_processes(8)) == 8 and len(calls) == 2, "asking for MORE rows re-walks"

    monkeypatch.setattr(m, "_top_cache", (m.time.monotonic() - m._TOP_TTL_S - 1, 8, []))
    m._top_processes(5)
    assert len(calls) == 3, "an expired entry re-walks"
