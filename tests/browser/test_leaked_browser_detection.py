"""Leaked debug browsers were invisible — nothing ever looked for them.

That invisibility is the whole reason ~24 of them (and 3.6 GB of profiles) once piled
up before anyone noticed:

  * A leaked browser renders NO page — the harness opens its content in a tab it then
    closes — so all you see is a blank window. Headless, you see nothing at all.
  * Port scanning cannot find them: a browser launched with
    `--remote-debugging-port=0` holds an ephemeral port nobody can guess. This is not
    hypothetical — the orphan found on this machine was exactly that.

So the only thing that can see them is a process scan, and it has to classify what it
finds, because the three kinds must be treated differently:

    tracked — ours, in the registry: a live session. Leave it.
    orphan  — ours, not in the registry: leaked. `cdp stop --all` reclaims it.
    foreign — someone else's harness. REPORT it, never kill it: it isn't ours, and
              another tool may still be driving it.
"""

from __future__ import annotations

import pytest

from navig.browser import targets as t


class _P:
    def __init__(self, pid, cmdline, name="chrome.exe"):
        self.info = {"pid": pid, "cmdline": cmdline, "name": name}


@pytest.fixture()
def scan(monkeypatch, tmp_path):
    """Drive list_debug_browsers over a fake process table + isolated registry."""
    registry = tmp_path / "cdp-launched.json"
    monkeypatch.setattr(t, "_launched_registry_path", lambda: registry)
    monkeypatch.setattr(t, "_profile_root", lambda: "/home/me/.navig/cdp-profiles")

    def _install(procs):
        fake = type("psutil", (), {"process_iter": staticmethod(lambda _a: procs)})
        monkeypatch.setitem(__import__("sys").modules, "psutil", fake)
        return t.list_debug_browsers()

    return _install


NAVIG_PROFILE = "/home/me/.navig/cdp-profiles/sessions/123"


def test_the_operators_own_browser_is_never_listed(scan):
    """Their real Chrome has no debug port. It must be invisible to this code — if it
    weren't, `cdp stop --all` could close the browser they are working in."""
    found = scan([_P(1, ["chrome.exe"]), _P(2, ["chrome.exe", "--profile-directory=Default"])])
    assert found == []


def test_a_navig_browser_in_the_registry_is_tracked(scan, tmp_path):
    t.record_launched(9222, pid=10, app="chrome", user_data_dir=NAVIG_PROFILE)
    found = scan([
        _P(10, ["chrome.exe", "--remote-debugging-port=9222", f"--user-data-dir={NAVIG_PROFILE}"])
    ])
    assert [b["kind"] for b in found] == ["tracked"]


def test_a_navig_browser_missing_from_the_registry_is_an_orphan(scan):
    """The exact residue of the old `stop`: killed the entry, not the browser."""
    found = scan([
        _P(10, ["chrome.exe", "--remote-debugging-port=9222", f"--user-data-dir={NAVIG_PROFILE}"])
    ])
    assert [b["kind"] for b in found] == ["orphan"]
    assert t._orphan_debug_browser_pids() == [10]  # reclaimable by `cdp stop --all`


def test_a_browser_from_another_harness_is_foreign_and_never_reclaimed(scan):
    """The real orphan on this machine: headless, ephemeral port, Temp profile, dead
    parent — a hand-rolled spawn that never reaped its child. NAVIG must SEE it and
    must NOT kill it."""
    found = scan([
        _P(77, [
            "chrome.exe",
            "--remote-debugging-port=0",
            "--user-data-dir=C:/Users/me/AppData/Local/Temp/cdp-profile-1vIU4F",
            "--headless=new",
        ])
    ])
    assert [b["kind"] for b in found] == ["foreign"]
    assert found[0]["headless"] is True
    assert found[0]["port"] == 0, "an ephemeral port — which is why no port scan finds it"

    # It is visible, but NOT in the reclaim set: navig only kills what navig launched.
    assert t._orphan_debug_browser_pids() == []


def test_child_processes_are_not_reported_as_browsers(scan):
    """Renderer/GPU children carry the debug port too; they die with their parent and
    reporting them would inflate every count."""
    found = scan([
        _P(10, ["chrome.exe", "--remote-debugging-port=9222", f"--user-data-dir={NAVIG_PROFILE}"]),
        _P(11, ["chrome.exe", "--remote-debugging-port=9222", "--type=renderer"]),
        _P(12, ["chrome.exe", "--remote-debugging-port=9222", "--type=gpu-process"]),
    ])
    assert [b["pid"] for b in found] == [10]


def test_doctor_stays_quiet_when_there_is_nothing_to_report(monkeypatch):
    from navig.commands.doctor import check_browsers

    monkeypatch.setattr(t, "list_debug_browsers", lambda: [])
    assert check_browsers() == [], "doctor must not add a section it has nothing to say in"


def test_doctor_warns_about_leaks_and_only_notes_foreign_ones(monkeypatch):
    from navig.commands.doctor import check_browsers

    monkeypatch.setattr(
        t,
        "list_debug_browsers",
        lambda: [
            {"pid": 10, "port": 9222, "kind": "orphan", "profile": "p", "headless": False},
            {"pid": 77, "port": 0, "kind": "foreign", "profile": "q", "headless": True},
        ],
    )
    rows = check_browsers()
    text = " ".join(r[2] for r in rows)

    leaked = [r for r in rows if "Leaked" in r[2]]
    assert leaked and leaked[0][1] is False, "a browser NAVIG leaked must be flagged"
    assert "navig cdp stop --all" in text, "tell the user how to reclaim it"

    foreign = [r for r in rows if "Foreign" in r[2]]
    assert foreign and foreign[0][1] is True, "someone else's browser is not our failure"
    assert "left untouched" in text


# ── `cdp status` must not go silent about a browser that is running ────────────────
#
# `discover_targets` probes for ATTACHABLE endpoints, so it cannot see a headless browser
# or one on the ephemeral port `--remote-debugging-port=0` takes. The leak list only ever
# showed kind != "tracked". A browser that is tracked AND live AND undiscoverable — the
# exact thing `navig cdp new --headless` produces, and what os-harness-shot drives — was
# therefore in NEITHER list, and `status` printed "No live CDP targets" while it ran.
#
# That is the reassuring-silence failure: CLAUDE.md tells agents to check `cdp status`
# before touching browsers precisely so they don't disturb another session's, and the
# command answered "nothing here".


def _status_output(monkeypatch, capsys, *, targets, browsers):
    from navig.commands.cdp import cdp_status

    monkeypatch.setattr(t, "discover_targets", lambda: targets)
    monkeypatch.setattr(t, "list_debug_browsers", lambda: browsers)
    monkeypatch.setattr(t, "platform_name", lambda: "TestOS")
    monkeypatch.setattr(t, "known_app_ids", lambda: ["chrome"])
    cdp_status(json_out=False)
    return capsys.readouterr().out


TRACKED_LIVE = {"pid": 10, "port": 30187, "kind": "tracked", "profile": "p", "headless": True}


def test_status_reports_a_tracked_live_browser_it_cannot_discover(monkeypatch, capsys):
    out = _status_output(monkeypatch, capsys, targets=[], browsers=[TRACKED_LIVE])

    assert "30187" in out, "a browser that is RUNNING must appear somewhere in status"
    assert "No live CDP targets" not in out, (
        "saying 'no targets' while one is alive is the silence that gets another "
        "session's browser killed"
    )


def test_status_still_says_nothing_is_running_when_nothing_is(monkeypatch, capsys):
    """Anti-vacuity partner: the assertion above must be able to fail."""
    out = _status_output(monkeypatch, capsys, targets=[], browsers=[])

    assert "No live CDP targets" in out
    assert "30187" not in out


def test_a_discoverable_tracked_browser_is_not_listed_twice(monkeypatch, capsys):
    """It already has a row in the targets table; repeating it below reads as a second
    browser, which is how a count becomes untrustworthy."""
    target = type("T", (), {
        "port": 30187, "tabs": [], "attachable": True, "kind": "browser", "browser": "chrome",
    })()
    out = _status_output(monkeypatch, capsys, targets=[target], browsers=[TRACKED_LIVE])

    assert out.count("30187") == 1
    assert "NAVIG-launched browser(s) running" not in out


def test_status_still_flags_a_leak_when_a_tracked_browser_is_also_running(monkeypatch, capsys):
    """The tracked row must not dilute the warning — a leak is still a leak."""
    orphan = {"pid": 77, "port": 9222, "kind": "orphan", "profile": "q", "headless": False}
    out = _status_output(monkeypatch, capsys, targets=[], browsers=[TRACKED_LIVE, orphan])

    assert "never closed" in out, "a leaked browser must still be warned about"
    assert "navig cdp stop --all" in out
    assert "30187" in out and "9222" in out
