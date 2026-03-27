"""Schema snapshot tests for agent MCP resources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.mcp_server import MCPProtocolHandler


def _mock_agent_resource_payloads(
    handler: MCPProtocolHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_execute(tool_name, args):
        if tool_name == "navig_agent_status_get":
            return {
                "installed": True,
                "running": True,
                "pid": 1234,
                "config_path": "/tmp/agent/config.yaml",
                "mode": "supervised",
                "personality": "friendly",
                "workspace": "/tmp/workspace",
                "timestamp": "2026-02-15T00:00:00",
            }
        if tool_name == "navig_agent_goal_list":
            return {
                "storage_dir": "/tmp/workspace",
                "count": 1,
                "goals": [
                    {
                        "id": "goal-1",
                        "description": "Test goal",
                        "state": "pending",
                        "progress": 0.0,
                        "subtasks": 0,
                        "created_at": "2026-02-15T00:00:00",
                        "started_at": None,
                        "completed_at": None,
                        "metadata": {},
                    }
                ],
            }
        if tool_name == "navig_agent_remediation_list":
            return {
                "source": "actions_file",
                "count": 1,
                "actions": [
                    {
                        "id": "action-1",
                        "type": "component_restart",
                        "component": "brain",
                        "reason": "test",
                        "timestamp": "2026-02-15T00:00:00",
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 5,
                        "error": None,
                        "metadata": {},
                    }
                ],
                "recent_log_entries": [
                    {
                        "timestamp": "2026-02-15 00:00:00",
                        "level": "info",
                        "message": "Scheduled restart",
                    }
                ],
            }
        if tool_name == "navig_agent_learning_run":
            return {
                "analyzed_at": "2026-02-15T00:00:00",
                "days": 7,
                "total_errors": 2,
                "patterns": {
                    "component_error": {
                        "count": 2,
                        "examples": ["[2026-02-14 00:00:00] failed to start"],
                    }
                },
                "recommendations": ["Investigate recurring component lifecycle failures."],
            }
        if tool_name == "navig_agent_service_status":
            return {
                "running": False,
                "platform": "linux",
                "status": "inactive",
                "can_install": True,
                "can_uninstall": True,
                "requires_elevation": False,
                "is_elevated": False,
            }
        raise ValueError(f"Unknown tool_name in mock: {tool_name}")

    monkeypatch.setattr(handler, "_execute_tool", _fake_execute)


def _read_json_resource(handler: MCPProtocolHandler, uri: str) -> dict:
    return json.loads(handler._read_resource(uri))


def test_agent_status_resource_schema_and_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """status resources should keep a stable payload schema and aliases."""
    monkeypatch.setattr("navig.mcp_server.Path.home", classmethod(lambda cls: tmp_path))
    handler = MCPProtocolHandler()
    _mock_agent_resource_payloads(handler, monkeypatch)

    primary = _read_json_resource(handler, "navig://agent/status")
    alias = _read_json_resource(handler, "agent://status")

    assert primary == alias
    assert {
        "installed",
        "running",
        "pid",
        "config_path",
        "mode",
        "personality",
        "workspace",
        "timestamp",
    }.issubset(primary.keys())


def test_agent_goals_resource_schema_and_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """goals resources should keep a stable payload schema and aliases."""
    monkeypatch.setattr("navig.mcp_server.Path.home", classmethod(lambda cls: tmp_path))
    handler = MCPProtocolHandler()
    _mock_agent_resource_payloads(handler, monkeypatch)

    primary = _read_json_resource(handler, "navig://agent/goals")
    alias = _read_json_resource(handler, "agent://goals")

    assert primary == alias
    assert {"storage_dir", "count", "goals"}.issubset(primary.keys())
    assert len(primary["goals"]) == 1
    assert {
        "id",
        "description",
        "state",
        "progress",
        "subtasks",
        "created_at",
        "started_at",
        "completed_at",
        "metadata",
    }.issubset(primary["goals"][0].keys())


def test_agent_remediation_resource_schema_and_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """remediation resources should keep a stable payload schema and aliases."""
    monkeypatch.setattr("navig.mcp_server.Path.home", classmethod(lambda cls: tmp_path))
    handler = MCPProtocolHandler()
    _mock_agent_resource_payloads(handler, monkeypatch)

    primary = _read_json_resource(handler, "navig://agent/remediation")
    alias = _read_json_resource(handler, "agent://remediation")

    assert primary == alias
    assert {"source", "count", "actions", "recent_log_entries"}.issubset(primary.keys())
    assert len(primary["actions"]) == 1
    assert {
        "id",
        "type",
        "component",
        "reason",
        "timestamp",
        "status",
        "attempts",
        "max_attempts",
        "error",
        "metadata",
    }.issubset(primary["actions"][0].keys())
    assert {"timestamp", "level", "message"}.issubset(primary["recent_log_entries"][0].keys())


def test_agent_learning_resource_schema_and_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """learning resources should keep a stable payload schema and aliases."""
    monkeypatch.setattr("navig.mcp_server.Path.home", classmethod(lambda cls: tmp_path))
    handler = MCPProtocolHandler()
    _mock_agent_resource_payloads(handler, monkeypatch)

    primary = _read_json_resource(handler, "navig://agent/learning")
    alias = _read_json_resource(handler, "agent://learning/patterns")

    assert primary == alias
    assert {
        "analyzed_at",
        "days",
        "total_errors",
        "patterns",
        "recommendations",
    }.issubset(primary.keys())
    assert isinstance(primary["patterns"], dict)
    assert isinstance(primary["recommendations"], list)


def test_agent_service_resource_schema_and_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """service resources should keep a stable payload schema and aliases."""
    monkeypatch.setattr("navig.mcp_server.Path.home", classmethod(lambda cls: tmp_path))
    handler = MCPProtocolHandler()
    _mock_agent_resource_payloads(handler, monkeypatch)

    primary = _read_json_resource(handler, "navig://agent/service")
    alias = _read_json_resource(handler, "agent://service")

    assert primary == alias
    assert {
        "running",
        "platform",
        "status",
        "can_install",
        "can_uninstall",
        "requires_elevation",
        "is_elevated",
    }.issubset(primary.keys())
