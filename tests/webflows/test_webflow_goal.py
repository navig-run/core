"""A NAVIG goal reaches "completed" through a Webflow only when the flow's check passed.

The agent's own goal loop (``Heart._process_goals``) runs a subtask whose command is
``navig webflow run <id> --json``; Hands judges it by the exit code. A real headless browser
replays the flow against a tiny local site:

* the check passes → exit 0 → the goal is COMPLETED, the subtask result is the run report;
* the site answers differently → the check fails → exit 1 → the goal is BLOCKED;
* a flow with no check runs every step → exit 3 → still BLOCKED: running is not proof.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import shlex
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from navig.agent.goals import GoalPlanner, GoalState
from navig.agent.heart import Heart
from navig.commands.webflow import webflow_app
from navig.webflows import SCHEMA, WebflowStore

ORDER = """<!doctype html><html><head><title>Order</title></head><body>
<label for="name">Full name</label><input id="name" type="text">
<button type="button" id="next">Next</button>
<script>document.getElementById('next').onclick = () => {
  sessionStorage.setItem('n', document.getElementById('name').value); location.href = 'done.html'; };</script>
</body></html>"""
DONE = """<!doctype html><html><head><title>Done</title></head><body><p role="status" id="r"></p>
<script>setTimeout(() => { document.getElementById('r').textContent =
  (location.search.includes('declined') ? 'Payment declined for ' : 'Order confirmed for ') + sessionStorage.getItem('n'); }, 200);</script>
</body></html>"""


@pytest.fixture
def site(tmp_path: Path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "order.html").write_text(ORDER, encoding="utf-8")
    (root / "done.html").write_text(DONE, encoding="utf-8")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # the access log would land in the captured command output
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))


def flow(site: str, *, check: bool = True, declined: bool = False) -> dict:
    steps = [
        {"action": "fill", "role": "textbox", "name": "Full name", "value": "{{customer}}"},
        {"action": "click", "role": "button", "name": "Next", "expect": {"url_contains": "/done.html"}},
    ]
    if declined:
        steps.append({"action": "navigate", "url": f"{site}/done.html?declined=1"})
    if check:
        steps.append({"action": "assert", "has_text": "Order confirmed for {{customer}}", "timeout_ms": 3000})
    return {"schema": SCHEMA, "name": f"Order {'declined' if declined else 'ok'} {check}", "origins": [site],
            "start_url": f"{site}/order.html",
            "params": [{"name": "customer", "type": "string", "required": True}], "steps": steps}


class Hands:
    """The agent's Hands, executing `navig webflow …` in-process (the installed `navig`
    would be another checkout's code). Same contract: success == exit code 0."""

    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, command: str):
        self.calls.append(command)
        argv = shlex.split(command)
        assert argv[:2] == ["navig", "webflow"]
        r = await asyncio.to_thread(CliRunner().invoke, webflow_app, argv[2:])
        return SimpleNamespace(success=r.exit_code == 0, stdout=r.output, stderr=r.output, exit_code=r.exit_code)


async def _drive(planner: GoalPlanner, hands: Hands) -> None:
    async def emit(*_a, **_k):
        return None

    heart = SimpleNamespace(_goal_planner=planner, get_component=lambda name: hands if name == "hands" else None, emit=emit)
    await Heart._process_goals(heart)


def _goal(tmp_path: Path, wf_id: str, monkeypatch) -> tuple[GoalPlanner, str]:
    """Created the way an agent does it: the MCP goal_add tool with a webflow."""
    import navig.mcp.tools.agent as mcp_agent

    planner = GoalPlanner(storage_dir=tmp_path / "goals")
    monkeypatch.setattr(mcp_agent, "_get_goal_planner", lambda: planner)
    out = mcp_agent._tool_agent_goal_add(None, {"description": "Order a plan for Ada",
                                                 "webflow": {"id": wf_id, "params": {"customer": "Ada"}}})
    assert out["ok"], out
    planner.start_goal(out["goal_id"])
    return planner, out["goal_id"]


def _run(tmp_path: Path, doc: dict, monkeypatch):
    pytest.importorskip("playwright")
    wf = WebflowStore().save(doc)["id"]
    planner, gid = _goal(tmp_path, wf, monkeypatch)
    hands = Hands()
    try:
        asyncio.run(_drive(planner, hands))
    except Exception as exc:  # a missing Chromium build is an environment gap, not a pass
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright Chromium is not installed (playwright install chromium)")
        raise
    goal = GoalPlanner(storage_dir=tmp_path / "goals").get_goal(gid)  # re-read from disk
    if goal.subtasks[0].error and "Executable doesn't exist" in goal.subtasks[0].error:
        pytest.skip("Playwright Chromium is not installed (playwright install chromium)")
    return goal, hands


def test_the_goal_completes_only_after_the_check_passed(tmp_path, site, monkeypatch):
    goal, hands = _run(tmp_path, flow(site), monkeypatch)
    assert hands.calls == [f"navig webflow run {WebflowStore().list()[0].id} -p customer=Ada --json"]
    assert goal.state == GoalState.COMPLETED, goal.subtasks[0].error
    report = json.loads(goal.subtasks[0].result)
    assert report["status"] == "verified"
    assert report["steps"][-1]["action"] == "assert" and report["steps"][-1]["ok"]


def test_a_failed_check_blocks_the_goal(tmp_path, site, monkeypatch):
    goal, _ = _run(tmp_path, flow(site, declined=True), monkeypatch)
    assert goal.state == GoalState.BLOCKED
    assert '"category": "assertion"' in goal.subtasks[0].error


def test_running_every_step_without_a_check_is_not_completion(tmp_path, site, monkeypatch):
    goal, _ = _run(tmp_path, flow(site, check=False), monkeypatch)
    assert goal.state == GoalState.BLOCKED
    assert '"status": "completed"' in goal.subtasks[0].error


# ── discovery and selection (no browser) ─────────────────────────────────────


def _two_flows(site: str) -> tuple[str, str]:
    a = WebflowStore().save({**flow(site), "name": "Order a plan", "description": "buy the pro plan"})["id"]
    b = WebflowStore().save({**flow(site, check=False), "name": "Cancel a plan"})["id"]
    return a, b


def test_a_goal_finds_its_flow_by_words_and_quotes_every_value(site):
    from navig.webflows.goals import webflow_subtask

    a, _ = _two_flows(site)
    st = webflow_subtask({"match": "pro plan", "params": {"customer": "Ada; rm -rf ~ $(id)"}})
    assert shlex.split(st["command"]) == ["navig", "webflow", "run", a, "-p", "customer=Ada; rm -rf ~ $(id)", "--json"]
    assert "WARNING" not in st["description"]


def test_an_ambiguous_or_unknown_flow_returns_candidates_and_creates_no_goal(tmp_path, site, monkeypatch):
    import navig.mcp.tools.agent as mcp_agent

    _two_flows(site)
    planner = GoalPlanner(storage_dir=tmp_path / "goals")
    monkeypatch.setattr(mcp_agent, "_get_goal_planner", lambda: planner)
    out = mcp_agent._tool_agent_goal_add(None, {"description": "x", "webflow": {"match": "plan"}})
    assert "2 webflows match" in out["error"] and {f["name"] for f in out["webflows"]} == {"Order a plan", "Cancel a plan"}
    assert [f["verifiable"] for f in sorted(out["webflows"], key=lambda f: f["name"])] == [False, True]
    out = mcp_agent._tool_agent_goal_add(None, {"description": "x", "webflow": {"id": "wf_nope"}})
    assert "no webflow" in out["error"] and len(out["webflows"]) == 2
    assert planner.list_goals() == []


def test_a_goal_never_carries_a_secret_or_an_unknown_param(site):
    from navig.webflows.goals import WebflowGoalError, webflow_subtask

    doc = flow(site)
    doc["params"].append({"name": "pw", "type": "secret", "vault": {"domain": "127.0.0.1", "username": "ada"}})
    doc["steps"].insert(1, {"action": "fill", "css": "#pw", "value": "{{pw}}", "sensitive": True})
    wf = WebflowStore().save(doc)["id"]
    for params, why in [({"pw": "hunter2"}, "is a secret"), ({"nope": "1"}, "not a parameter")]:
        with pytest.raises(WebflowGoalError) as exc:
            webflow_subtask({"id": wf, "params": params})
        assert why in exc.value.errors[0]
    st = webflow_subtask({"id": wf, "params": {"customer": "Ada"}, "allow_vault": True})
    assert shlex.split(st["command"])[-2:] == ["--allow-vault", "--json"]


def test_a_flow_without_a_check_is_flagged_when_chosen(site):
    from navig.webflows.goals import webflow_subtask

    _, b = _two_flows(site)
    assert "can never verify" in webflow_subtask({"id": b, "params": {"customer": "Ada"}})["description"]
