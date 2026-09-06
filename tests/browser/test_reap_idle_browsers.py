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
