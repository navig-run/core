"""The idle reaper closes leaks — and REFUSES everything it cannot prove is a leak.

Teardown used to be discipline ("run `navig cdp stop --all` when you're done"), which is
exactly what an unattended cron job does not have. Browsers are spawned DETACHED_PROCESS
and outlive the process that started them, so nothing closed them and ~24 once piled up.

The tests that matter here are the REFUSALS. Closing the operator's browser is not a
recoverable mistake — there is a standing rule in this project against killing Chrome
because a cleanup once shut their real browser and lost their tabs — so every "do not
touch" rule gets its own test.
"""

from __future__ import annotations

import pytest

from navig.browser import targets as t


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Point the launched registry at a temp file and hand back a writer."""
    path = tmp_path / "cdp-launched.json"
    monkeypatch.setattr(t, "_launched_registry_path", lambda: path)

    def _write(entries: dict) -> None:
        t._write_launched(entries)

    return _write


@pytest.fixture
def stopped(monkeypatch):
    """Record which ports stop_launched was asked to close, without closing anything."""
    calls: list[int] = []

    def _fake_stop(port: int) -> dict:
        calls.append(int(port))
        return {"ok": True, "closed": True}

    monkeypatch.setattr(t, "stop_launched", _fake_stop)
    return calls


OLD = 1_000_000.0  # long before `now`
NOW = OLD + 10_000


def _entry(**over):
    base = {
        "pid": 4242,
        "app": "chrome",
        "user_data_dir": r"C:\Users\x\.navig\cdp-profiles\sessions\s1",
        "started": OLD,
        "last_used": OLD,
        "headless": True,
    }
    base.update(over)
    return base


# ── it does reap a genuine leak ───────────────────────────────────────────────


def test_reaps_an_idle_headless_session_browser(registry, stopped):
    registry({"9222": _entry()})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == [9222]
    assert stopped == [9222]


def test_delegates_to_stop_launched_rather_than_killing_directly(registry, stopped):
    """One kill path, not two. stop_launched already verifies identity and PROVES closure."""
    registry({"9222": _entry()})
    t.reap_idle_browsers(60, now=NOW)
    assert stopped == [9222], "the reaper must go through stop_launched"


# ── the refusals ──────────────────────────────────────────────────────────────


def test_refuses_a_named_profile(registry, stopped):
    """Named profiles hold logins done by hand — idleness there is normal, not a leak."""
    registry({"9222": _entry(user_data_dir=r"C:\Users\x\.navig\cdp-profiles\named\research")})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["skipped"]["named_profile"] == 1
    assert stopped == []


def test_refuses_a_browser_with_a_visible_window(registry, stopped):
    """A window on screen means a human asked for it (`navig do`, `cdp login`)."""
    registry({"9222": _entry(headless=False)})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["skipped"]["visible_window"] == 1


def test_refuses_a_browser_that_is_still_fresh(registry, stopped):
    registry({"9222": _entry(last_used=NOW - 5)})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["skipped"]["still_fresh"] == 1


def test_refuses_an_entry_with_no_timestamp(registry, stopped):
    """An unknown age is not 'old'. Never close what you cannot date."""
    entry = _entry()
    del entry["last_used"]
    del entry["started"]
    registry({"9222": entry})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["skipped"]["no_timestamp"] == 1


def test_refuses_a_malformed_entry(registry, stopped):
    registry({"9222": "not-a-dict"})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["skipped"]["malformed"] == 1


def test_a_threshold_of_zero_disables_the_sweep(registry, stopped):
    registry({"9222": _entry()})
    res = t.reap_idle_browsers(0, now=NOW)
    assert res["reaped"] == []
    assert stopped == []


def test_never_looks_outside_the_registry(registry, stopped):
    """A `foreign` browser is not ours. The reaper only ever reads NAVIG's own registry."""
    registry({})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["checked"] == 0
    assert stopped == []


def test_a_failing_stop_is_reported_not_swallowed(registry, monkeypatch):
    def _boom(port: int) -> dict:
        return {"ok": False, "error": "still serving CDP"}

    monkeypatch.setattr(t, "stop_launched", _boom)
    registry({"9222": _entry()})
    res = t.reap_idle_browsers(60, now=NOW)
    assert res["reaped"] == []
    assert res["errors"] and "9222" in res["errors"][0]


def test_an_exception_in_stop_does_not_take_the_daemon_down(registry, monkeypatch):
    def _raise(port: int) -> dict:
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(t, "stop_launched", _raise)
    registry({"9222": _entry()})
    res = t.reap_idle_browsers(60, now=NOW)  # must not raise
    assert res["errors"] and "psutil exploded" in res["errors"][0]


# ── registry round-trip: additive fields must not break old entries ───────────


def test_an_old_entry_without_the_new_fields_still_parses(registry, stopped):
    """Backward compatibility: `cdp-launched.json` outlives upgrades (and reboots)."""
    registry({"9222": {"pid": 1, "app": "chrome", "user_data_dir": "/tmp/s", "started": OLD}})
    res = t.reap_idle_browsers(60, now=NOW)
    # No `headless` key means unknown, which must not read as "a human wanted a window";
    # it falls through to the age check and is reaped like any other stale session.
    assert res["reaped"] == [9222]


def test_touch_launched_updates_last_used(registry):
    registry({"9222": _entry(last_used=OLD)})
    t.touch_launched(9222)
    assert t.get_launched()["9222"]["last_used"] > OLD


def test_touch_launched_on_an_unknown_port_is_a_no_op(registry):
    registry({"9222": _entry()})
    t.touch_launched(9999)  # must not create an entry or raise
    assert set(t.get_launched()) == {"9222"}


def test_record_launched_round_trips_headless(registry):
    t.record_launched(9222, 42, "chrome", "/tmp/s", headless=True)
    assert t.get_launched()["9222"]["headless"] is True
    t.record_launched(9333, 43, "chrome", "/tmp/s", headless=False)
    assert t.get_launched()["9333"]["headless"] is False


def test_record_launched_omits_headless_when_unknown(registry):
    """Absent must stay absent — writing a default would fabricate human intent."""
    t.record_launched(9222, 42, "chrome", "/tmp/s")
    assert "headless" not in t.get_launched()["9222"]


# ── named-profile detection ───────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    (r"C:\Users\x\.navig\cdp-profiles\named\research", True),
    ("/home/x/.navig/cdp-profiles/named/research", True),
    (r"C:\Users\x\.navig\cdp-profiles\sessions\s1788", False),
    (r"C:\Users\x\.navig\cdp-profiles\chrome", False),
    # "named" as a bare component elsewhere must not count — the check is on path parts
    # under cdp-profiles, not a substring search.
    (r"C:\projects\named\thing", False),
    (None, False),
    ("", False),
])
def test_named_profile_detection(path, expected):
    assert t._is_named_profile(path) is expected


# ── a dead entry cleared is not a browser reaped ──────────────────────────────


def test_an_entry_whose_browser_was_already_gone_is_not_counted_as_reaped(registry, monkeypatch):
    """The reaper announces every port in `reaped` as "closed idle browser on port N". An
    idle entry whose browser had ALREADY died came back from stop_launched as ok (the final
    probe was dark), so it was announced as a close nobody performed — a phantom success.
    Clearing the dead entry is right; calling it a reap is not."""
    registry({"9301": _entry(), "9302": _entry()})

    def _fake_stop(port: int) -> dict:
        # 9301 was live and got closed; 9302 was already dark when stop arrived.
        return {"ok": True, "closed": True, "already_closed": port == 9302}

    monkeypatch.setattr(t, "stop_launched", _fake_stop)
    res = t.reap_idle_browsers(60, now=NOW)

    assert res["reaped"] == [9301], "only a browser that was actually running counts as reaped"
    assert res["skipped"].get("already_gone") == 1
    assert 9302 not in res["reaped"]


def test_stop_launched_reports_whether_anything_was_there_to_close(registry, monkeypatch):
    """The field the reaper relies on. A dark port before any action ⇒ already_closed."""
    registry({"9303": _entry()})
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)          # dark throughout
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: False)
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *_a, **_k: [], raising=False)

    res = t.stop_launched(9303)

    assert res["ok"] and res["closed"]
    assert res["already_closed"] is True
    assert "already closed" in res["note"]
    assert "9303" not in t._read_launched(), "the dead entry is still cleared — that part is right"


def test_stop_launched_note_says_closed_when_it_actually_closed(registry, monkeypatch):
    registry({"9304": _entry(app="edge")})
    live = {"v": True}
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: object() if live["v"] else None)
    monkeypatch.setattr(t, "request_browser_close", lambda *_a, **_k: live.update(v=False) or True)
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: False)
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *_a, **_k: [], raising=False)

    res = t.stop_launched(9304)

    assert res["ok"] and res["already_closed"] is False
    assert res["note"] == "closed edge on port 9304"


def test_stop_all_separates_dead_entries_from_real_closes(registry, monkeypatch):
    registry({"9305": _entry(), "9306": _entry()})
    monkeypatch.setattr(t, "stop_launched",
                        lambda port: {"ok": True, "closed": True, "already_closed": port == 9306})
    monkeypatch.setattr(t, "_orphan_debug_browser_pids", lambda: [])

    res = t.stop_all_launched()

    assert res["ok"]
    assert res["closed_ports"] == [9305]
    assert res["already_gone_ports"] == [9306]
    assert "closed 1 browser(s) on port(s) 9305" in res["note"]
    assert "cleared 1 dead registry entry" in res["note"]


def test_stop_all_with_nothing_running_says_so(registry, monkeypatch):
    registry({})
    monkeypatch.setattr(t, "_orphan_debug_browser_pids", lambda: [])
    res = t.stop_all_launched()
    assert res["ok"] and res["closed_ports"] == []
    assert "nothing to close" in res["note"]


# ── a closed throwaway profile is deleted, a named one never ────────────────


@pytest.fixture
def profile_root(tmp_path, monkeypatch):
    root = tmp_path / "cdp-profiles"
    (root / "sessions").mkdir(parents=True)
    (root / "named").mkdir()
    monkeypatch.setattr(t, "_profile_root", lambda: str(root))
    return root


def _closes_cleanly(monkeypatch):
    monkeypatch.setattr(t, "request_browser_close", lambda *_a, **_k: True)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: False)
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *_a, **_k: [], raising=False)


def test_a_closed_throwaway_session_dir_is_deleted(registry, profile_root, monkeypatch):
    """Nothing deleted these: every `cdp new` left ~130 MB under sessions/ once its browser
    closed — five in one half-hour of a harness, 517 MB."""
    udd = profile_root / "sessions" / "s1790009241357"
    udd.mkdir(); (udd / "Default").mkdir(); (udd / "Default" / "Cache").write_bytes(b"x" * 1024)
    registry({"9310": _entry(user_data_dir=str(udd))})
    _closes_cleanly(monkeypatch)
    res = t.stop_launched(9310)
    assert res["ok"]
    assert not udd.exists(), "the throwaway profile dir must go with its browser"


def test_a_named_profile_dir_is_never_deleted_on_close(registry, profile_root, monkeypatch):
    udd = profile_root / "named" / "cybesis"
    udd.mkdir(); (udd / "Default").mkdir(); (udd / "Default" / "Cookies").write_bytes(b"precious")
    registry({"9311": _entry(user_data_dir=str(udd))})
    _closes_cleanly(monkeypatch)
    assert t.stop_launched(9311)["ok"]
    assert (udd / "Default" / "Cookies").read_bytes() == b"precious"


def test_a_dir_outside_the_profile_root_is_never_deleted_on_close(registry, profile_root, monkeypatch, tmp_path):
    """A temp dir, or — the case that must never happen — the operator's real Chrome."""
    udd = tmp_path / "elsewhere" / "sessions" / "s1"   # contains 'sessions' but is not under OUR root
    udd.mkdir(parents=True); (udd / "keep").write_bytes(b"x")
    registry({"9312": _entry(user_data_dir=str(udd))})
    _closes_cleanly(monkeypatch)
    assert t.stop_launched(9312)["ok"]
    assert udd.exists()


def test_the_reap_waits_for_the_browsers_processes_before_deleting(registry, profile_root, monkeypatch):
    """Port dark precedes files released. Deleting under a live renderer fails on Windows;
    the reap must wait for the pids serving this port+dir to vanish (bounded)."""
    udd = profile_root / "sessions" / "s2"; udd.mkdir(); (udd / "f").write_bytes(b"x")
    registry({"9313": _entry(user_data_dir=str(udd))})
    monkeypatch.setattr(t, "request_browser_close", lambda *_a, **_k: True)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: False)
    seen = {"polls": 0}

    def _pids(*_a, **_k):
        seen["polls"] += 1
        return [4242] if seen["polls"] < 3 else []   # gone on the third look

    monkeypatch.setattr(t, "_debug_browser_pids", _pids, raising=False)
    monkeypatch.setattr(t.time, "sleep", lambda _s: None)
    assert t.stop_launched(9313)["ok"]
    assert not udd.exists()
    assert seen["polls"] >= 3, "it deleted before the processes were gone"


def test_a_dir_that_will_not_go_is_left_for_prune_not_an_error(registry, profile_root, monkeypatch):
    udd = profile_root / "sessions" / "s3"; udd.mkdir()
    registry({"9314": _entry(user_data_dir=str(udd))})
    _closes_cleanly(monkeypatch)
    monkeypatch.setattr(t.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("in use")))
    res = t.stop_launched(9314)
    assert res["ok"] and res["closed"], "a stuck profile dir must not fail the close itself"
    assert udd.exists()
