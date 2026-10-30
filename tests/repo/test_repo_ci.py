"""`navig repo gates` fleet + repository views — runners, checks, the land queue, ETA,
bottleneck and spend — against an injected GitHub seam and a temp CI dir.

Nothing here talks to GitHub or reads this machine's real lanes or telemetry.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pytest

from navig.commands import repo_ci
from navig.commands.repo import collect_gates

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _private_ci_state(tmp_path, monkeypatch):
    """Every test reads and writes ONLY its own CI + lane dirs."""
    monkeypatch.setenv("NAVIG_CI_DIR", str(tmp_path / "ci"))
    monkeypatch.setenv("NAVIG_SLOT_DIR", str(tmp_path / "slots"))
    return tmp_path


def _api(table: dict):
    """A gh_api stand-in: exact path → payload; anything else is a failed read (None)."""
    return lambda path: table.get(path)


# ── lane view fixes ───────────────────────────────────────────────────────────


def test_push_lane_holders_are_counted_and_the_default_lane_count_matches_the_protocol(tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "push-0.json").write_text(
        json.dumps({"pid": 7, "repo": "navig/wt", "label": "push", "started": _iso(NOW - 120)}),
        encoding="utf-8",
    )
    data = collect_gates(slots, None, now=NOW, pid_info=lambda pid: {"created": NOW - 121})
    assert data["lanes"] == 4, "heavy-slot-lib.mjs DEFAULT_SLOTS is 4; this view said 3"
    assert data["push"] == 1 and data["push_busy"] == 1
    assert data["gates"][0]["lane"] == "push 0"


def test_a_job_owned_by_another_user_is_running_not_dead(tmp_path):
    """psutil.AccessDenied for the navig-ci service account used to read as 'dead', calling a
    busy lane free; the lane protocol counts EPERM as alive."""
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "slot-0.json").write_text(
        json.dumps(
            {"pid": 9, "repo": "navig/runner", "label": "ci-local fast", "started": _iso(NOW - 300)}
        ),
        encoding="utf-8",
    )
    other = {"created": None, "cwd": None, "ancestors": [], "other_user": True}
    data = collect_gates(slots, None, now=NOW, pid_info=lambda pid: other)
    row = data["gates"][0]
    assert row["state"] == "running" and row["other_user"] is True
    assert data["lanes_busy"] == 1


def test_another_users_step_is_read_from_the_per_pid_run_log_mirror(tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "slot-0.json").write_text(
        json.dumps(
            {"pid": 9, "repo": "navig/runner", "label": "ci-local fast", "started": _iso(NOW - 300)}
        ),
        encoding="utf-8",
    )
    runlogs = tmp_path / "ci" / "runlogs"
    runlogs.mkdir(parents=True)
    (runlogs / "9.json").write_text(
        json.dumps(
            {
                "pid": 9,
                "steps": [
                    {
                        "step": "pytest — core",
                        "state": "running",
                        "startedAt": _iso(NOW - 120),
                        "budgetMin": 60,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    other = {"created": None, "cwd": None, "ancestors": [], "other_user": True}
    data = collect_gates(slots, None, now=NOW, pid_info=lambda pid: other)
    assert data["gates"][0]["step"] == {"name": "pytest — core", "minutes": 2, "budget_min": 60}


# ── runners ───────────────────────────────────────────────────────────────────

_RUNNERS = {
    "total_count": 3,
    "runners": [
        {
            "name": "navig-neuron-r1",
            "status": "online",
            "busy": False,
            "labels": [
                {"name": "navig-gate", "type": "custom"},
                {"name": "Windows", "type": "read-only"},
            ],
        },
        {
            "name": "navig-neuron-r2",
            "status": "online",
            "busy": True,
            "labels": [{"name": "navig-gate", "type": "custom"}],
        },
        {
            "name": "navig-neuron-r3",
            "status": "offline",
            "busy": False,
            "labels": [{"name": "navig-gate", "type": "custom"}],
        },
    ],
}


def test_runner_rows_count_free_busy_and_offline():
    r = repo_ci.collect_runners(
        "o/r", api=_api({"repos/o/r/actions/runners?per_page=100": _RUNNERS})
    )
    assert (r["online"], r["busy"], r["free"], r["offline"]) == (2, 1, 1, 1)
    assert [x["state"] for x in r["runners"]] == ["idle", "busy", "offline"]
    assert r["runners"][0]["labels"] == ["navig-gate"], (
        "read-only labels (OS, arch) are not worker labels"
    )


def test_an_idle_runner_whose_local_service_is_stopped_is_not_a_free_worker():
    services = {"actions.runner.o-r.navig-neuron-r1": "Stopped"}
    r = repo_ci.collect_runners(
        "o/r", api=_api({"repos/o/r/actions/runners?per_page=100": _RUNNERS}), services=services
    )
    assert r["free"] == 0
    assert r["runners"][0]["service"] == "Stopped"


def test_an_unreadable_github_is_unavailable_never_zero_runners():
    r = repo_ci.collect_runners("o/r", api=_api({}))
    assert r["available"] is False


# ── actions ───────────────────────────────────────────────────────────────────


def test_actions_report_queue_delay_and_age():
    run = {
        "id": 1,
        "name": "gate",
        "head_branch": "feat/x",
        "event": "pull_request",
        "head_sha": "a" * 40,
        "created_at": _iso(NOW - 600),
        "run_started_at": _iso(NOW - 480),
    }
    queued = {
        "id": 2,
        "name": "gate",
        "head_branch": "feat/y",
        "event": "pull_request",
        "head_sha": "b" * 40,
        "created_at": _iso(NOW - 90),
        "run_started_at": None,
    }
    a = repo_ci.collect_actions(
        "o/r",
        now=NOW,
        api=_api(
            {
                "repos/o/r/actions/runs?status=in_progress&per_page=30": {"workflow_runs": [run]},
                "repos/o/r/actions/runs?status=queued&per_page=30": {"workflow_runs": [queued]},
            }
        ),
    )
    assert a["running"][0]["wait_s"] == 120 and a["running"][0]["age_s"] == 480
    assert a["queued"][0]["age_s"] == 90


def test_one_failed_actions_read_makes_the_whole_block_unavailable():
    a = repo_ci.collect_actions(
        "o/r",
        api=_api({"repos/o/r/actions/runs?status=in_progress&per_page=30": {"workflow_runs": []}}),
    )
    assert a["available"] is False


# ── PR checks + land queue ────────────────────────────────────────────────────


def _pr(number, sha, labels=(), draft=False):
    return {
        "number": number,
        "title": f"t{number}",
        "head": {"sha": sha, "ref": f"feat/{number}"},
        "draft": draft,
        "user": {"login": "dev"},
        "labels": [{"name": n} for n in labels],
    }


def _check(conclusion, fp="f" * 64, app="github-actions", status="completed"):
    return {
        "check_runs": [
            {
                "app": {"slug": app},
                "status": status,
                "conclusion": conclusion,
                "external_id": fp,
                "completed_at": "2026-10-16T11:00:00Z",
                "html_url": "https://x",
            }
        ]
    }


def test_pr_states_cover_green_red_checking_no_check_draft_and_the_land_queue():
    table = {
        "repos/o/r/pulls?state=open&per_page=50": [
            _pr(1, "a" * 40),
            _pr(2, "b" * 40),
            _pr(3, "c" * 40),
            _pr(4, "d" * 40),
            _pr(5, "e" * 40, draft=True),
            _pr(6, "f" * 40, labels=["land"]),
            _pr(7, "9" * 40, labels=["land-failed"]),
        ],
        f"repos/o/r/commits/{'a' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            "success"
        ),
        f"repos/o/r/commits/{'b' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            "failure"
        ),
        f"repos/o/r/commits/{'c' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            None, status="in_progress"
        ),
        f"repos/o/r/commits/{'d' * 40}/check-runs?check_name=gate-result&per_page=20": {
            "check_runs": []
        },
        f"repos/o/r/commits/{'e' * 40}/check-runs?check_name=gate-result&per_page=20": {
            "check_runs": []
        },
        f"repos/o/r/commits/{'f' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            "success"
        ),
        f"repos/o/r/commits/{'9' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            "success"
        ),
    }
    res = repo_ci.collect_pr_checks("o/r", api=_api(table))
    assert [p["state"] for p in res["prs"]] == [
        "green",
        "red",
        "checking",
        "no-check",
        "draft",
        "queued-to-land",
        "land-failed",
    ]
    assert res["prs"][0]["check"]["fingerprint"] == "f" * 12


def test_a_check_not_authored_by_github_actions_is_ignored():
    table = {
        "repos/o/r/pulls?state=open&per_page=50": [_pr(1, "a" * 40)],
        f"repos/o/r/commits/{'a' * 40}/check-runs?check_name=gate-result&per_page=20": _check(
            "success", app="some-pat-app"
        ),
    }
    assert repo_ci.collect_pr_checks("o/r", api=_api(table))["prs"][0]["state"] == "no-check"


def test_blocked_landings_say_why_from_the_audit_log(tmp_path):
    log = [
        {
            "outcome": "landed",
            "land": "1" * 40,
            "branch": "feat/a",
            "at": "2026-10-16T10:00:00Z",
            "validation": "reused",
            "pr": 3,
        },
        {
            "outcome": "conflict",
            "pr": 7,
            "branch": "feat/7",
            "message": "feat/7 does not rebase cleanly onto origin/main\nresolve it",
        },
    ]
    prs = [{"number": 7, "branch": "feat/7", "state": "land-failed"}]
    s = repo_ci.land_summary(log, prs)
    assert s["last_landed"]["sha"] == "1" * 9 and s["last_landed"]["validation"] == "reused"
    assert s["blocked"] == [
        {
            "number": 7,
            "branch": "feat/7",
            "outcome": "conflict",
            "why": "feat/7 does not rebase cleanly onto origin/main",
        }
    ]


def test_the_land_log_reads_back_what_the_lander_wrote(tmp_path):
    ci = tmp_path / "ci"
    ci.mkdir()
    (ci / "land.jsonl").write_text(
        '{"outcome":"landed","land":"' + "a" * 40 + '"}\nnot json\n{"outcome":"race"}\n',
        encoding="utf-8",
    )
    assert [r["outcome"] for r in repo_ci.read_land_log()] == ["landed", "race"]


# ── ETA ───────────────────────────────────────────────────────────────────────


def _run(seconds, profile="fast", conclusion="success", start="2026-10-16T10:00:00Z"):
    a = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
    return {
        "profile": profile,
        "conclusion": conclusion,
        "startedAt": _iso(a),
        "finishedAt": _iso(a + seconds),
    }


def test_no_eta_from_fewer_than_five_comparable_runs():
    e = repo_ci.eta_estimate([_run(600)] * 4, "fast")
    assert e["seconds"] is None and e["n"] == 4 and "need 5" in e["why"]


def test_failed_runs_and_other_profiles_are_not_comparable():
    runs = [_run(600)] * 4 + [_run(30, conclusion="failure")] + [_run(900, profile="core")]
    assert repo_ci.eta_estimate(runs, "fast")["seconds"] is None


def test_a_dependable_history_gives_p50_and_p90():
    e = repo_ci.eta_estimate([_run(s) for s in (540, 570, 600, 630, 660, 690)], "fast")
    assert e["seconds"] == 615 and e["n"] == 6 and e["p90"] >= 660


def test_a_wide_spread_suppresses_the_eta():
    e = repo_ci.eta_estimate([_run(s) for s in (60, 70, 600, 650, 3000, 3200)], "fast")
    assert e["seconds"] is None and "too variable" in e["why"]


# ── bottleneck ────────────────────────────────────────────────────────────────

_NO_GH = {"available": False}


def _bn(**kw):
    base = dict(lanes_busy=0, lanes_total=4, queued_local=0, runners=_NO_GH, actions=_NO_GH)
    base.update(kw)
    return repo_ci.classify_bottleneck(**base)["kind"]


def test_bottleneck_precedence_and_evidence():
    assert _bn() == "none"
    assert _bn(lanes_busy=4, queued_local=2) == "lane-starved"
    assert _bn(lanes_busy=2, queued_local=2) == "none", (
        "free lanes + a queue is a transient, not starvation"
    )
    assert _bn(cpu_percent=97) == "cpu-bound"
    assert _bn(disk_queue=5, cpu_percent=30) == "io-bound"
    assert _bn(disk_queue=5, cpu_percent=95) == "cpu-bound", (
        "a saturated CPU explains a deep disk queue"
    )
    assert _bn(hung=["pytest (80m, usual p90 12m)"], lanes_busy=4, queued_local=3) == "hung-step"
    gh = _bn(
        runners={"available": True, "free": 2}, actions={"available": True, "queued": [{"id": 1}]}
    )
    assert gh == "github-queued"


def test_hung_step_detection_needs_a_known_p90():
    rows = [
        {"step": {"name": "pytest", "minutes": 40}},
        {"step": {"name": "unknown", "minutes": 500}},
    ]
    assert repo_ci.hung_steps(rows, {"pytest": 600.0}) == ["pytest (40m, usual p90 10m)"]


def test_step_p90s_need_enough_successful_samples():
    runs = [
        {"conclusion": "success", "steps": [{"name": "ruff", "state": "pass", "seconds": s}]}
        for s in (1, 2, 3, 4, 5)
    ]
    assert repo_ci.step_p90s(runs) == {"ruff": 5.0}
    assert repo_ci.step_p90s(runs[:3]) == {}


# ── spend ─────────────────────────────────────────────────────────────────────


def _usage(*items):
    return {"usageItems": [{"product": "actions", **i} for i in items]}


def test_spend_is_the_metered_net_cost_and_the_postponed_fee_is_separate():
    api = _api(
        {
            "organizations/o/settings/billing/usage?year=2026&month=10": _usage(
                {
                    "sku": "Actions Linux",
                    "unitType": "Minutes",
                    "quantity": 1500,
                    "grossAmount": 9.0,
                    "netAmount": 0.0,
                },
                {
                    "sku": "Actions Windows",
                    "unitType": "Minutes",
                    "quantity": 600,
                    "grossAmount": 6.0,
                    "netAmount": 4.0,
                },
                {
                    "sku": "Actions storage",
                    "unitType": "GigabyteHours",
                    "quantity": 5,
                    "grossAmount": 0.001,
                    "netAmount": 0.0,
                },
            )
        }
    )
    s = repo_ci.collect_spend(
        "o", api=api, budget_usd=10, self_hosted_minutes=1000, month="2026-10"
    )
    assert s["hosted_minutes"] == 2100, "only Minutes lines count as minutes — not storage GB-hours"
    assert s["net_usd"] == 4.0 and s["gross_usd"] == 15.0
    assert s["if_enacted_usd"] == 2.0, (
        "1000 self-hosted min x $0.002 — a what-if, never added to net"
    )
    assert s["near_budget"] is False and s["over_budget"] is False


def test_spend_flags_near_and_over_budget():
    mk = lambda net: _api(
        {
            "organizations/o/settings/billing/usage?year=2026&month=10": _usage(  # noqa: E731
                {
                    "sku": "x",
                    "unitType": "Minutes",
                    "quantity": 1,
                    "grossAmount": net,
                    "netAmount": net,
                }
            )
        }
    )
    assert (
        repo_ci.collect_spend("o", api=mk(8.0), budget_usd=10, month="2026-10")["near_budget"]
        is True
    )
    assert (
        repo_ci.collect_spend("o", api=mk(11.0), budget_usd=10, month="2026-10")["over_budget"]
        is True
    )


def test_unreadable_billing_is_unavailable_not_zero():
    assert repo_ci.collect_spend("o", api=_api({}), month="2026-10")["available"] is False


def test_self_hosted_minutes_come_from_runner_telemetry_this_month_only():
    runs = [
        {**_run(600, start="2026-10-05T10:00:00Z"), "runner": "r1"},
        {**_run(1200, start="2026-10-06T10:00:00Z"), "runner": "r2"},
        {**_run(3600, start="2026-09-30T10:00:00Z"), "runner": "r1"},  # last month
        _run(3600, start="2026-10-07T10:00:00Z"),  # a local gate, not a runner
    ]
    assert repo_ci.self_hosted_minutes(runs, month="2026-10") == 30.0


# ── command surface ───────────────────────────────────────────────────────────


def test_gates_json_includes_the_fleet_and_offline_mode_skips_github(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from navig.commands import repo as repo_mod

    monkeypatch.setattr(
        repo_ci, "gh_api", lambda path: pytest.fail("--no-github must not call GitHub")
    )
    monkeypatch.setattr(repo_ci, "repo_slug", lambda root: "o/r")
    monkeypatch.setattr(repo_ci, "sample_cpu", lambda: 10.0)
    monkeypatch.setattr(repo_mod, "resolve_repo_root", lambda _r=None: tmp_path)
    res = CliRunner().invoke(repo_mod.repo_app, ["gates", "--json", "--no-github"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["fleet"]["github"] is False
    assert data["fleet"]["bottleneck"]["kind"] == "none"
    assert "repo_view" not in data


def test_prs_view_is_repository_scoped(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from navig.commands import repo as repo_mod

    monkeypatch.setattr(repo_ci, "repo_slug", lambda root: "o/r")
    monkeypatch.setattr(
        repo_ci, "gh_api", lambda path: [] if path.startswith("repos/o/r/pulls") else None
    )
    monkeypatch.setattr(repo_mod, "resolve_repo_root", lambda _r=None: tmp_path)
    res = CliRunner().invoke(repo_mod.repo_app, ["gates", "--prs", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["repo_view"]["repo"] == "o/r" and data["repo_view"]["prs"] == []
    assert "fleet" not in data


# ── doctor rows ───────────────────────────────────────────────────────────────


def _rows(**kw):
    kw.setdefault(
        "services", {"actions.runner.o-r.r1": "Running", "actions.runner.o-r.r2": "Running"}
    )
    kw.setdefault("env", {})
    kw.setdefault("acl_probe", lambda p: False)
    return {r[0]: r for r in repo_ci.ci_runner_rows(**kw)}


def test_no_runner_services_means_no_rows_not_a_failure():
    assert repo_ci.ci_runner_rows(services={}, env={}) == []


def test_a_stopped_runner_service_is_named(tmp_path):
    rows = _rows(
        services={"actions.runner.o-r.r1": "Running", "actions.runner.o-r.r2": "Stopped"},
        env={"NAVIG_SLOT_DIR": str(tmp_path)},
    )
    assert rows["Runner services"][1] is False and "r2" in rows["Runner services"][2]
    assert rows["Shared lane dir"][1] is True


def test_an_unset_or_unwritable_shared_lane_dir_fails(tmp_path):
    assert _rows(env={})["Shared lane dir"][1] is False
    missing = tmp_path / "nope" / "deeper"
    row = _rows(
        env={"NAVIG_SLOT_DIR": str(missing)},
        group_in_token=lambda _g: False,
        group_lists_user=lambda _g: False,
    )["Shared lane dir"]
    assert row[1] is False and "not writable" in row[2]


# ── the lane hint names the real cause (2026-10-25) ──────────────────────────


def _lane_row(tmp_path, *, in_token, lists_user):
    """The Shared lane dir row for a path this process cannot write."""
    return _rows(
        env={"NAVIG_SLOT_DIR": str(tmp_path / "nope" / "deeper")},
        group_in_token=in_token,
        group_lists_user=lists_user,
    )["Shared lane dir"]


def test_a_missing_membership_asks_for_an_administrator(tmp_path):
    row = _lane_row(tmp_path, in_token=lambda _g: False, lists_user=lambda _g: False)
    assert row[1] is False and "add this account to navig-ci-users" in row[2]
    assert "sign out" not in row[2], "membership is the cause here, not the token"


def test_a_member_with_a_stale_token_is_told_to_sign_back_in(tmp_path):
    """The case that misled this machine: the group lists the account, the token predates it.

    No administrator can fix it and nothing has to be reinstalled — a new logon token does.
    """
    row = _lane_row(tmp_path, in_token=lambda _g: False, lists_user=lambda _g: True)
    assert row[1] is False
    assert "already lists this account" in row[2] and "sign out and back in" in row[2]
    assert "no administrator needed" in row[2]
    assert "add this account" not in row[2]


def test_a_member_in_the_token_gets_the_acl_as_the_blocker(tmp_path):
    row = _lane_row(tmp_path, in_token=lambda _g: True, lists_user=lambda _g: True)
    assert row[1] is False and "ACL is the blocker" in row[2]
    if sys.platform == "win32":
        assert "icacls" in row[2] and "navig-ci-users:(OI)(CI)M" in row[2]
    else:
        assert "setfacl" in row[2]


def test_an_unreadable_membership_says_so_and_still_names_the_way_out(tmp_path):
    row = _lane_row(tmp_path, in_token=lambda _g: None, lists_user=lambda _g: None)
    assert row[1] is False and "could not read navig-ci-users membership" in row[2]
    assert "sign out and back in" in row[2], "an unknown cause still needs a next step"


def test_a_token_alone_is_enough_to_blame_the_acl(tmp_path):
    """If the token carries the group, membership is moot: only the ACL can refuse the write."""
    row = _lane_row(tmp_path, in_token=lambda _g: True, lists_user=lambda _g: None)
    assert "ACL is the blocker" in row[2]


def test_the_disk_floor_and_an_unreadable_volume():
    low = _rows(env={"NAVIG_CI_ROOT": "D:\\x"}, free_gb=lambda p: 5.0)["Runner disk"]
    assert low[1] is False and "infra:disk" in low[2]
    unknown = _rows(env={"NAVIG_CI_ROOT": "D:\\x"}, free_gb=lambda p: None)["Runner disk"]
    assert (unknown[1], unknown[3]) == (False, True), (
        "an unreadable disk is a warning, never a green row"
    )
    assert _rows(env={"NAVIG_CI_ROOT": "D:\\x"}, free_gb=lambda p: 300.0)["Runner disk"][1] is True


def test_a_checkout_every_local_account_can_modify_is_flagged_when_runners_exist(tmp_path):
    exposed = _rows(checkout=tmp_path, acl_probe=lambda p: True)["Repo write access"]
    assert exposed[1] is False and exposed[3] is True and "TightenProjectsAcl" in exposed[2]
    assert _rows(checkout=tmp_path, acl_probe=lambda p: False)["Repo write access"][1] is True
    unknown = _rows(checkout=tmp_path, acl_probe=lambda p: None)["Repo write access"]
    assert (unknown[1], unknown[3]) == (False, True)
