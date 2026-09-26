"""Six CLI options that were parsed, shown in --help, and never read -- now read.

Each was in tests/quality/test_cli_options_are_used.py::_KNOWN_DEAD with its price written
down. These tests are the receipts: they drive the command or the function it reaches and
assert the option changes the outcome. Where a real command needs a remote host (files,
hestia) the test drives the branch that decides, not the transport.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


# ── tools schema --available/--all ──────────────────────────────────────────


def _registry_with_one_disabled():
    """A registry holding one available tool and one disabled one, no bootstrap."""
    from navig.tools.router import ToolDomain, ToolMeta, ToolRegistry, ToolStatus

    reg = ToolRegistry.__new__(ToolRegistry)
    reg._lock = __import__("threading").RLock()
    reg._initialized = True                       # do not bootstrap the real tool packs
    domain = next(iter(ToolDomain))               # list_tools sorts on domain.value

    def _meta(name, status):
        m = MagicMock(spec=ToolMeta)
        m.name, m.status, m.domain = name, status, domain
        m.is_available = lambda: status is ToolStatus.AVAILABLE
        m.to_openapi_schema = lambda: {}
        return m

    reg._tools = {"live": _meta("live", ToolStatus.AVAILABLE),
                  "dead": _meta("dead", ToolStatus.DISABLED)}
    return reg, ToolStatus


def test_schema_available_only_drops_a_disabled_tool():
    from navig.tools.router import ToolRegistry
    reg, _ = _registry_with_one_disabled()
    names_all = set(ToolRegistry.to_openapi_schema(reg)["paths"])
    names_avail = set(ToolRegistry.to_openapi_schema(reg, available_only=True)["paths"])
    assert "/tools/dead" in names_all, "--all must include the disabled tool"
    assert "/tools/dead" not in names_avail, "--available must drop it (this was never honoured)"
    assert "/tools/live" in names_avail


# ── agent plan --max-steps ───────────────────────────────────────────────────


def test_max_steps_cuts_the_plan_before_execution():
    from navig.agent.plan_execute import ExecutionPlan, PlanExecuteAgent, PlanStep

    agent = PlanExecuteAgent.__new__(PlanExecuteAgent)
    plan = ExecutionPlan(task="t", steps=[PlanStep(tool="x", reason=f"s{i}")
                                          for i in range(1, 6)])
    executed = {}

    async def _plan(task, toolset=None):
        return plan

    async def _execute(p, toolset=None, max_retries=1):
        executed["n"] = len(p.steps)

    async def _approve(p):
        return True

    agent._plan = _plan
    agent._execute = _execute
    agent._request_approval = _approve
    asyncio.run(agent.run("t", auto_approve=True, max_steps=2))
    assert executed["n"] == 2, f"--max-steps 2 executed {executed['n']} steps; the flag was decorative"


def test_no_max_steps_executes_the_whole_plan():
    from navig.agent.plan_execute import ExecutionPlan, PlanExecuteAgent, PlanStep

    agent = PlanExecuteAgent.__new__(PlanExecuteAgent)
    plan = ExecutionPlan(task="t", steps=[PlanStep(tool="x", reason=f"s{i}")
                                          for i in range(1, 4)])
    executed = {}

    async def _plan(task, toolset=None): return plan
    async def _execute(p, toolset=None, max_retries=1): executed["n"] = len(p.steps)
    async def _approve(p): return True

    agent._plan, agent._execute, agent._request_approval = _plan, _execute, _approve
    asyncio.run(agent.run("t", auto_approve=True))
    assert executed["n"] == 3


# ── web hestia list --users / --domains ──────────────────────────────────────


def _hestia(args):
    from navig.commands.webserver import web_hestia_app
    calls = []
    with patch("navig.commands.hestia.list_domains_cmd", lambda uf, obj: calls.append(("domains", uf))), \
         patch("navig.commands.hestia.list_users_cmd", lambda obj: calls.append(("users", None))):
        result = CliRunner().invoke(web_hestia_app, ["list", *args], obj={})
    return result, calls


def test_hestia_domains_flag_lists_domains():
    """It used to be crossed: --users listed domains and --domains was ignored."""
    result, calls = _hestia(["--domains"])
    assert result.exit_code == 0, result.output
    assert calls == [("domains", None)], calls


def test_hestia_users_flag_lists_users():
    result, calls = _hestia(["--users"])
    assert result.exit_code == 0, result.output
    assert calls == [("users", None)], calls


def test_hestia_default_still_lists_users():
    result, calls = _hestia([])
    assert result.exit_code == 0
    assert calls == [("users", None)]


def test_hestia_user_filter_alone_selects_domains():
    result, calls = _hestia(["--user", "alice"])
    assert calls == [("domains", "alice")]


def test_hestia_both_flags_is_refused_not_guessed():
    result, calls = _hestia(["--users", "--domains"])
    assert result.exit_code == 2
    assert calls == []


# ── file list --tables / --containers ────────────────────────────────────────


@pytest.mark.parametrize("flag, expect", [("--tables", "navig db tables"), ("--containers", "navig docker ps")])
def test_file_list_leftover_flags_point_at_the_real_command(flag, expect):
    """A FILE listing has no tables or containers. Removing the flags would break the CLI;
    ignoring them was the bug. They now say where to go."""
    from navig.commands.files import file_app
    result = CliRunner().invoke(file_app, ["list", "/tmp", flag], obj={})
    assert result.exit_code == 2, result.output
    assert expect in result.output, f"must name the right command: {result.output}"


# ── blackbox capture --limit ─────────────────────────────────────────────────


def test_blackbox_capture_passes_its_limit_to_the_bundle():
    from navig.commands.blackbox import blackbox_app
    seen = {}

    def fake_create_bundle(since_hours=24.0, blackbox_dir=None, log_files=None, limit=2000):
        seen["limit"] = limit
        b = MagicMock(); b.event_count = lambda: 0; b.crash_count = lambda: 0
        return b

    with patch("navig.blackbox.bundle.create_bundle", fake_create_bundle):
        CliRunner().invoke(blackbox_app, ["capture", "-n", "7"], obj={})
    assert seen.get("limit") == 7, f"--limit 7 reached the bundle as {seen.get('limit')} (was hardcoded 2000)"


# ── update source --no-show ──────────────────────────────────────────────────


def test_update_source_no_show_says_nothing():
    """`--no-show` was declared and never read, so it displayed the config anyway."""
    from navig.commands.update import update_app
    with patch("navig.config.get_config_manager") as cm:
        cm.return_value.get.side_effect = lambda k, d=None: {"update.source": {"type": "pypi"},
                                                            "update.channel": "stable"}.get(k, d)
        quiet = CliRunner().invoke(update_app, ["source", "--no-show"], obj={})
        loud = CliRunner().invoke(update_app, ["source"], obj={})
    assert quiet.exit_code == 0, quiet.output
    assert quiet.output.strip() == "", f"--no-show printed: {quiet.output!r}"
    assert "pypi" in loud.output, "the default must still show the source"
