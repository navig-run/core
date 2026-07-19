"""Contract tests for the /api/deck/schedule crons + triggers routes (slice B5).

Exercises the automations editor API over real HTTP (TestClient) against the
REAL engines — a detached `CronService` over `<config_dir>/scheduler/` and a
`TriggerManager` over `<config_dir>/triggers/` (no gateway in the app, so the
routes take their file-backed fallback paths).

All filesystem state is isolated: NAVIG_CONFIG_DIR + NAVIG_DATA_DIR → tmp_path.
Run-now never spawns subprocesses here: `_execute_job_command` is monkeypatched
for crons, and the fired trigger uses the console notify action.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.deck.routes.schedule as schedule_routes
from navig.scheduler.cron_service import CronParser, CronService


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/schedule/crons", schedule_routes.handle_deck_crons_list)
    app.router.add_post("/api/deck/schedule/crons", schedule_routes.handle_deck_crons_create)
    app.router.add_get("/api/deck/schedule/crons/runs", schedule_routes.handle_deck_cron_runs)
    app.router.add_post("/api/deck/schedule/crons/{job_id}", schedule_routes.handle_deck_cron_update)
    app.router.add_delete("/api/deck/schedule/crons/{job_id}", schedule_routes.handle_deck_cron_delete)
    app.router.add_post("/api/deck/schedule/crons/{job_id}/enable", schedule_routes.handle_deck_cron_enable)
    app.router.add_post("/api/deck/schedule/crons/{job_id}/disable", schedule_routes.handle_deck_cron_disable)
    app.router.add_post("/api/deck/schedule/crons/{job_id}/run", schedule_routes.handle_deck_cron_run)
    app.router.add_get("/api/deck/schedule/triggers", schedule_routes.handle_deck_triggers_list)
    app.router.add_post("/api/deck/schedule/triggers", schedule_routes.handle_deck_triggers_create)
    app.router.add_get("/api/deck/schedule/triggers/history", schedule_routes.handle_deck_triggers_history)
    app.router.add_post("/api/deck/schedule/triggers/{trigger_id}", schedule_routes.handle_deck_trigger_update)
    app.router.add_delete("/api/deck/schedule/triggers/{trigger_id}", schedule_routes.handle_deck_trigger_delete)
    app.router.add_post("/api/deck/schedule/triggers/{trigger_id}/enable", schedule_routes.handle_deck_trigger_enable)
    app.router.add_post("/api/deck/schedule/triggers/{trigger_id}/disable", schedule_routes.handle_deck_trigger_disable)
    app.router.add_post("/api/deck/schedule/triggers/{trigger_id}/fire", schedule_routes.handle_deck_trigger_fire)
    return app


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point every store at tmp: cron store → <config>/scheduler, triggers → <config>/triggers."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


async def _post(client: TestClient, path: str, body: dict | None = None):
    res = await client.post(path, json=body or {})
    return res.status, await res.json()


async def _get(client: TestClient, path: str):
    res = await client.get(path)
    return res.status, await res.json()


async def _delete(client: TestClient, path: str):
    res = await client.delete(path)
    return res.status, await res.json()


# ── Schedule validation (engine-level truth the editor preview relies on) ─────


def test_cron_parser_is_valid():
    assert CronParser.is_valid("every 5 minutes")
    assert CronParser.is_valid("daily")
    assert CronParser.is_valid("*/5 * * * *")
    assert CronParser.is_valid("0 9 * * 1-5")
    assert CronParser.is_valid("0 0 * * 7")  # 7 == Sunday
    assert not CronParser.is_valid("whenever vibes")
    assert not CronParser.is_valid("")
    assert not CronParser.is_valid("* * *")  # too few cron fields
    assert not CronParser.is_valid("61 * * * *")  # out of range — parses but never fires
    assert not CronParser.is_valid("0 0 * * 8")  # day-of-week 8 out of range


# ── Crons — CRUD over the real store ──────────────────────────────────────────


async def test_cron_create_list_roundtrip(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "Health check",
            "schedule": "every 5 minutes",
            "command": "navig host list",
        })
        assert status == 201 and body["ok"] is True
        job = body["data"]
        assert job["id"] and job["name"] == "Health check"
        assert job["enabled"] is True
        assert job["next_run"]  # computed at creation for enabled jobs

        status, body = await _get(client, "/api/deck/schedule/crons")
        assert status == 200
        data = body["data"]
        assert data["count"] == 1
        assert data["jobs"][0]["command"] == "navig host list"

    # Persisted where the LIVE scheduler engine reads (config_dir/scheduler).
    store = isolated_home / "config" / "scheduler" / "cron_jobs.json"
    assert store.is_file()


async def test_cron_create_validation(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/schedule/crons", {"name": "x"})
        assert status == 400 and "required" in body["error"]

        status, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "x", "schedule": "whenever vibes", "command": "navig --version",
        })
        assert status == 400 and "schedule" in body["error"]

        # Out-of-range cron parses the char-set but never fires — rejected with a
        # specific reason instead of silently becoming a +1h job.
        status, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "x", "schedule": "0 25 * * *", "command": "navig --version",
        })
        assert status == 400 and "out of range" in body["error"]

        status, _ = await _post(client, "/api/deck/schedule/crons", {
            "name": "x", "schedule": "daily", "command": "navig --version",
            "timeout_seconds": -3,
        })
        assert status == 400

        status, body = await _get(client, "/api/deck/schedule/crons")
        assert status == 200 and body["data"]["count"] == 0  # nothing leaked


async def test_cron_update_and_toggle(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "Backup", "schedule": "daily", "command": "navig backup run",
        })
        job_id = body["data"]["id"]
        first_next = body["data"]["next_run"]

        # Rename + reschedule → next_run recalculated.
        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}", {
            "name": "Backup (hourly)", "schedule": "every 1 hours",
        })
        assert status == 200
        assert body["data"]["name"] == "Backup (hourly)"
        assert body["data"]["next_run"] != first_next

        # Bad schedule refused.
        status, _ = await _post(client, f"/api/deck/schedule/crons/{job_id}", {"schedule": "nope"})
        assert status == 400

        # Disable clears next_run; enable recomputes it.
        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}/disable")
        assert status == 200
        assert body["data"]["enabled"] is False and body["data"]["next_run"] is None

        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}/enable")
        assert status == 200
        assert body["data"]["enabled"] is True and body["data"]["next_run"]

        # enabled=false through the update verb behaves like disable.
        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}", {"enabled": False})
        assert status == 200
        assert body["data"]["enabled"] is False and body["data"]["next_run"] is None

        # Unknown id → 404; empty update → 400.
        status, _ = await _post(client, "/api/deck/schedule/crons/ghost", {"name": "x"})
        assert status == 404
        status, _ = await _post(client, f"/api/deck/schedule/crons/{job_id}", {})
        assert status == 400


async def test_cron_delete(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "Doomed", "schedule": "daily", "command": "navig --version",
        })
        job_id = body["data"]["id"]

        status, body = await _delete(client, f"/api/deck/schedule/crons/{job_id}")
        assert status == 200 and body["data"]["deleted"] == job_id

        status, _ = await _delete(client, f"/api/deck/schedule/crons/{job_id}")
        assert status == 404

        status, body = await _get(client, "/api/deck/schedule/crons")
        assert body["data"]["count"] == 0


async def test_cron_run_now_records_history(isolated_home, monkeypatch):
    async def fake_exec(self, job):
        return f"ran {job.name}"

    monkeypatch.setattr(CronService, "_execute_job_command", fake_exec)

    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "Echo", "schedule": "daily", "command": "navig --version",
        })
        job_id = body["data"]["id"]

        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}/run")
        assert status == 200
        data = body["data"]
        assert data["success"] is True
        assert data["output"] == "ran Echo"
        assert data["job"]["last_status"] == "success"

        # Unknown id → 404.
        status, _ = await _post(client, "/api/deck/schedule/crons/ghost/run")
        assert status == 404

        # The manual run landed in the run history, newest first.
        status, body = await _get(client, f"/api/deck/schedule/crons/runs?job_id={job_id}")
        assert status == 200
        runs = body["data"]["runs"]
        assert len(runs) == 1
        assert runs[0]["trigger"] == "manual"
        assert runs[0]["status"] == "success"
        assert runs[0]["output"] == "ran Echo"

        # Filtering by another job id returns nothing.
        status, body = await _get(client, "/api/deck/schedule/crons/runs?job_id=ghost")
        assert body["data"]["count"] == 0

        # Bad limit → 400.
        status, _ = await _get(client, "/api/deck/schedule/crons/runs?limit=oops")
        assert status == 400


async def test_cron_run_failure_recorded(isolated_home, monkeypatch):
    async def failing_exec(self, job):
        raise RuntimeError("boom")

    monkeypatch.setattr(CronService, "_execute_job_command", failing_exec)

    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/crons", {
            "name": "Flaky", "schedule": "daily", "command": "navig --version",
        })
        job_id = body["data"]["id"]

        status, body = await _post(client, f"/api/deck/schedule/crons/{job_id}/run")
        assert status == 200
        assert body["data"]["success"] is False
        assert "boom" in (body["data"]["error"] or "")

        _, body = await _get(client, f"/api/deck/schedule/crons/runs?job_id={job_id}")
        assert body["data"]["runs"][0]["status"] == "failed"


# ── Triggers — CRUD + fire + history over the real store ─────────────────────


async def test_trigger_create_list(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/schedule/triggers", {
            "name": "Console ping",
            "type": "manual",
            "action_type": "notify",
            "action_target": "console",
            "description": "says hi",
        })
        assert status == 201 and body["ok"] is True
        trig = body["data"]
        assert trig["id"].startswith("console-ping-")
        assert trig["status"] == "enabled"
        assert trig["actions"][0] == {
            "type": "notify", "target": "console", "params": {},
            "on_failure": "continue", "retries": 0,
        }

        status, body = await _get(client, "/api/deck/schedule/triggers")
        assert status == 200
        assert body["data"]["count"] == 1
        assert body["data"]["triggers"][0]["name"] == "Console ping"

        # Same name → same derived id → 409, not a silent overwrite.
        status, _ = await _post(client, "/api/deck/schedule/triggers", {
            "name": "Console ping", "action_target": "console", "action_type": "notify",
        })
        assert status == 409

    assert (isolated_home / "config" / "triggers" / "triggers.yaml").is_file()


async def test_trigger_create_validation(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(client, "/api/deck/schedule/triggers", {})
        assert status == 400  # name required

        status, body = await _post(client, "/api/deck/schedule/triggers", {"name": "x"})
        assert status == 400 and "action_target" in body["error"]

        status, body = await _post(client, "/api/deck/schedule/triggers", {
            "name": "x", "action_target": "y", "type": "vibes",
        })
        assert status == 400 and "type" in body["error"]

        status, _ = await _post(client, "/api/deck/schedule/triggers", {
            "name": "x", "action_target": "y", "action_type": "teleport",
        })
        assert status == 400

        status, _ = await _post(client, "/api/deck/schedule/triggers", {
            "name": "x", "action_target": "y", "type": "schedule", "schedule": "gibberish",
        })
        assert status == 400


async def test_trigger_update_toggle_delete(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/triggers", {
            "name": "Nightly", "type": "schedule", "schedule": "daily",
            "action_type": "notify", "action_target": "console",
        })
        tid = body["data"]["id"]

        status, body = await _post(client, f"/api/deck/schedule/triggers/{tid}", {
            "description": "runs at night", "cooldown_seconds": 120,
            "action_target": "log",
        })
        assert status == 200
        assert body["data"]["description"] == "runs at night"
        assert body["data"]["cooldown_seconds"] == 120
        assert body["data"]["actions"][0]["target"] == "log"
        assert body["data"]["actions"][0]["type"] == "notify"  # merged, not reset

        status, body = await _post(client, f"/api/deck/schedule/triggers/{tid}/disable")
        assert status == 200 and body["data"]["status"] == "disabled"

        status, body = await _post(client, f"/api/deck/schedule/triggers/{tid}/enable")
        assert status == 200 and body["data"]["status"] == "enabled"

        # Bad update input → 400; unknown id → 404.
        status, _ = await _post(client, f"/api/deck/schedule/triggers/{tid}", {"schedule": "nope"})
        assert status == 400
        status, _ = await _post(client, "/api/deck/schedule/triggers/ghost", {"name": "x"})
        assert status == 404

        status, body = await _delete(client, f"/api/deck/schedule/triggers/{tid}")
        assert status == 200 and body["data"]["deleted"] == tid
        status, _ = await _delete(client, f"/api/deck/schedule/triggers/{tid}")
        assert status == 404


async def test_trigger_fire_dry_run_and_history(isolated_home):
    async with TestClient(TestServer(_app())) as client:
        _, body = await _post(client, "/api/deck/schedule/triggers", {
            "name": "Hello", "type": "manual",
            "action_type": "notify", "action_target": "console",
        })
        tid = body["data"]["id"]

        # Dry run: reports what would happen, executes nothing, logs nothing.
        status, body = await _post(client, f"/api/deck/schedule/triggers/{tid}/fire", {"dry_run": True})
        assert status == 200
        assert body["data"]["success"] is True
        assert body["data"]["dry_run"] is True
        assert body["data"]["actions_run"] == 0

        status, body = await _get(client, "/api/deck/schedule/triggers/history")
        assert body["data"]["count"] == 0

        # Real fire: console notify runs in-process (no subprocess).
        status, body = await _post(client, f"/api/deck/schedule/triggers/{tid}/fire")
        assert status == 200
        data = body["data"]
        assert data["success"] is True and data["actions_run"] == 1

        status, body = await _get(client, f"/api/deck/schedule/triggers/history?trigger_id={tid}")
        assert body["data"]["count"] == 1
        assert body["data"]["history"][0]["trigger_id"] == tid
        assert body["data"]["history"][0]["success"] is True

        # Fire count reflected in the listing.
        _, body = await _get(client, "/api/deck/schedule/triggers")
        assert body["data"]["triggers"][0]["fire_count"] == 1

        status, _ = await _post(client, "/api/deck/schedule/triggers/ghost/fire")
        assert status == 404


# ── Rate limit: max_fires_per_hour is actually ENFORCED, not just displayed ────


def test_process_event_enforces_max_fires_per_hour(isolated_home):
    # End-to-end proof that _execute_trigger feeds the rolling window and
    # process_event honours it: a 2/hour trigger fires twice, then is blocked —
    # the limit was previously stored + shown but never checked.
    from navig.commands.triggers import (
        ActionType,
        Trigger,
        TriggerAction,
        TriggerEvent,
        TriggerManager,
        TriggerType,
    )

    mgr = TriggerManager()
    mgr.add_trigger(
        Trigger(
            id="",
            name="Flapper",
            type=TriggerType.MANUAL,
            max_fires_per_hour=2,
            cooldown_seconds=0,  # isolate the rate limit from the cooldown gate
            actions=[TriggerAction(type=ActionType.NOTIFY, target="console")],
        )
    )

    fired = 0
    for _ in range(5):
        results = mgr.process_event(
            TriggerEvent(type=TriggerType.MANUAL, source="test", data={})
        )
        fired += len(results)  # only triggers that passed can_fire appear here
    assert fired == 2  # the 2/hour limit held; the other 3 events were blocked
