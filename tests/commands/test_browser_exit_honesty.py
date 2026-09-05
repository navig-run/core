"""`navig browser` reported every failure and exited 0.

Six commands, each with four failure branches — a non-200, a 503 "browser module
not available", a gateway that is not running, and any other exception — and not
one exited non-zero. 24 paths. Browser automation is scripted by definition, so
the exit code is the only thing a script can see:

    navig browser open https://example.com && navig browser click "#buy"

With the gateway down that printed two warnings and reported success, having
clicked nothing on a page it never navigated to.

The request is patched at `requests.sessions.Session.request` — the transport every
requests API funnels through — so no implementation detail can let these reach a
real gateway. Patching one convenience wrapper let an earlier version of the cron
tests create four real jobs on the operator's live daemon.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.browser import browser_app

pytestmark = pytest.mark.integration

runner = CliRunner()

# One invocation of every command, so each is proven for every failure mode.
ALL_COMMANDS = [
    ["status"],
    ["open", "https://example.com"],
    ["screenshot"],
    ["click", "#buy"],
    ["fill", "#email", "a@b.c"],
    ["stop"],
]


def _response(status=200, payload=None, error=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = (
        {"ok": True, "data": payload, "error": None}
        if error is None
        else {"ok": False, "data": None, "error": error}
    )
    return r


def _patch(**kwargs):
    return patch("requests.sessions.Session.request", **kwargs)


@pytest.mark.parametrize("argv", ALL_COMMANDS)
def test_gateway_down_exits_non_zero(argv):
    import requests

    with _patch(side_effect=requests.exceptions.ConnectionError("refused")):
        res = runner.invoke(browser_app, argv)

    assert res.exit_code == 1, f"{argv} -> {res.exit_code}\n{res.output}"
    assert "gateway is not running" in res.output.lower()
    assert "navig gateway start" in res.output


@pytest.mark.parametrize("argv", ALL_COMMANDS)
def test_missing_browser_module_exits_non_zero(argv):
    """503 was a `warning` at exit 0 — the requested action did not happen."""
    with _patch(return_value=_response(status=503)):
        res = runner.invoke(browser_app, argv)

    assert res.exit_code == 1, f"{argv} -> {res.exit_code}\n{res.output}"
    assert "browser module is not available" in res.output.lower()
    # The actionable hint `status` alone used to carry now applies everywhere.
    assert "playwright" in res.output


@pytest.mark.parametrize("argv", ALL_COMMANDS)
def test_a_gateway_error_exits_non_zero(argv):
    with _patch(return_value=_response(status=500, error="boom")):
        res = runner.invoke(browser_app, argv)

    assert res.exit_code == 1, f"{argv} -> {res.exit_code}\n{res.output}"


@pytest.mark.parametrize("argv", ALL_COMMANDS)
def test_a_timeout_exits_non_zero(argv):
    import requests

    with _patch(side_effect=requests.exceptions.Timeout()):
        res = runner.invoke(browser_app, argv)

    assert res.exit_code == 1, f"{argv} -> {res.exit_code}\n{res.output}"
    assert "did not answer" in res.output


def test_an_unreadable_200_is_not_treated_as_success():
    """A 200 whose body will not parse must fail, not render as an empty payload."""
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("not json")

    with _patch(return_value=bad):
        res = runner.invoke(browser_app, ["screenshot"])

    assert res.exit_code == 1, res.output
    assert "could not be read" in res.output


# ── the answers that are unwelcome but successful ──────────────────────


def test_status_reporting_a_stopped_browser_still_exits_zero():
    """The query SUCCEEDED — the answer is just "not running".

    Conflating the two would make `navig browser status` useless for asking the
    question, which is its only purpose.
    """
    with _patch(return_value=_response(payload={"started": False})):
        res = runner.invoke(browser_app, ["status"])

    assert res.exit_code == 0, res.output
    assert "not running" in res.output


def test_the_happy_paths_still_exit_zero():
    """Anti-vacuity: raising unconditionally would satisfy every test above."""
    with _patch(return_value=_response(payload={"started": True, "has_page": True})):
        res = runner.invoke(browser_app, ["status"])
        assert res.exit_code == 0, res.output
        assert "running" in res.output

    with _patch(return_value=_response(payload={"path": "shot.png"})):
        res = runner.invoke(browser_app, ["screenshot"])
        assert res.exit_code == 0, res.output
        assert "shot.png" in res.output

    with _patch(return_value=_response(payload={})):
        for argv in (["open", "https://x.test"], ["click", "#a"], ["fill", "#a", "v"], ["stop"]):
            res = runner.invoke(browser_app, argv)
            assert res.exit_code == 0, f"{argv} -> {res.exit_code}\n{res.output}"
