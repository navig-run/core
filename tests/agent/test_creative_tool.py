"""The agent `creative` tool: mode-independent schema, honest gates, grounded proposals."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from navig.agent.tools.creative_tools import CreativeTool


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.notify import store as notify_store

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    monkeypatch.setattr(notify_store, "_initialised", False)
    yield
    reset_config_manager()
    ConfigSingleton._instance = None


@pytest.fixture()
def tool():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    if not any(e.name == "creative" for e in _AGENT_REGISTRY.entries()):
        from navig.agent.tools.creative_tools import register_creative_tool

        register_creative_tool()
    return CreativeTool()


def _mode(mode: str) -> None:
    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", mode) is True


def _run(tool_obj, **args):
    return asyncio.run(tool_obj.run(args))


def _seed_history(monkeypatch, command: str, times: int = 6) -> None:
    from navig.creative import signals

    base = datetime.now(timezone.utc)
    records = [
        SimpleNamespace(
            command=command,
            timestamp=(base - timedelta(minutes=i * 90)).isoformat(),
            status=SimpleNamespace(value="success"),
        )
        for i in range(times)
    ]
    monkeypatch.setattr(signals, "_read_operations", lambda limit=500: records)


# ── permission invariant: the schema never depends on the mode ───────────────


def test_schema_is_byte_identical_across_modes(tool):
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    def _fingerprint() -> str:
        for entry in _AGENT_REGISTRY.entries():
            if entry.name == "creative":
                return json.dumps(entry.schema, sort_keys=True)
        raise AssertionError("creative tool missing from the registry")

    _mode("standard")
    before = _fingerprint()
    _mode("creative")
    after = _fingerprint()
    assert before == after, "mode switch must never change the tool schema"
    # And the schema really exists with the documented actions.
    schema = json.loads(before)
    props = schema.get("parameters", schema).get("properties", {})
    assert "action" in props


# ── both modes: discovery + advice ───────────────────────────────────────────


def test_lookup_works_in_standard(tool):
    _mode("standard")
    result = _run(tool, action="lookup", query="docker")
    assert result.success, result.error
    assert "entries" in result.output
    assert result.output["count"] >= 0


def test_decide_works_in_standard(tool):
    _mode("standard")
    result = _run(tool, action="decide", goal="list docker containers")
    assert result.success, result.error
    assert result.output["verdict"] in (
        "reuse", "configure", "compose", "extend", "create", "noop",
    )
    assert "reason" in result.output


def test_status_reports_mode_and_index(tool):
    _mode("standard")
    result = _run(tool, action="status")
    assert result.success
    assert result.output["mode"] == "standard"
    assert "index" in result.output and "proposals" in result.output


def test_unknown_action_fails_without_raising(tool):
    result = _run(tool, action="teleport")
    assert result.success is False
    assert "unknown action" in (result.error or "")


# ── propose: gated, grounded, honest ─────────────────────────────────────────


def test_propose_refused_in_standard(tool):
    _mode("standard")
    result = _run(tool, action="propose", goal="frobnicate reports", problem="P")
    assert result.success, "a gate refusal is a normal result, not a crash"
    assert result.output["ok"] is False
    assert "Creative mode is off" in result.output["reason"]

    from navig.creative import proposals as prop

    assert prop.list_proposals() == []


def test_propose_persists_in_creative_with_collected_evidence(tool, monkeypatch):
    _mode("creative")
    _seed_history(monkeypatch, "navig frobnicate reports", times=6)

    result = _run(
        tool, action="propose",
        goal="frobnicate the weekly reports",
        problem="Reports are frobnicated by hand every week",
    )
    assert result.success, result.error
    assert result.output["ok"] is True
    assert result.output["created"] is True, result.output
    assert result.output["verdict"] == "create"

    from navig.creative import proposals as prop

    row = prop.get_proposal(result.output["id"])
    assert row is not None
    assert row["source"] == "agent:chat"
    # The evidence carries the COLLECTED count, not a model-supplied number.
    counts = [e for e in row["evidence"] if e["kind"] == "repeated_command"]
    assert counts and counts[0]["count"] == 6
    assert counts[0]["detail"] == "navig frobnicate reports"


def test_propose_duplicate_is_reported_not_recreated(tool, monkeypatch):
    _mode("creative")
    _seed_history(monkeypatch, "navig dupdup", times=6)

    first = _run(tool, action="propose", goal="dupdup workflow")
    assert first.output["created"] is True
    second = _run(tool, action="propose", goal="dupdup workflow")
    assert second.success
    assert second.output["created"] is False
    assert second.output["reason"].startswith("duplicate:")

    from navig.creative import proposals as prop

    assert len(prop.list_proposals()) == 1
