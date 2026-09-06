"""Reclaiming profile disk — and refusing everything that isn't clearly disposable.

`~/.navig/cdp-profiles` reached **13 GB** on the machine this was written for. A browser
profile is a full Chrome user-data dir (caches, service workers, IndexedDB), so it grows
without bound and silently.

Deleting one destroys logins, so the tests that matter are the REFUSALS — above all the
`real` profile, whose `user_data_dir` points at the operator's ACTUAL Chrome data
directory. Deleting that would take their real browser's history, cookies and saved
passwords with it.
"""

from __future__ import annotations

import pytest

from navig.browser import cdp_actions as A


def _mkprofile(tmp_path, name: str, size: int = 2048):
    d = tmp_path / "cdp-profiles" / "named" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob.bin").write_bytes(b"x" * size)
    return d


@pytest.fixture
def usage(monkeypatch):
    """Drive profile_prune from a synthetic usage report."""
    state: dict = {"named": [], "orphans": [], "sessions": [], "root": "/tmp/cdp-profiles",
                   "total_bytes": 0, "ok": True}
    monkeypatch.setattr(A, "profile_usage", lambda: state)
    monkeypatch.setattr(A, "_launched_entries", lambda: [])
    return state


def _named(name, path, *, real=False, running=False, size=1024):
    return {"name": name, "user_data_dir": path, "bytes": size, "last_used": 0,
            "real": real, "running": running}


# ── refusals ──────────────────────────────────────────────────────────────────


def test_refuses_a_real_chrome_profile(usage, tmp_path):
    """The one that would destroy the operator's actual browser. No override exists."""
    d = _mkprofile(tmp_path, "myrealchrome")
    usage["named"] = [_named("myrealchrome", str(d), real=True)]
    res = A.profile_prune(["myrealchrome"], sessions=False, dry_run=True)
    assert res["planned"] == []
    assert "REAL Chrome data" in res["refused"][0]["why"]
    assert d.exists()


def test_a_real_profile_is_refused_even_when_not_dry_run(usage, tmp_path):
    """The refusal must live in the planner, not in the confirmation prompt."""
    d = _mkprofile(tmp_path, "myrealchrome")
    usage["named"] = [_named("myrealchrome", str(d), real=True)]
    res = A.profile_prune(["myrealchrome"], sessions=False, dry_run=False)
    assert res["deleted"] == []
    assert d.exists(), "a real Chrome profile must survive a non-dry-run prune"


def test_refuses_a_running_profile(usage, tmp_path):
    d = _mkprofile(tmp_path, "busy")
    usage["named"] = [_named("busy", str(d), running=True)]
    res = A.profile_prune(["busy"], sessions=False, dry_run=False)
    assert res["deleted"] == []
    assert "currently running" in res["refused"][0]["why"]
    assert d.exists()


def test_never_selects_a_named_profile_that_was_not_asked_for(usage, tmp_path):
    """'Looks unused' is not consent — a profile holds logins."""
    d = _mkprofile(tmp_path, "research")
    usage["named"] = [_named("research", str(d))]
    res = A.profile_prune([], sessions=False, dry_run=True)
    assert res["planned"] == []
    assert d.exists()


def test_refuses_a_session_dir_a_launched_browser_is_using(usage, monkeypatch, tmp_path):
    """37 sessions can be live at once; deleting under one corrupts what is left."""
    live = tmp_path / "cdp-profiles" / "sessions" / "s1"
    live.mkdir(parents=True)
    usage["sessions"] = [{"path": str(live), "bytes": 10, "mtime": 0}]
    monkeypatch.setattr(A, "_launched_entries", lambda: [{"user_data_dir": str(live)}])
    res = A.profile_prune([], sessions=True, dry_run=False)
    assert res["deleted"] == []
    assert "a launched browser is using it" in res["refused"][0]["why"]
    assert live.exists()


def test_unknown_name_is_reported_not_silently_ignored(usage):
    res = A.profile_prune(["ghost"], sessions=False, dry_run=True)
    assert res["planned"] == []
    assert res["refused"][0]["name"] == "ghost"


# ── the things it does do ─────────────────────────────────────────────────────


def test_dry_run_deletes_nothing(usage, tmp_path):
    d = _mkprofile(tmp_path, "spare")
    usage["named"] = [_named("spare", str(d))]
    res = A.profile_prune(["spare"], sessions=False, dry_run=True)
    assert res["planned"][0]["name"] == "spare"
    assert res["deleted"] == [] and res["freed_bytes"] == 0
    assert d.exists(), "a dry run must not touch the disk"


def test_deletes_a_named_profile_when_explicitly_asked(usage, monkeypatch, tmp_path):
    d = _mkprofile(tmp_path, "spare", size=4096)
    usage["named"] = [_named("spare", str(d), size=4096)]
    removed: list[str] = []
    monkeypatch.setattr("navig.browser.profiles.remove_profile",
                        lambda n: removed.append(n) or True)
    res = A.profile_prune(["spare"], sessions=False, dry_run=False)
    assert not d.exists()
    assert res["freed_bytes"] == 4096
    assert removed == ["spare"], "the registry entry must go with the bytes"


def test_sweeps_a_free_session_dir(usage, tmp_path):
    s = tmp_path / "cdp-profiles" / "sessions" / "s2"
    s.mkdir(parents=True)
    (s / "f").write_bytes(b"y" * 512)
    usage["sessions"] = [{"path": str(s), "bytes": 512, "mtime": 0}]
    res = A.profile_prune([], sessions=True, dry_run=False)
    assert not s.exists()
    assert res["freed_bytes"] == 512


def test_an_orphan_dir_can_be_pruned_by_name(usage, tmp_path):
    """A dir under named/ with no registry entry — invisible before, and 1.6 GB of it."""
    d = _mkprofile(tmp_path, "leftover", size=2048)
    usage["orphans"] = [{"name": "leftover", "path": str(d), "bytes": 2048, "mtime": 0}]
    res = A.profile_prune(["leftover"], sessions=False, dry_run=False)
    assert not d.exists()
    assert res["freed_bytes"] == 2048
    assert not res["refused"], "an orphan that exists must not also be reported missing"


def test_a_failed_delete_is_reported_not_swallowed(usage, monkeypatch, tmp_path):
    d = _mkprofile(tmp_path, "locked")
    usage["named"] = [_named("locked", str(d))]

    def _boom(path):
        raise OSError("file in use by another process")

    monkeypatch.setattr("shutil.rmtree", _boom)
    res = A.profile_prune(["locked"], sessions=False, dry_run=False)
    assert res["ok"] is False
    assert "file in use" in res["errors"][0]
    assert res["freed_bytes"] == 0
