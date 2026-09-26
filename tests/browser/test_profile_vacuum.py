"""Vacuuming a profile — reclaim what Chrome rebuilds, keep every login.

The size of `~/.navig/cdp-profiles` was read as "logins we can't delete" for months. Measured
2026-09-15: of 11.7 GB, **8.4 GB was Chrome's on-device AI model** (`OptGuideOnDeviceModel`,
4,072 MB) downloaded separately into each of two profiles, ~1.6 GB was orphaned dirs, ~0.6 GB
was caches — and the logins were **6 MB and 1 MB**. Deleting the profiles would have thrown
away 7 MB of state to reclaim 11 GB of cache.

`profile_vacuum` removes only an ALLOWLIST of regenerable directories. The tests that matter
are the ones proving what it leaves alone.
"""

from __future__ import annotations

import os

import pytest

from navig.browser import cdp_actions as A

# ⚠ Modern Chrome (≥96) keeps cookies at `Default/Network/Cookies`, NOT `Default/Cookies`.
# The first verification after the real vacuum looked in the old place, saw nothing, and
# read it as "cookies deleted" — the untouched profiles had no `Default/Cookies` either.
# Model BOTH so the fixture guards the layout that actually exists on disk.
_LOGIN_STATE = (
    "Default/Network/Cookies",
    "Default/Network/Trust Tokens",
    "Default/Cookies",
    "Default/Login Data",
    "Default/Web Data",
    "Default/Preferences",
    "Default/Local Storage/leveldb/000003.log",
    "Default/Session Storage/000003.log",
    "Default/IndexedDB/https_example.com_0.indexeddb.leveldb/000003.log",
    "Default/Extensions/abc/1.0/manifest.json",
    "Default/History",
    "Default/Bookmarks",
    "Local State",
)


def _mkprofile(tmp_path, name: str, *, model_mb: int = 4, cache_kb: int = 64):
    """A user-data-dir shaped like the measured one: a big model dir, some caches, and
    small-but-precious login state."""
    d = tmp_path / "cdp-profiles" / "named" / name
    (d / "OptGuideOnDeviceModel" / "2025.8.8.1141").mkdir(parents=True)
    (d / "OptGuideOnDeviceModel" / "2025.8.8.1141" / "weights.bin").write_bytes(b"m" * (model_mb * 1024 * 1024))
    (d / "Default" / "Cache" / "Cache_Data").mkdir(parents=True)
    (d / "Default" / "Cache" / "Cache_Data" / "f_000001").write_bytes(b"c" * (cache_kb * 1024))
    (d / "Default" / "Service Worker" / "CacheStorage" / "x").mkdir(parents=True)
    (d / "Default" / "Service Worker" / "CacheStorage" / "x" / "blob").write_bytes(b"s" * 1024)
    for rel in _LOGIN_STATE:
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"precious")
    return d


@pytest.fixture
def usage(monkeypatch):
    state: dict = {"named": [], "orphans": [], "sessions": [], "root": "/tmp/cdp-profiles",
                   "total_bytes": 0, "ok": True}
    monkeypatch.setattr(A, "profile_usage", lambda: state)
    return state


def _named(name, path, *, real=False, running=False):
    return {"name": name, "user_data_dir": str(path), "bytes": 0, "last_used": 0,
            "real": real, "running": running}


def _login_state_intact(d) -> list[str]:
    return [rel for rel in _LOGIN_STATE if not (d / rel).is_file() or (d / rel).read_bytes() != b"precious"]


# ── what it keeps ─────────────────────────────────────────────────────────────


def test_every_login_file_survives_a_vacuum(usage, tmp_path):
    """THE test. Cookies, Login Data, Local Storage, IndexedDB, Extensions, History,
    Bookmarks, Preferences, Local State — all present and byte-identical afterwards."""
    d = _mkprofile(tmp_path, "cybesis")
    usage["named"].append(_named("cybesis", d))
    r = A.profile_vacuum(["cybesis"], dry_run=False)
    assert r["ok"], r["errors"]
    assert _login_state_intact(d) == [], "a vacuum touched login state"


def test_the_model_and_caches_are_gone_and_the_bytes_are_counted(usage, tmp_path):
    d = _mkprofile(tmp_path, "cybesis", model_mb=4, cache_kb=64)
    usage["named"].append(_named("cybesis", d))
    r = A.profile_vacuum(["cybesis"], dry_run=False)
    assert not (d / "OptGuideOnDeviceModel").exists(), "the 4 GB model dir must go"
    assert not (d / "Default" / "Cache").exists()
    assert not (d / "Default" / "Service Worker" / "CacheStorage").exists()
    assert r["freed_bytes"] == 4 * 1024 * 1024 + 64 * 1024 + 1024
    assert r["per_profile"] == {"cybesis": r["freed_bytes"]}
    assert set(r["deleted"]) == {str(d / "OptGuideOnDeviceModel"), str(d / "Default" / "Cache"),
                                 str(d / "Default" / "Service Worker" / "CacheStorage")}


def test_the_allowlist_never_names_login_state():
    """The list is what gets deleted. It must never grow a login path — pin the shape."""
    forbidden = ("Cookies", "Network", "Login Data", "Web Data", "Preferences", "Local Storage",
                 "Session Storage", "IndexedDB", "Extensions", "History", "Bookmarks",
                 "Local State", "Local Extension Settings", "Sync Data")
    # By path SEGMENT, not substring: `extensions_crx_cache` is the CRX download cache and
    # regenerable; `Default/Extensions` is installed extension code and is not.
    for rel in A._REGENERABLE_DIRS:
        segments = {seg.lower() for seg in rel.split("/")}
        for bad in forbidden:
            assert bad.lower() not in segments, f"{rel!r} would delete login state ({bad})"
    assert "OptGuideOnDeviceModel" in A._REGENERABLE_DIRS, "the 4 GB dir is the whole point"


# ── refusals ──────────────────────────────────────────────────────────────────


def test_refuses_a_real_chrome_profile(usage, tmp_path):
    """Even a cache-only delete is off limits on the operator's real browser: it is very
    likely running, and this module never touches it."""
    d = _mkprofile(tmp_path, "real")
    usage["named"].append(_named("real", d, real=True))
    r = A.profile_vacuum(["real"], dry_run=False)
    assert r["planned"] == []
    assert r["refused"] and "REAL" in r["refused"][0]["why"]
    assert (d / "OptGuideOnDeviceModel").exists(), "nothing may be deleted from a real profile"


def test_refuses_a_running_profile(usage, tmp_path):
    """Chrome holds cache files open; deleting under it corrupts what is left."""
    d = _mkprofile(tmp_path, "live")
    usage["named"].append(_named("live", d, running=True))
    r = A.profile_vacuum(["live"], dry_run=False)
    assert r["planned"] == []
    assert "close it first" in r["refused"][0]["why"]
    assert (d / "OptGuideOnDeviceModel").exists()


def test_all_profiles_skips_the_refused_and_vacuums_the_rest(usage, tmp_path):
    ok = _mkprofile(tmp_path, "ok")
    real = _mkprofile(tmp_path, "real")
    live = _mkprofile(tmp_path, "live")
    usage["named"] += [_named("ok", ok), _named("real", real, real=True), _named("live", live, running=True)]
    r = A.profile_vacuum(all_profiles=True, dry_run=False)
    assert not (ok / "OptGuideOnDeviceModel").exists()
    assert (real / "OptGuideOnDeviceModel").exists()
    assert (live / "OptGuideOnDeviceModel").exists()
    assert {x["name"] for x in r["refused"]} == {"real", "live"}
    assert _login_state_intact(ok) == []


def test_unknown_name_is_reported_not_silently_ignored(usage):
    r = A.profile_vacuum(["nope"], dry_run=True)
    assert r["refused"] == [{"name": "nope", "why": "no such profile (or its dir is gone)"}]


# ── dry run and failure ───────────────────────────────────────────────────────


def test_dry_run_plans_but_deletes_nothing(usage, tmp_path):
    d = _mkprofile(tmp_path, "cybesis")
    usage["named"].append(_named("cybesis", d))
    r = A.profile_vacuum(["cybesis"], dry_run=True)
    assert r["dry_run"] is True
    assert {x["rel"] for x in r["planned"]} == {"OptGuideOnDeviceModel", "Default/Cache",
                                                 "Default/Service Worker/CacheStorage"}
    assert r["deleted"] == [] and r["freed_bytes"] == 0
    assert (d / "OptGuideOnDeviceModel").exists()


def test_an_empty_regenerable_dir_is_not_planned(usage, tmp_path):
    """Nothing to reclaim ⇒ nothing listed; a plan full of 0-byte rows is noise."""
    d = tmp_path / "cdp-profiles" / "named" / "fresh"
    (d / "Default" / "Cache").mkdir(parents=True)
    (d / "Default" / "Cookies").write_bytes(b"precious")
    usage["named"].append(_named("fresh", d))
    r = A.profile_vacuum(["fresh"], dry_run=True)
    assert r["planned"] == []


def test_a_failed_delete_is_reported_not_swallowed(usage, monkeypatch, tmp_path):
    d = _mkprofile(tmp_path, "cybesis")
    usage["named"].append(_named("cybesis", d))

    def _boom(path, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr("shutil.rmtree", _boom)
    r = A.profile_vacuum(["cybesis"], dry_run=False)
    assert r["ok"] is False
    assert r["errors"] and "locked" in r["errors"][0]
    assert r["freed_bytes"] == 0, "bytes must not be counted as freed when the delete failed"


def test_regenerable_bytes_measures_only_the_allowlist(tmp_path):
    d = _mkprofile(tmp_path, "m", model_mb=2, cache_kb=8)
    got = A.regenerable_bytes(str(d))
    assert got == 2 * 1024 * 1024 + 8 * 1024 + 1024
    # the login files are not in it
    assert got < A._dir_size_bytes(str(d))
    assert os.path.isfile(d / "Default" / "Cookies")
