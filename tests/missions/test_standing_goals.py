"""Standing goals: an objective worked on a schedule, one mission per cycle, until met.

Pins the parts that make it safe to leave running: a cycle is claimed atomically in
the goal's own row (so cron + a manual run + a second daemon cannot double-run it),
it always runs AUTO under the read-only grant (never a per-cycle approval page), it
ends by itself (DONE / max cycles / repeated failure) and removes its own schedule,
and an operator's pause during a cycle is not undone by the cycle finishing.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from navig.contracts.mission import MissionStatus
from navig.contracts.store import RuntimeStore
from navig.missions import standing_goals as sg
from navig.store.board import BoardStore


@pytest.fixture
def board(tmp_path):
    return BoardStore(tmp_path / "board.db")


@pytest.fixture
def calls(monkeypatch):
    """Record schedule/unschedule/notify instead of touching the live cron store."""
    rec: dict[str, list] = {"unschedule": [], "schedule": [], "digest": []}
    monkeypatch.setattr(sg, "unschedule", lambda gid: rec["unschedule"].append(gid) or 1)
    monkeypatch.setattr(sg, "schedule", lambda goal: rec["schedule"].append(goal["id"]) or {"id": "job_1"})

    async def fake_dispatch(type_key, title, body="", **kw):
        rec["digest"].append({"type": type_key, "title": title, "body": body, **kw})
        return {}

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)
    return rec


class FakeExecutor:
    """Stands in for MissionExecutor: records the mission, finishes it with `outcome`."""

    def __init__(self, tmp_path, outcomes):
        self.store = RuntimeStore(tmp_path / "rt")
        self.outcomes = list(outcomes)
        self.missions = []

    async def run_to_completion(self, mission):
        self.missions.append(mission)
        self.store.create_mission(mission)
        mission.start()
        kind, text = self.outcomes.pop(0)
        if kind == "raise":
            raise RuntimeError(text)
        self.store.complete_mission(
            mission.mission_id, succeeded=(kind == "ok"),
            result=text if kind == "ok" else None, error=None if kind == "ok" else text,
        )
        return mission


def _goal(board, **kw):
    kw.setdefault("objective", "keep disk usage under 80%")
    kw.setdefault("every", "every 1 hours")
    return board.create_pursuit(kw.pop("title", "disk"), **kw)


# ── store ────────────────────────────────────────────────────────────────────


def test_v4_database_migrates_and_keeps_its_goals(tmp_path):
    db = tmp_path / "board.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version VALUES (4);
        CREATE TABLE board_goal (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#6366f1', space TEXT,
            status TEXT NOT NULL DEFAULT 'active', sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO board_goal(id, title, created_at, updated_at)
            VALUES ('g1', 'Ship v2', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    store = BoardStore(db)

    # Actual == declared, with no trailing literal: a pinned `== 5` went red on main when
    # v5 -> v6 landed. A v4 database must still reach whatever the current version is.
    assert store.get_schema_version() == BoardStore.SCHEMA_VERSION
    old = store.get_goal("g1")
    assert old["title"] == "Ship v2"
    assert old["objective"] is None and old["cycles_run"] == 0
    assert store.list_pursuits() == []  # a plain board goal is not a standing goal
    # Re-opening an already-migrated database is a no-op, not a duplicate-column crash.
    assert BoardStore(db).get_goal("g1")["title"] == "Ship v2"


def test_generic_goal_patch_cannot_touch_pursuit_fields(board):
    goal = _goal(board)
    board.update_goal(goal["id"], {"grant_scope": "full", "cycles_run": 0, "max_cycles": 9999})
    after = board.get_goal(goal["id"])
    assert after["grant_scope"] == "readonly"
    assert after["max_cycles"] == 24


def test_a_cycle_can_be_claimed_once(board):
    goal = _goal(board)
    now = datetime.now(UTC)
    stamp, stale = sg._iso(now), sg._iso(now - sg.CLAIM_STALE_AFTER)

    assert board.claim_pursuit_cycle(goal["id"], now=stamp, stale_before=stale) is True
    assert board.claim_pursuit_cycle(goal["id"], now=stamp, stale_before=stale) is False


def test_a_claim_left_by_a_dead_run_can_be_taken_over(board):
    goal = _goal(board)
    long_ago = datetime.now(UTC) - timedelta(hours=3)
    assert board.claim_pursuit_cycle(goal["id"], now=sg._iso(long_ago), stale_before=sg._iso(long_ago - timedelta(hours=1)))

    now = datetime.now(UTC)
    assert board.claim_pursuit_cycle(goal["id"], now=sg._iso(now), stale_before=sg._iso(now - sg.CLAIM_STALE_AFTER))


def test_a_paused_goal_cannot_be_claimed(board):
    goal = _goal(board)
    board.set_pursuit_state(goal["id"], "paused")
    now = datetime.now(UTC)
    assert not board.claim_pursuit_cycle(goal["id"], now=sg._iso(now), stale_before=sg._iso(now))


# ── verdict parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,done",
    [
        ("All three drives are under 60%.\nSTATUS: DONE", True),
        ("Still at 85%.\n\nSTATUS: CONTINUE", False),
        ("report\n**STATUS: DONE**", True),
        ("report\n`STATUS: done`", True),
        ("I will say STATUS: DONE later\nSTATUS: CONTINUE", False),  # the LAST line wins
        ("no status line at all", False),  # silence continues; never a silent stop
    ],
)
def test_parse_status(text, done):
    got_done, _report = sg.parse_status(text)
    assert got_done is done


def test_parse_status_strips_the_status_line_from_the_report():
    done, report = sg.parse_status("Backups at 42%.\nSTATUS: DONE")
    assert done and report == "Backups at 42%."


# ── one cycle ────────────────────────────────────────────────────────────────


async def test_done_cycle_finishes_the_goal_and_removes_its_schedule(tmp_path, board, calls):
    goal = _goal(board)
    ex = FakeExecutor(tmp_path, [("ok", "Disk at 41%.\nSTATUS: DONE")])

    out = await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "done"
    assert after["cycles_run"] == 1
    assert after["last_report"] == "Disk at 41%."
    assert after["last_receipt_id"] and after["cycle_started_at"] is None
    assert calls["unschedule"] == [goal["id"]]
    assert calls["digest"][0]["type"] == "goal_digest"
    assert "cycle 1" in out and after["last_receipt_id"] in out


async def test_every_cycle_runs_auto_under_the_goal_capability(tmp_path, board, calls):
    goal = _goal(board)
    ex = FakeExecutor(tmp_path, [("ok", "STATUS: CONTINUE")])

    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    mission = ex.missions[0]
    assert mission.capability == "goal"
    assert mission.metadata["autonomy"] == "auto"
    assert mission.metadata["goal_id"] == goal["id"]
    assert "READ-ONLY" in mission.payload["message"]
    assert goal["objective"] in mission.payload["message"]


def test_executor_never_asks_approval_for_a_goal_cycle():
    from navig.contracts.mission import Mission
    from navig.missions.executor import Autonomy, MissionExecutor

    ex = MissionExecutor(None, store=RuntimeStore.__new__(RuntimeStore))
    assert ex._resolve_autonomy(Mission(title="x", capability="goal")) == Autonomy.AUTO


async def test_continue_keeps_the_goal_active_and_feeds_the_next_prompt(tmp_path, board, calls):
    goal = _goal(board)
    ex = FakeExecutor(tmp_path, [("ok", "Disk at 85%.\nSTATUS: CONTINUE"), ("ok", "STATUS: CONTINUE")])

    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)
    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "active" and after["cycles_run"] == 2
    assert "Disk at 85%." in ex.missions[1].payload["message"]
    assert calls["unschedule"] == []


async def test_max_cycles_stops_the_goal(tmp_path, board, calls):
    goal = _goal(board, max_cycles=2)
    ex = FakeExecutor(tmp_path, [("ok", "STATUS: CONTINUE")] * 2)

    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)
    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "stopped"
    assert calls["unschedule"] == [goal["id"]]
    assert "2-cycle limit" in calls["digest"][-1]["body"]


async def test_three_failed_cycles_in_a_row_stop_the_goal(tmp_path, board, calls):
    goal = _goal(board)
    ex = FakeExecutor(tmp_path, [("fail", "boom"), ("raise", "crash"), ("fail", "boom")])

    outs = [await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex) for _ in range(3)]

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "stopped" and after["fail_streak"] == 3
    assert "failed (2 of 3 in a row)" in outs[1]  # recorded, not raised to cron
    assert "crash" in calls["digest"][1]["body"]
    assert calls["unschedule"] == [goal["id"]]


async def test_a_success_resets_the_failure_streak(tmp_path, board, calls):
    goal = _goal(board)
    ex = FakeExecutor(tmp_path, [("fail", "x"), ("fail", "x"), ("ok", "STATUS: CONTINUE"), ("fail", "x")])

    for _ in range(4):
        await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "active" and after["fail_streak"] == 1


async def test_a_running_cycle_blocks_a_second_one(tmp_path, board, calls):
    goal = _goal(board)
    now = datetime.now(UTC)
    board.claim_pursuit_cycle(goal["id"], now=sg._iso(now), stale_before=sg._iso(now - sg.CLAIM_STALE_AFTER))
    ex = FakeExecutor(tmp_path, [])

    out = await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    assert out.startswith("skipped") and ex.missions == []


async def test_pausing_during_a_cycle_is_not_undone_by_it(tmp_path, board, calls):
    goal = _goal(board)

    class PausingExecutor(FakeExecutor):
        async def run_to_completion(self, mission):
            board.set_pursuit_state(goal["id"], "paused")  # the operator, mid-cycle
            return await super().run_to_completion(mission)

    ex = PausingExecutor(tmp_path, [("ok", "STATUS: CONTINUE")])
    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    assert after["run_state"] == "paused"
    assert after["cycles_run"] == 1


async def test_a_deleted_goal_removes_its_orphan_schedule(tmp_path, board, calls):
    out = await sg.run_goal_cycle(None, "nope", store=board, executor=FakeExecutor(tmp_path, []))
    assert out.startswith("skipped") and calls["unschedule"] == ["nope"]


async def test_cycle_leaves_a_receipt_through_the_real_executor(tmp_path, board, calls, monkeypatch):
    from navig.missions.executor import MissionExecutor

    monkeypatch.setattr("navig.license.device_keys._device_key_path", lambda: tmp_path / "k" / "device.key")
    ex = MissionExecutor(None, store=RuntimeStore(tmp_path / "rt"))

    async def fake_agent(mission):
        return "Checked 3 drives.\nSTATUS: DONE"

    async def no_verdict(mission):
        return None

    monkeypatch.setattr(ex, "_run_agent", fake_agent)
    monkeypatch.setattr(ex, "_verify_mission", no_verdict)
    goal = _goal(board)

    await sg.run_goal_cycle(None, goal["id"], store=board, executor=ex)

    after = board.get_goal(goal["id"])
    receipt = ex.store.get_receipt(after["last_receipt_id"])
    assert receipt is not None and receipt.outcome.value == "succeeded"
    assert after["run_state"] == "done"
    from navig.license.device_keys import verify_receipt_dict

    assert verify_receipt_dict(receipt.to_dict()) == (True, "ok")  # every cycle is provable
    assert ex.store.get_mission(receipt.mission_id).status == MissionStatus.SUCCEEDED


# ── scheduler hook ───────────────────────────────────────────────────────────


async def test_cron_dispatches_goal_commands_to_the_cycle_runner(tmp_path, monkeypatch):
    from navig.scheduler.cron_service import CronJob, CronService

    seen = []

    async def fake_cycle(gateway, goal_id, **kw):
        seen.append(goal_id)
        return "cycle 1: active"

    monkeypatch.setattr(sg, "run_goal_cycle", fake_cycle)
    svc = CronService(None, tmp_path)
    svc.gateway = object()  # a live gateway, without registering a global service
    job = CronJob(id="j", name=sg.cron_name("abc"), schedule="every 1 hours", command=sg.cron_command("abc"))

    assert await svc._execute_job_command(job) == "cycle 1: active"
    assert seen == ["abc"]


# ── CLI ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [("6h", "every 6 hours"), ("30m", "every 30 minutes"), ("1d", "every 1 days"),
     ("daily at 9am", "daily at 9am"), ("*/5 * * * *", "*/5 * * * *")],
)
def test_every_shorthand(raw, expected):
    from navig.commands.goal import _normalize_every

    assert _normalize_every(raw) == expected


def test_cli_add_list_pause_resume_remove(board, calls, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands import goal as cli

    monkeypatch.setattr(cli, "_store", lambda: board)
    run = CliRunner().invoke

    added = run(cli.goal_app, ["add", "Keep backups fresh", "--every", "6h", "--json"])
    assert added.exit_code == 0, added.output
    gid = json.loads(added.output)["id"]
    assert calls["schedule"] == [gid]
    assert board.get_goal(gid)["every"] == "every 6 hours"

    listed = json.loads(run(cli.goal_app, ["list", "--json"]).output)
    assert [g["id"] for g in listed] == [gid]

    assert run(cli.goal_app, ["pause", gid[:6]]).exit_code == 0
    assert board.get_goal(gid)["run_state"] == "paused" and calls["unschedule"] == [gid]
    assert run(cli.goal_app, ["pause", gid]).exit_code == 1  # already paused

    assert run(cli.goal_app, ["resume", gid]).exit_code == 0
    assert board.get_goal(gid)["run_state"] == "active" and calls["schedule"] == [gid, gid]

    assert run(cli.goal_app, ["remove", gid, "--yes"]).exit_code == 0
    assert board.get_goal(gid) is None


def test_cli_rejects_a_bad_schedule_and_an_unknown_grant(board, calls, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands import goal as cli

    monkeypatch.setattr(cli, "_store", lambda: board)
    run = CliRunner().invoke

    assert run(cli.goal_app, ["add", "x", "--every", "whenever"]).exit_code == 1
    assert run(cli.goal_app, ["add", "x", "--grant", "full"]).exit_code == 1
    assert board.list_pursuits() == [] and calls["schedule"] == []


def test_cli_does_not_leave_a_goal_nothing_will_run(board, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands import goal as cli

    def broken_schedule(goal):
        raise OSError("scheduler store locked")

    monkeypatch.setattr(cli, "_store", lambda: board)
    monkeypatch.setattr(sg, "schedule", broken_schedule)

    result = CliRunner().invoke(cli.goal_app, ["add", "Keep backups fresh"])

    assert result.exit_code == 1
    assert board.list_pursuits() == []
