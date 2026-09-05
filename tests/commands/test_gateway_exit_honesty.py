"""Regression: `navig approve|queue|gateway session` reported success for work the
gateway never did — and rendered the ids you need to act on as blank.

Three distinct bugs, all in the same copied HTTP block (42 paths / 16 commands):

1. NO FAILURE BRANCH EXITED NON-ZERO. The sharpest is the approval gate:

       navig approve yes <id-that-does-not-exist>   ->  "x Request … not found", exit 0

   so `navig approve yes bogus && <proceed>` proceeded having approved nothing. And
   "Gateway is not running" was a ch.warning at exit 0 in 11 copies, so `queue list`
   against a dead gateway was indistinguishable from a genuinely empty queue — the
   `navig doctor` rule ("green means verified, never 'I could not look'") in CLI form.

2. THE json_ok ENVELOPE WAS NOT UNWRAPPED in queue add/show/stats. The payload is one
   level down, so `.get("id")` read the envelope's missing key: a successful add
   printed "Task added: None", `queue stats` reported all zeros for a busy queue, and
   `queue show` rendered a real task as "Task: unknown" with a None handler/status.

3. RICH ATE THE IDs. `approve list` and `queue list` printed the id with a bare
   f"[{id}]", which Rich parses as a style tag and SWALLOWS — verified:

       "  [op-20260730-abc] shell_command (dangerous) - rm -rf /"
         renders as  "shell_command (dangerous) - rm -rf /"

   The operator could see a dangerous pending command but not the id required to
   approve or deny it, and `queue show`/`cancel` both take that id as their argument.

Everything now routes through `_gw_api`, which unwraps, escapes nothing (callers do)
and exits non-zero with the reason. The `*_status` commands deliberately do NOT use it.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.gateway import approve_app, queue_app

pytestmark = pytest.mark.integration


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status: int, body: object = None):
        self.status_code = status
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _envelope(data: object) -> dict:
    """The gateway's json_ok shape — the payload is one level down."""
    return {"ok": True, "data": data, "error": None}


@pytest.fixture
def gw(monkeypatch):
    """Capture calls and script responses, without touching a real gateway."""
    calls: list[tuple[str, str]] = []
    box: dict[str, object] = {"resp": _Resp(200, _envelope({}))}

    def fake(method: str, path: str, **kwargs):
        calls.append((method, path))
        resp = box["resp"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    import navig.commands.gateway as gwmod

    monkeypatch.setattr(gwmod, "_gw_request", fake)
    box["calls"] = calls
    return box


# ── 1. failures exit non-zero ────────────────────────────────────────────────


def test_approving_a_nonexistent_request_does_not_report_success(gw):
    """The bug that could wave a dangerous command through a scripted chain."""
    gw["resp"] = _Resp(404, {"error": "no such request"})

    result = CliRunner().invoke(approve_app, ["yes", "op-nope"], obj={})

    assert result.exit_code == 2, (
        "`approve yes` on a missing request reported success — a scripted "
        "`navig approve yes … && <proceed>` would proceed having approved nothing"
    )
    assert "not found" in " ".join(result.output.split())


def test_denying_a_nonexistent_request_does_not_report_success(gw):
    gw["resp"] = _Resp(404, {"error": "no such request"})
    result = CliRunner().invoke(approve_app, ["no", "op-nope"], obj={})
    assert result.exit_code == 2


def test_an_unreachable_gateway_is_a_failure_not_an_empty_queue(gw):
    """`queue list` used to warn and exit 0, so "gateway down" and "queue empty"
    were the same observable outcome."""
    import requests

    gw["resp"] = requests.exceptions.ConnectionError("refused")

    result = CliRunner().invoke(queue_app, ["list"], obj={})

    assert result.exit_code == 1, "could-not-look must never exit 0"
    flat = " ".join(result.output.split())
    assert "not running" in flat and "navig gateway start" in flat, flat


def test_a_503_module_unavailable_is_a_failure(gw):
    gw["resp"] = _Resp(503, {"error": "tasks module off"})
    result = CliRunner().invoke(queue_app, ["add", "t", "h"], obj={})
    assert result.exit_code == 1
    assert "not available" in " ".join(result.output.split())


def test_a_200_that_cannot_be_parsed_is_not_a_success(gw):
    """A 200 whose body will not parse used to render as "no tasks"."""
    gw["resp"] = _Resp(200, None)  # .json() raises
    result = CliRunner().invoke(queue_app, ["list"], obj={})
    assert result.exit_code == 1
    assert "could not be read" in " ".join(result.output.split())


# ── 2. the envelope is unwrapped ─────────────────────────────────────────────


def test_queue_add_prints_the_real_task_id_not_none(gw):
    """Read off the raw body this was the envelope's missing key -> "Task added: None"."""
    gw["resp"] = _Resp(200, _envelope({"id": "task-42", "name": "t"}))

    result = CliRunner().invoke(queue_app, ["add", "t", "h"], obj={})

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "task-42" in flat, f"the envelope was not unwrapped: {flat}"
    assert "None" not in flat


def test_queue_stats_reports_real_counters_not_zeros(gw):
    gw["resp"] = _Resp(200, _envelope({"total_tasks": 7, "heap_size": 3, "completed_count": 4}))

    result = CliRunner().invoke(queue_app, ["stats"], obj={})

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "Total tasks: 7" in flat, f"a busy queue still reported zeros: {flat}"


def test_queue_show_renders_the_task_not_unknown(gw):
    gw["resp"] = _Resp(200, _envelope({"id": "task-9", "name": "backup", "handler": "h",
                                       "status": "running", "priority": 10}))

    result = CliRunner().invoke(queue_app, ["show", "task-9"], obj={})

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "backup" in flat and "unknown" not in flat, flat


# ── 3. the id survives Rich ──────────────────────────────────────────────────


def test_approve_list_shows_the_request_id(gw):
    """Rich swallowed the bare [id] as a style tag, leaving the operator with a
    dangerous pending action and no id to approve or deny it with."""
    gw["resp"] = _Resp(200, _envelope({"pending": [
        {"id": "op-20260730-abc", "action": "shell_command", "level": "dangerous",
         "description": "rm -rf /"},
    ]}))

    result = CliRunner().invoke(approve_app, ["list"], obj={})

    assert result.exit_code == 0, result.output
    assert "op-20260730-abc" in result.output, (
        "the request id was swallowed as Rich markup — it is the argument the "
        "operator has to pass to `navig approve yes|no`"
    )


def test_queue_list_shows_the_task_id(gw):
    gw["resp"] = _Resp(200, _envelope({"tasks": [
        {"id": "task-77", "name": "sync", "status": "running"},
    ]}))

    result = CliRunner().invoke(queue_app, ["list"], obj={})

    assert result.exit_code == 0, result.output
    assert "task-77" in result.output, "the task id was swallowed as Rich markup"


# ── anti-vacuity ─────────────────────────────────────────────────────────────


def test_success_paths_still_exit_zero(gw):
    """If everything now raised, every assertion above would pass while the
    commands were unusable."""
    gw["resp"] = _Resp(200, _envelope({"pending": []}))
    assert CliRunner().invoke(approve_app, ["list"], obj={}).exit_code == 0

    gw["resp"] = _Resp(200, _envelope({"tasks": []}))
    r = CliRunner().invoke(queue_app, ["list"], obj={})
    assert r.exit_code == 0
    assert "No tasks in queue" in " ".join(r.output.split())


def test_invalid_json_params_is_a_usage_error(gw):
    result = CliRunner().invoke(queue_app, ["add", "t", "h", "--params", "{not json"], obj={})
    assert result.exit_code == 2
    assert "Invalid JSON" in " ".join(result.output.split())


# ── gateway stop: a gateway still running is not a successful stop ───────────


def test_gateway_stop_that_failed_to_stop_it_exits_nonzero(monkeypatch):
    """`gateway stop` warned and exited 0 when the shutdown request was refused, so
    the daemon was still up while the shell saw success — and `stop && start` would
    then race a second instance against a live one."""
    import requests

    from navig.commands.gateway import gateway_app

    def fake_get(url, **kw):
        return _Resp(200, {"ok": True})  # /health -> reachable

    def fake_post(url, **kw):
        return _Resp(500, {"error": "refused"})  # /shutdown -> refused

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    result = CliRunner().invoke(gateway_app, ["stop"], obj={})

    assert result.exit_code == 1, (
        "a refused shutdown reported success; the gateway is still running"
    )
    assert "HTTP 500" in " ".join(result.output.split())


def test_gateway_stop_succeeds_when_the_socket_closes(monkeypatch):
    """Anti-vacuity: the normal stop path must stay exit 0. A ConnectionError on
    /shutdown means the gateway went down mid-request — that IS success."""
    import requests

    from navig.commands.gateway import gateway_app

    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(200, {"ok": True}))

    def closed(url, **kw):
        raise requests.exceptions.ConnectionError("closed")

    monkeypatch.setattr(requests, "post", closed)

    result = CliRunner().invoke(gateway_app, ["stop"], obj={})

    assert result.exit_code == 0, result.output
    assert "Gateway stopped" in " ".join(result.output.split())
