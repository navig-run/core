"""`navig repo gates` — every lane holder classified the way the lane queue sees it.

The view reads the same holder files as scripts/heavy-slot-lib.mjs. Each test pins one
health state against an injected process table, so nothing here inspects real pids.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from navig.commands.repo import GATE_MAX_HOLD_S, GATE_SLOW_S, collect_gates

NOW = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _holder(slot_dir: Path, name: str, pid: int, started: float, repo: str = "navig/wt") -> None:
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / name).write_text(
        json.dumps({"pid": pid, "repo": repo, "label": "ci-local fast", "started": _iso(started)}),
        encoding="utf-8",
    )


def _procs(table: dict[int, dict]):
    return lambda pid: table.get(pid)


def _state(data: dict, pid: int) -> str:
    return next(r["state"] for r in data["gates"] if r.get("pid") == pid)


def test_a_live_holder_is_running_and_counts_its_lane_busy(tmp_path):
    slots = tmp_path / "slots"
    _holder(slots, "slot-0.json", 100, NOW - 600, repo="schema/schematic-2")
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 601}}))
    row = data["gates"][0]
    assert (row["state"], row["project"], row["worktree"], row["minutes"]) == (
        "running", "schema", "schematic-2", 10,
    )  # fmt: skip
    assert data["lanes_busy"] == 1


def test_a_holder_past_the_slow_mark_is_flagged_but_still_busy(tmp_path):
    slots = tmp_path / "slots"
    _holder(slots, "slot-0.json", 100, NOW - GATE_SLOW_S - 60)
    data = collect_gates(
        slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - GATE_SLOW_S - 61}})
    )
    assert _state(data, 100) == "slow"
    assert data["lanes_busy"] == 1


def test_a_dead_holder_frees_its_lane(tmp_path):
    slots = tmp_path / "slots"
    _holder(slots, "slot-0.json", 100, NOW - 600)
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({}))
    assert _state(data, 100) == "dead"
    assert data["lanes_busy"] == 0


def test_a_holder_older_than_the_hold_limit_is_expired_even_if_its_pid_lives(tmp_path):
    slots = tmp_path / "slots"
    _holder(slots, "overflow-0.json", 100, NOW - GATE_MAX_HOLD_S - 60)
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 99999}}))
    assert _state(data, 100) == "expired"
    assert data["overflow_busy"] == 0


def test_a_pid_reused_by_a_younger_process_is_reported_as_blocking_the_lane(tmp_path):
    # The queue's alive() check cannot tell, so the lane stays taken: the one state that
    # needs a human, and the reason the view compares start times.
    slots = tmp_path / "slots"
    _holder(slots, "slot-1.json", 100, NOW - 600)
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 60}}))
    assert _state(data, 100) == "reused"
    assert data["lanes_busy"] == 1


def test_a_marker_without_a_lane_is_queued_but_one_nested_in_a_holder_is_not(tmp_path):
    slots, markers = tmp_path / "slots", tmp_path / "ci-running"
    _holder(slots, "slot-0.json", 100, NOW - 600)
    markers.mkdir()
    for pid in (100, 200, 300, 400):
        (markers / f"{pid}.pid").write_text(str(pid), encoding="utf-8")
    procs = {
        100: {"created": NOW - 601},  # the holder itself: listed once, as its lane
        200: {"created": NOW - 120, "cwd": str(tmp_path / "ci-health"), "ancestors": []},
        300: {"created": NOW - 60, "ancestors": [100]},  # a nested gate inside the holder
        # 400 is gone: its marker is reaped on the next count, not shown
    }
    data = collect_gates(slots, markers, now=NOW, pid_info=_procs(procs))
    assert [r["pid"] for r in data["gates"]] == [100, 200]
    queued = data["gates"][1]
    assert (queued["state"], queued["worktree"], queued["minutes"]) == ("queued", "ci-health", 2)
    assert data["queued"] == 1


def test_a_marker_whose_pid_was_reused_is_not_reported_as_queued(tmp_path):
    markers = tmp_path / "ci-running"
    markers.mkdir()
    marker = markers / "200.pid"
    marker.write_text("200", encoding="utf-8")
    os.utime(marker, (NOW - 600, NOW - 600))

    data = collect_gates(
        tmp_path / "slots",
        markers,
        now=NOW,
        pid_info=_procs({
            200: {
                "created": NOW - 60,
                "cwd": str(tmp_path / "unrelated-program"),
                "ancestors": [],
            }
        }),
    )

    assert data["gates"] == []
    assert data["queued"] == 0


def test_the_current_step_comes_from_the_gates_run_log(tmp_path):
    slots, wt = tmp_path / "slots", tmp_path / "wt"
    _holder(slots, "slot-0.json", 100, NOW - 600)
    (wt / ".dev" / "logs").mkdir(parents=True)
    (wt / ".dev" / "logs" / "ci-local-last-run.json").write_text(
        json.dumps([
            {"step": "ruff — core lint", "state": "pass", "seconds": 1},
            {"step": "source guards", "state": "running", "startedAt": _iso(NOW - 180), "budgetMin": 60},
        ]),
        encoding="utf-8",
    )  # fmt: skip
    data = collect_gates(
        slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 601, "cwd": str(wt)}})
    )
    assert data["gates"][0]["step"] == {"name": "source guards", "minutes": 3, "budget_min": 60}


def _ticket(
    slot_dir: Path, pid: int, since: float, repo: str, *, beat: float = NOW, express: bool = False
) -> Path:
    """A waiter's ticket, as heavy-slot-lib.mjs writes it (since in ms; mtime = heartbeat)."""
    slot_dir.mkdir(parents=True, exist_ok=True)
    f = slot_dir / f"ticket-{int(since * 1000)}-{pid}.json"
    f.write_text(
        json.dumps({"pid": pid, "repo": repo, "label": "ci-local fast", "since": int(since * 1000),
                    "lateAt": int((since + 1800) * 1000), "express": express}),
        encoding="utf-8",
    )  # fmt: skip
    os.utime(f, (beat, beat))
    return f


def test_the_queue_is_read_from_tickets_from_any_directory_and_any_project(tmp_path):
    # The incident: run from a home directory (no git), the marker dir resolved to None and
    # the view printed "0 queued" with 18 jobs waiting — 6 of them Schema's, which no navig
    # marker could ever show. Tickets live beside the lanes, so marker_dir=None is the
    # home-directory case exactly.
    slots = tmp_path / "slots"
    _ticket(slots, 300, NOW - 60, "navig/gate-scheduling")
    _ticket(slots, 200, NOW - 600, "schema/test-speed", express=True)
    procs = {200: {"created": NOW - 601}, 300: {"created": NOW - 61}}
    data = collect_gates(slots, None, now=NOW, pid_info=_procs(procs))
    assert data["queued"] == 2
    # Oldest first: the order the queue serves them.
    assert [(r["pid"], r["project"], r["worktree"], r["minutes"]) for r in data["gates"]] == [
        (200, "schema", "test-speed", 10),
        (300, "navig", "gate-scheduling", 1),
    ]
    assert data["gates"][0]["lane"] == "— (express)"


def test_a_stale_or_orphaned_ticket_is_not_counted_and_not_deleted(tmp_path):
    slots = tmp_path / "slots"
    stale = _ticket(slots, 200, NOW - 600, "navig/a", beat=NOW - 31)  # heartbeat stopped
    gone = _ticket(slots, 300, NOW - 600, "navig/b")  # its waiter died
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({200: {"created": NOW - 601}}))
    assert data["queued"] == 0
    # The queue reaps stale tickets itself; a VIEW that deleted them would be racing it.
    assert stale.exists() and gone.exists()


def test_a_ticket_and_a_marker_for_one_gate_count_once(tmp_path):
    slots, markers = tmp_path / "slots", tmp_path / "ci-running"
    _ticket(slots, 200, NOW - 120, "navig/wt")
    markers.mkdir()
    (markers / "200.pid").write_text("200", encoding="utf-8")
    data = collect_gates(
        slots, markers, now=NOW, pid_info=_procs({200: {"created": NOW - 121, "ancestors": []}})
    )
    assert data["queued"] == 1


def test_a_lane_holder_with_a_leftover_ticket_is_running_not_queued(tmp_path):
    # tryAcquire wins, then dropTicket runs: for a moment both files exist.
    slots = tmp_path / "slots"
    _holder(slots, "slot-0.json", 100, NOW - 1)
    _ticket(slots, 100, NOW - 300, "navig/wt")
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 301}}))
    assert [r["state"] for r in data["gates"]] == ["running"]
    assert data["queued"] == 0


def test_an_express_lane_holder_is_shown_and_counted(tmp_path):
    # heavy-slot-lib.mjs grew an express lane (express-<k>.json); a view that only knew
    # slot-/overflow- files showed nothing at all for a job running in it.
    slots = tmp_path / "slots"
    _holder(slots, "express-0.json", 100, NOW - 60, repo="navig/quick")
    data = collect_gates(slots, None, now=NOW, pid_info=_procs({100: {"created": NOW - 61}}))
    assert [(r["lane"], r["state"]) for r in data["gates"]] == [("express 0", "running")]
    assert (data["express_busy"], data["lanes_busy"]) == (1, 0)


def test_unreadable_holders_and_foreign_files_never_crash_the_view(tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "slot-0.json").write_text("{half a write", encoding="utf-8")
    (slots / "notes.json").write_text("{}", encoding="utf-8")  # not a lane file
    data = collect_gates(slots, tmp_path / "absent", now=NOW, pid_info=_procs({}))
    assert [r["state"] for r in data["gates"]] == ["unreadable"]
    assert (
        collect_gates(tmp_path / "no-such-dir", None, now=NOW, pid_info=_procs({}))["gates"] == []
    )


def test_unreadable_ticket_is_ignored(tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "ticket-1-2.json").write_text("{half", encoding="utf-8")
    assert collect_gates(slots, None, now=NOW, pid_info=_procs({}))["queued"] == 0


# ── the view: typical holds, start estimates, project filter, narrow terminals ──

from navig.commands.repo import estimate_gate_starts, gate_typical_hold  # noqa: E402


def _hist(tmp_path, runs):
    p = tmp_path / "history.jsonl"
    lines = []
    for label, minutes in runs:
        a = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
        b = datetime.fromtimestamp(a.timestamp() + minutes * 60, tz=timezone.utc)
        lines.append(json.dumps({"repo": "navig/x", "label": label, "started": a.isoformat(), "ended": b.isoformat()}))
    p.write_text("\n".join(lines + ["not json"]), encoding="utf-8")
    return p


def test_typical_hold_is_the_median_per_label_and_overall(tmp_path):
    p = _hist(tmp_path, [("ci-local fast", 10), ("ci-local fast", 30), ("ci-local fast", 20), ("pnpm typecheck", 1)])
    per, overall = gate_typical_hold(p)
    assert per["ci-local fast"] == 20 * 60
    assert per["pnpm typecheck"] == 60
    assert overall == 15 * 60, "median of 1,10,20,30 minutes"


def test_typical_hold_without_history_falls_back(tmp_path):
    per, overall = gate_typical_hold(tmp_path / "missing.jsonl")
    assert per == {} and overall == 25 * 60


def test_queued_gates_take_the_earliest_free_lane_in_queue_order():
    now = 10_000.0
    rows = [
        {"kind": "slot", "state": "running", "label": "gate", "since": now - 5 * 60},   # frees in 15 min
        {"kind": "slot", "state": "slow", "label": "gate", "since": now - 60 * 60},     # overdue -> free now
        {"kind": "queued", "state": "queued", "label": "short", "since": now - 100},
        {"kind": "queued", "state": "queued", "label": "gate", "since": now - 50},
        {"kind": "queued", "state": "queued", "label": "gate", "since": now - 10},
    ]
    estimate_gate_starts(rows, lanes=2, holds={"gate": 20 * 60, "short": 60}, fallback=20 * 60, now=now)
    q = [r["eta_s"] for r in rows if r["state"] == "queued"]
    assert q[0] == 0, "an overdue holder means a lane is effectively free"
    assert q[1] == 60, "the short job ahead holds that lane for its own typical minute"
    assert q[2] == 15 * 60, "the third waits for the running gate to finish"
    assert "eta_s" not in rows[0], "only queued rows get an estimate"


def test_free_lanes_without_holders_count(tmp_path):
    rows = [{"kind": "queued", "state": "queued", "label": "g", "since": 1.0}]
    estimate_gate_starts(rows, lanes=3, holds={}, fallback=600, now=100.0)
    assert rows[0]["eta_s"] == 0


def _cli(monkeypatch, tmp_path, args, width=120):
    """Drive `navig repo gates` against a synthetic queue."""
    from typer.testing import CliRunner

    import navig.commands.repo as R

    now = NOW
    data = {
        "gates": [
            {"lane": "slot 0", "kind": "slot", "project": "navig", "worktree": "a-very-long-worktree-name-here",
             "label": "ci-local fast", "pid": 111, "since": now - 600, "minutes": 10, "state": "running",
             "step": {"minutes": 3, "budget_min": 60, "name": "tests for changed modules (99 of 1041 files)"}},
            {"lane": "—", "kind": "queued", "project": "schema", "worktree": "lazy-auth", "label": "api:test",
             "pid": 222, "since": now - 300, "minutes": 5, "state": "queued", "step": None},
            {"lane": "—", "kind": "queued", "project": "navig", "worktree": "go-live", "label": "ci-local fast",
             "pid": 333, "since": now - 200, "minutes": 3, "state": "queued", "step": None},
        ],
        "lanes": 1, "overflow": 0, "express": 0, "lanes_busy": 1, "overflow_busy": 0, "express_busy": 0, "queued": 2,
    }
    monkeypatch.setattr(R, "collect_gates", lambda *a, **k: json.loads(json.dumps(data)))
    monkeypatch.setattr(R, "_gate_slot_dir", lambda: tmp_path)
    monkeypatch.setattr(R, "resolve_repo_root", lambda repo: None)
    # Width is set on the console ONLY — never by narrowing COLUMNS. Rich reads COLUMNS once,
    # when a Console is created, and pins it; the shared console is lazy, so when this was the
    # first test in a worker to touch it, `setenv("COLUMNS", "80")` created it pinned at 80,
    # monkeypatch recorded 80 as the "old" width, and undo restored 80 — every later test in
    # that worker then wrapped at 80 (three red land gates, a different victim each time).
    from navig import console_helper as ch

    # Patch the REAL console's width, not the lazy proxy. `monkeypatch.setattr(ch.console,
    # "width", …)` reads the old value through `_LazyConsole.__getattr__`, which LOADS a
    # console that had not been created yet, and then its teardown deletes the attribute --
    # Rich's `width` is a property with no deleter, so every test here ended in
    # `AttributeError: property 'width' … has no deleter` once the console had not been
    # created before the patch (2026-10-30, outpost). Creating it first keeps the patch a
    # plain set-and-restore of an int on a live object.
    real = object.__getattribute__(ch.console, "_real") or ch.console._load()
    monkeypatch.setattr(real, "width", width)
    return CliRunner().invoke(R.repo_app, ["gates", *args])


def test_a_project_name_filters_the_view_and_keeps_queue_positions(monkeypatch, tmp_path):
    r = _cli(monkeypatch, tmp_path, ["schema", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert [g["project"] for g in data["gates"]] == ["schema"]
    assert data["gates"][0]["position"] == 1, "the position is in the WHOLE queue, not the filtered list"
    assert data["filter"] == "schema"


def test_the_filter_is_case_insensitive(monkeypatch, tmp_path):
    r = _cli(monkeypatch, tmp_path, ["NAVIG", "--json"])
    assert {g["project"] for g in json.loads(r.output)["gates"]} == {"navig"}


def test_a_normal_terminal_shows_every_column_whole(monkeypatch, tmp_path):
    # 100 columns with a long Step cell: proportional shrinking cuts "Project" to "Pr…"
    # here; sizing every column to its widest cell and giving Step the rest does not.
    r = _cli(monkeypatch, tmp_path, [], width=100)
    assert r.exit_code == 0, r.output
    header = r.output.splitlines()[0]
    for col in ("#", "Project", "Worktree", "Gate", "State", "Since", "Min", "ETA", "PID", "Step"):
        assert col in header, f"{col!r} missing or cut in: {header!r}"
    assert "Pr…" not in r.output


def test_a_narrow_terminal_drops_whole_columns_instead_of_shaving_them(monkeypatch, tmp_path):
    r = _cli(monkeypatch, tmp_path, [], width=80)
    assert r.exit_code == 0, r.output
    header = r.output.splitlines()[0]
    assert "Project" in header and "State" in header and "ETA" in header
    assert "PID" not in header, "PID is the first column a narrow window gives up"


def test_watch_and_json_are_refused_together(monkeypatch, tmp_path):
    r = _cli(monkeypatch, tmp_path, ["--json", "--watch", "2"])
    assert r.exit_code == 2


def test_an_express_waiter_gets_the_express_lane_before_the_main_pool():
    """An express waiter has TWO doors; quoting it only the main lane's wait overstates it.

    heavy-slot-lib.mjs serves the express lanes to express waiters only (counting express
    tickets ahead), and prefers a free main lane to keep express open. So when express is
    idle its ETA is ~0 however long the main pool still holds.
    """
    now = 1000.0
    running = {"kind": "slot", "state": "running", "label": "big", "since": now - 100}
    express_q = {"kind": "queued", "state": "queued", "label": "g", "since": now - 10, "express": True}
    plain_q = {"kind": "queued", "state": "queued", "label": "g", "since": now - 10}
    holds = {"big": 1100.0, "g": 600.0}  # the running gate frees the main lane in 1000 s

    rows = [running, express_q, plain_q]
    estimate_gate_starts(rows, lanes=1, holds=holds, fallback=600, now=now, express=1)
    assert rows[1]["eta_s"] == 0, "the express lane is free — this waiter does not wait"
    assert rows[2]["eta_s"] == 1000, "the non-express waiter still waits on the main lane"

    # Express lane busy: the express door is shut, so it falls back to the main pool.
    busy_express = {"kind": "express", "state": "running", "label": "x", "since": now - 10}
    rows = [running, busy_express, express_q]
    estimate_gate_starts(rows, lanes=1, holds=holds, fallback=600, now=now, express=1)
    assert rows[2]["eta_s"] == 1000, "no free express lane → wait with everyone else"

    # Capacity off: the parameter must not conjure a lane that does not exist.
    rows = [running, express_q]
    estimate_gate_starts(rows, lanes=1, holds=holds, fallback=600, now=now, express=0)
    assert rows[1]["eta_s"] == 1000


def test_a_queued_express_ticket_carries_its_express_flag(tmp_path):
    """The flag is a row field, not re-parsed out of the `lane` cell — the ETA model reads it."""
    slots = tmp_path / "slots"
    _ticket(slots, 400, NOW - 30, "navig/fast", express=True)
    _ticket(slots, 401, NOW - 30, "navig/slow")
    procs = {400: {"created": NOW - 31}, 401: {"created": NOW - 31}}
    data = collect_gates(slots, None, now=NOW, pid_info=_procs(procs))
    flags = {r["pid"]: r["express"] for r in data["gates"]}
    assert flags == {400: True, 401: False}


def test_a_filter_that_matches_nothing_names_the_project_once_and_fits_the_width(monkeypatch, tmp_path):
    """The empty message repeated the project and then wrapped at 120 columns."""
    r = _cli(monkeypatch, tmp_path, ["nosuchproject"], width=120)
    assert r.exit_code == 0, r.output
    body = r.output.replace("\r", "")
    assert body.count("nosuchproject") == 1, f"named twice:\n{body}"
    assert "No gates for nosuchproject running or queued." in body
    for line in body.splitlines():
        assert len(line) <= 120, f"wrapped at 120 columns: {line!r}"


def test_the_narrow_view_never_pins_the_shared_console_for_later_tests(tmp_path):
    """The narrow-terminal test must not leave the shared console narrow.

    Rich reads COLUMNS once, at Console creation, and the shared console is lazy — so when
    this view was the FIRST thing in a worker to touch it, a narrowed COLUMNS pinned it and
    the next test in that worker wrapped at 80 (`'holds main' in out` failed on a correct
    table, three red land gates, a different victim each time). The width is now set on the
    Console object, so the invariant is exact: what the shared console had before a narrow
    run, it still has after one.
    """
    from navig import console_helper as ch

    before = ch.console.width
    with pytest.MonkeyPatch.context() as mp:
        r = _cli(mp, tmp_path, [], width=80)
        assert r.exit_code == 0, r.output
    assert ch.console.width == before, (
        f"the narrow view left the shared console at {ch.console.width} instead of {before}"
    )
