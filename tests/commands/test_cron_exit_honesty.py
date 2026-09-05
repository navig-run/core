"""`navig cron` reported every failure and exited 0.

All seven commands carried the same copied block — import requests, call, check
for 200, sniff for ConnectionError — and not one failure branch exited non-zero.
The consequence is a job that silently never runs:

    navig cron add "nightly backup" "0 2 * * *" "navig backup export"
    ⚠ Gateway is not running          <- exit 0

A provisioning script recorded a schedule that does not exist. 17 such paths.

These tests pin both directions: every failure exits 1, and the two answers that
are legitimately "successful but unwelcome" (an empty job list, a status query
that reports the service is down) still exit 0 — otherwise `raise Exit(1)`
everywhere would satisfy the failure half.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.cron import cron_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _response(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = {"ok": True, "data": payload, "error": None}
    return r


def _patch_request(**kwargs):
    """Block the requests TRANSPORT, not one convenience wrapper.

    `requests.get` / `.post` / `.delete` / `.request` all funnel through
    `Session.request`, so patching there intercepts whichever the implementation
    happens to use. Patching `requests.request` alone was a real hazard, found by
    running these tests against origin/main (which calls `requests.get`): they did
    not intercept and **reached the operator's live gateway**, returning its actual
    job list. A test that silently talks to a real daemon is worse than a failing
    one — `cron remove`/`enable` would have been aimed at it too.
    """
    return patch("requests.sessions.Session.request", **kwargs)


# ── every failure path exits non-zero ─────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "nightly", "0 2 * * *", "navig backup export"],
        ["list"],
        ["remove", "job-1"],
        ["run", "job-1"],
        ["enable", "job-1"],
        ["disable", "job-1"],
        ["status"],
    ],
)
def test_gateway_down_exits_non_zero(argv):
    """The operation did not happen, so the command must not claim success."""
    import requests

    with _patch_request(side_effect=requests.exceptions.ConnectionError("refused")):
        res = runner.invoke(cron_app, argv)

    assert res.exit_code == 1, f"{argv} -> exit {res.exit_code}\n{res.output}"
    assert "gateway is not running" in res.output.lower()
    assert "navig gateway start" in res.output


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "n", "hourly", "cmd"],
        ["list"],
        ["remove", "job-1"],
        ["run", "job-1"],
        ["enable", "job-1"],
        ["disable", "job-1"],
        ["status"],
    ],
)
def test_non_200_exits_non_zero(argv):
    with _patch_request(return_value=_response(status=500, text="boom")):
        res = runner.invoke(cron_app, argv)

    assert res.exit_code == 1, f"{argv} -> exit {res.exit_code}\n{res.output}"
    assert "HTTP 500" in res.output


def test_a_timeout_exits_non_zero_and_says_so():
    import requests

    with _patch_request(side_effect=requests.exceptions.Timeout()):
        res = runner.invoke(cron_app, ["add", "n", "hourly", "cmd"])

    assert res.exit_code == 1
    assert "did not answer" in res.output


def test_an_unreadable_200_is_not_reported_as_an_empty_schedule():
    """A 200 whose body cannot be parsed must fail, not render as "No scheduled jobs".

    Degrading to `{}` here is how a broken gateway looks identical to an empty one.
    """
    bad = MagicMock()
    bad.status_code = 200
    bad.text = "<html>proxy error</html>"
    bad.json.side_effect = ValueError("not json")

    with _patch_request(return_value=bad):
        res = runner.invoke(cron_app, ["list"])

    assert res.exit_code == 1, res.output
    assert "could not be read" in res.output
    assert "No scheduled jobs" not in res.output


def test_a_job_that_ran_and_failed_exits_non_zero():
    """The CALL succeeded; the JOB failed. `cron run x && <next>` must stop."""
    with _patch_request(return_value=_response(payload={"success": False, "error": "boom"})):
        res = runner.invoke(cron_app, ["run", "job-1"])

    assert res.exit_code == 1, res.output
    assert "Job failed" in res.output


def test_missing_requests_exits_non_zero():
    """Two of the seven commands handled this; five gave a bare "Error:" at exit 0."""
    import builtins

    real_import = builtins.__import__

    def _no_requests(name, *a, **kw):
        if name == "requests":
            raise ImportError("no module named requests")
        return real_import(name, *a, **kw)

    with patch.object(builtins, "__import__", _no_requests):
        res = runner.invoke(cron_app, ["list"])

    assert res.exit_code == 1, res.output
    assert "requests" in res.output


# ── the two answers that are unwelcome but successful ──────────────────


def test_an_empty_job_list_still_exits_zero():
    with _patch_request(return_value=_response(payload={"jobs": []})):
        res = runner.invoke(cron_app, ["list"])

    assert res.exit_code == 0, res.output
    assert "No scheduled jobs" in res.output


def test_status_reporting_a_stopped_service_still_exits_zero():
    """The query SUCCEEDED — the answer is just "not running".

    Only an unanswerable query fails. Conflating the two would make
    `navig cron status` unusable for asking the question.
    """
    with _patch_request(return_value=_response(payload={"status": "stopped", "cron": {}})):
        res = runner.invoke(cron_app, ["status"])

    assert res.exit_code == 0, res.output
    assert "not running" in res.output


def test_the_happy_paths_still_exit_zero():
    with _patch_request(return_value=_response(payload={"id": "j1", "next_run": "soon"})):
        assert runner.invoke(cron_app, ["add", "n", "hourly", "cmd"]).exit_code == 0
    with _patch_request(return_value=_response(payload={"success": True, "output": "done"})):
        assert runner.invoke(cron_app, ["run", "j1"]).exit_code == 0
    with _patch_request(return_value=_response(payload={})):
        assert runner.invoke(cron_app, ["remove", "j1"]).exit_code == 0
        assert runner.invoke(cron_app, ["enable", "j1"]).exit_code == 0
        assert runner.invoke(cron_app, ["disable", "j1"]).exit_code == 0
    with _patch_request(
        return_value=_response(payload={"status": "running", "cron": {"jobs": 3}})
    ):
        assert runner.invoke(cron_app, ["status"]).exit_code == 0


def test_list_shows_the_job_id_rich_markup_used_to_eat():
    """`[job_8]` in a `ch.info` string is parsed as a Rich style tag and dropped.

    The id is the argument every other cron command takes (`remove`/`enable`/
    `disable`/`run`), so the listing showed you your jobs and hid the one field
    you need to act on them. Verified against the live gateway before the fix:
    `navig cron list` printed "✅  nightly" with an empty gap where `[job_8]`
    should have been.
    """
    payload = {
        "jobs": [
            {"id": "job_8", "name": "nightly", "schedule": "0 2 * * *",
             "next_run": "2026-07-31T02:00:00", "enabled": True},
            {"id": "job_9", "name": "paused one", "schedule": "hourly", "enabled": False},
        ]
    }
    with _patch_request(return_value=_response(payload=payload)):
        res = runner.invoke(cron_app, ["list"])

    assert res.exit_code == 0, res.output
    assert "job_8" in res.output, f"the job id was swallowed again:\n{res.output}"
    assert "job_9" in res.output
    assert "nightly" in res.output
    # The enabled/disabled distinction survives too.
    assert "●" in res.output and "○" in res.output
