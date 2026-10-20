"""Deck routes (validation + read-back) and the `navig checkin` CLI."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

CHAT = 4242


class _Req:
    def __init__(self, body=None, match=None):
        self._body = body
        self.match_info = match or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _body(resp):
    return resp.status, json.loads(resp.body.decode())


@pytest.fixture
def owner(ci_env):
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.allowed_users", [CHAT])
    return ci_env


# ── deck routes ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_adds_pills_and_reads_back(owner):
    from navig.gateway.deck.routes import checkins as r

    status, body = _body(
        await r.handle_checkins_save(
            _Req(
                {
                    "template": "pills",
                    "name": "Metformin",
                    "dose": "500 mg",
                    "times": ["08:00", "20:00"],
                }
            )
        )
    )
    assert status == 201 and body["ok"]
    assert body["data"]["id"] == "metformin" and body["data"]["slots"] == ["08:00", "20:00"]
    assert owner["jobs"]["sync"] == [("metformin", CHAT)]
    status, body = _body(await r.handle_checkins_list(_Req()))
    ids = [c["id"] for c in body["data"]["checkins"]]
    assert ids[0] == "weigh" and "metformin" in ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"template": "pills", "name": "X", "times": ["25:00"]}, "time"),
        ({"template": "pills", "times": ["08:00"]}, "name"),
        ({"template": "nope"}, "template"),
        ({"template": "pills", "name": "X", "times": ["08:00"], "evil": 1}, "unknown"),
        ({"template": "water", "target": 0}, "target"),
        ({"template": "pills", "name": "X", "times": ["08:00"], "enabled": "false"}, "enabled"),
        ({"id": "weigh", "template": "pills"}, "weigh-in"),
    ],
)
async def test_route_rejects_bad_input(owner, payload, needle):
    from navig.gateway.deck.routes import checkins as r

    status, body = _body(await r.handle_checkins_save(_Req(payload)))
    assert status == 400 and not body["ok"] and needle in body["error"]


@pytest.mark.asyncio
async def test_route_rejects_a_non_object_body(owner):
    from navig.gateway.deck.routes import checkins as r

    assert (await r.handle_checkins_save(_Req(ValueError("bad json")))).status == 400
    assert (await r.handle_checkins_save(_Req([1, 2]))).status == 400


@pytest.mark.asyncio
async def test_route_pause_resume_and_update(owner, add_pills):
    from navig.gateway.deck.routes import checkins as r

    add_pills()
    status, body = _body(await r.handle_checkins_save(_Req({"id": "metformin", "enabled": False})))
    assert status == 200 and body["data"]["enabled"] is False
    assert ("metformin", False) in owner["jobs"]["toggled"]
    status, body = _body(
        await r.handle_checkins_save(_Req({"id": "metformin", "times": ["09:00"]}))
    )
    assert status == 200 and body["data"]["slots"] == ["09:00"] and body["data"]["dose"] == "500 mg"
    status, _ = _body(await r.handle_checkins_save(_Req({"id": "ghost", "enabled": True})))
    assert status == 404


@pytest.mark.asyncio
async def test_route_pauses_the_weigh_in_through_its_cadence(owner):
    from navig.gateway.deck.routes import checkins as r
    from navig.spaces import weigh_cadence as wc

    status, body = _body(await r.handle_checkins_save(_Req({"id": "weigh", "enabled": False})))
    assert status == 200 and body["data"]["enabled"] is False
    assert wc.load_settings().enabled is False


@pytest.mark.asyncio
async def test_route_delete_keeps_records_and_refuses_the_weigh_in(owner, add_pills):
    from navig.checkins import defs as d
    from navig.checkins import records
    from navig.gateway.deck.routes import checkins as r

    add_pills()
    records.append(
        owner["records"],
        {
            "date": "2026-10-08",
            "item": "metformin",
            "kind": "dose",
            "slot": "0800",
            "status": "taken",
        },
    )
    status, body = _body(await r.handle_checkins_delete(_Req(match={"id": "metformin"})))
    assert status == 200 and body["data"]["removed"] == "metformin"
    assert d.get_item("metformin") is None
    assert len(records.read(owner["records"])) == 1, "removing a check-in keeps its history"
    assert (await r.handle_checkins_delete(_Req(match={"id": "weigh"}))).status == 400
    assert (await r.handle_checkins_delete(_Req(match={"id": "metformin"}))).status == 404


@pytest.mark.asyncio
async def test_route_needs_a_chat_to_schedule(ci_env):
    from navig.gateway.deck.routes import checkins as r

    status, body = _body(await r.handle_checkins_save(_Req({"template": "water", "target": 8})))
    assert status == 400 and "chat" in body["error"]


# ── CLI ───────────────────────────────────────────────────────────────────────


def _run(*args):
    from navig.commands.checkin import checkin_app

    return CliRunner().invoke(checkin_app, list(args))


def test_cli_add_list_pause_log_stats(owner, monkeypatch):
    # Pinned to today at noon: `stats` rightly skips a slot still ahead today, so the
    # 08:00 dose logged below counted only when the suite ran after 08:00 — it failed
    # every night between midnight and 08:00 (seen 05:09, `scheduled: 0`).
    from navig.checkins import cadence

    real_now = cadence.local_now
    monkeypatch.setattr(
        cadence, "local_now",
        lambda tz=None: real_now(tz).replace(hour=12, minute=0, second=0, microsecond=0),
    )
    res = _run(
        "add", "pills", "--name", "Metformin", "--dose", "500 mg", "--at", "08:00,20:00", "--json"
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["checkin"]["id"] == "metformin"

    res = _run("add", "pills", "--name", "Metformin", "--at", "09:00", "--json")
    assert json.loads(res.output)["checkin"]["id"] == "metformin-2", "never overwrites"

    res = _run("list", "--json")
    assert res.exit_code == 0
    ids = [c["id"] for c in json.loads(res.output)["checkins"]]
    assert ids[:2] == ["weigh", "metformin"]

    assert _run("pause", "metformin", "--json").exit_code == 0
    assert ("metformin", False) in owner["jobs"]["toggled"]

    res = _run("log", "metformin", "--slot", "08:00", "--json")
    assert res.exit_code == 0, res.output
    res = _run("log", "metformin", "--slot", "08:00", "--json")
    assert res.exit_code == 1 and "already recorded" in res.output, "the CLI has the guard too"
    assert _run("log", "metformin", "--slot", "08:00", "--force", "--json").exit_code == 0

    res = _run("stats", "--json")
    assert res.exit_code == 0
    stat = next(s for s in json.loads(res.output)["stats"] if s["id"] == "metformin")
    assert stat["taken"] >= 1


def test_cli_rejects_bad_input_with_a_failing_exit(owner):
    assert _run("add", "pills", "--name", "X", "--at", "26:00", "--json").exit_code == 1
    assert _run("pause", "ghost", "--json").exit_code == 1
    assert _run("remove", "weigh", "--yes", "--json").exit_code == 1
    assert _run("log", "weigh", "--json").exit_code == 1


def test_cli_water(owner):
    res = _run(
        "add", "water", "--target", "8", "--every", "2h", "--from", "9", "--to", "21", "--json"
    )
    assert res.exit_code == 0, res.output
    jobs = json.loads(res.output)["jobs"]
    assert [j["name"] for j in jobs][:2] == ["checkin:water:0900", "checkin:water:1100"]
    assert _run("add", "water", "--json").exit_code == 1, "one water check-in only"
    res = _run("log", "water", "--glasses", "3", "--json")
    assert json.loads(res.output)["total"] == 3


def test_cli_migrate_dry_run_on_a_fixture_store(owner, tmp_path):
    store = tmp_path / "cron_jobs.json"
    store.write_text(
        json.dumps(
            {
                "counter": 1,
                "jobs": [
                    {
                        "id": "job_7",
                        "name": "habit:weigh",
                        "schedule": "10 8 * * *",
                        "command": "navig body checkin --send --space X",
                        "enabled": True,
                        "timeout_seconds": 60,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = store.read_bytes()
    res = _run("migrate", "--dry-run", "--store", str(store), "--json")
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["changes"][0]["to"]["command"] == f"NAVIG_CHECKIN:weigh:{CHAT}"
    assert store.read_bytes() == before
