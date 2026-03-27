"""Tests for agent control-plane MCP tools/resources."""

from __future__ import annotations

import json

from navig.mcp_server import MCPProtocolHandler


def test_agent_control_plane_tools_are_exposed():
    """New agent control-plane tools should be listed."""
    handler = MCPProtocolHandler()
    names = {tool["name"] for tool in handler._handle_tools_list({})["tools"]}

    expected = {
        "navig_agent_status_get",
        "navig_agent_goal_list",
        "navig_agent_goal_add",
        "navig_agent_goal_start",
        "navig_agent_goal_cancel",
        "navig_agent_remediation_list",
        "navig_agent_learning_run",
        "navig_agent_service_status",
        "navig_agent_component_restart",
        "navig_agent_remediation_retry",
        "navig_agent_service_install",
        "navig_agent_service_uninstall",
    }
    assert expected.issubset(names)


def test_agent_component_restart_and_retry(monkeypatch):
    """Restart should queue remediation action, retry should requeue it."""
    state = {}

    class FakeRemediationEngine:
        def __init__(self):
            self._state = state

        def schedule_restart_sync(self, component, reason, metadata=None):
            action_id = "action-1"
            self._state[action_id] = {
                "id": action_id,
                "component": component,
                "reason": reason,
                "metadata": metadata or {},
                "status": "pending",
            }
            return action_id

        def get_action_status(self, action_id):
            return self._state.get(action_id)

        def retry_action(self, action_id, reset_attempts=True):
            if action_id not in self._state:
                return False
            self._state[action_id]["status"] = "pending"
            self._state[action_id]["reset_attempts"] = bool(reset_attempts)
            return True

    monkeypatch.setattr("navig.agent.remediation.RemediationEngine", FakeRemediationEngine)

    handler = MCPProtocolHandler()
    restart = handler._execute_tool(
        "navig_agent_component_restart",
        {"component": "brain", "reason": "test-restart"},
    )
    assert restart["ok"] is True
    assert restart["action_id"] == "action-1"
    assert restart["action"]["component"] == "brain"

    retry = handler._execute_tool(
        "navig_agent_remediation_retry",
        {"id": "action-1", "reset_attempts": True},
    )
    assert retry["ok"] is True
    assert retry["action"]["reset_attempts"] is True


def test_agent_service_install_and_uninstall(monkeypatch):
    """Service install/uninstall tools should return installer results."""
    calls = {"install": 0, "uninstall": 0}

    class FakeServiceInstaller:
        def __init__(self):
            self.system = "linux"

        def install(self, start_now=True):
            calls["install"] += 1
            return True, f"installed start_now={bool(start_now)}"

        def uninstall(self):
            calls["uninstall"] += 1
            return True, "uninstalled"

    monkeypatch.setattr("navig.agent.service.ServiceInstaller", FakeServiceInstaller)

    handler = MCPProtocolHandler()
    install_result = handler._execute_tool(
        "navig_agent_service_install",
        {"start_now": False},
    )
    uninstall_result = handler._execute_tool("navig_agent_service_uninstall", {})

    assert install_result["ok"] is True
    assert install_result["platform"] == "linux"
    assert "start_now=False" in install_result["message"]
    assert uninstall_result["ok"] is True
    assert uninstall_result["platform"] == "linux"
    assert calls["install"] == 1
    assert calls["uninstall"] == 1


def test_agent_service_status_includes_capability_flags(monkeypatch):
    """Service status tool should include capability flags."""

    class FakeServiceInstaller:
        def __init__(self):
            self.system = "linux"

        def status(self):
            return False, "inactive"

    monkeypatch.setattr("navig.agent.service.ServiceInstaller", FakeServiceInstaller)

    handler = MCPProtocolHandler()
    monkeypatch.setattr(
        "navig.mcp.tools.agent._get_service_capabilities",
        lambda: {
            "can_install": True,
            "can_uninstall": True,
            "requires_elevation": False,
            "is_elevated": False,
        },
    )
    result = handler._execute_tool("navig_agent_service_status", {})

    assert result["running"] is False
    assert result["platform"] == "linux"
    assert result["status"] == "inactive"
    assert result["can_install"] is True
    assert result["can_uninstall"] is True
    assert result["requires_elevation"] is False
    assert result["is_elevated"] is False


def test_agent_resource_aliases_read(monkeypatch):
    """Both navig://agent/* and agent://* URIs should resolve."""
    handler = MCPProtocolHandler()

    def _fake_execute(tool_name, args):
        if tool_name == "navig_agent_status_get":
            return {"installed": True, "running": False}
        if tool_name == "navig_agent_goal_list":
            return {"count": 1, "goals": [{"id": "g1"}]}
        if tool_name == "navig_agent_remediation_list":
            return {"count": 0, "actions": []}
        if tool_name == "navig_agent_learning_run":
            return {"total_errors": 0, "patterns": {}}
        if tool_name == "navig_agent_service_status":
            return {"running": False, "platform": "linux", "status": "inactive"}
        raise ValueError(f"Unexpected tool: {tool_name}")

    monkeypatch.setattr(handler, "_execute_tool", _fake_execute)

    status = json.loads(handler._read_resource("agent://status"))
    goals = json.loads(handler._read_resource("navig://agent/goals"))
    remediation = json.loads(handler._read_resource("agent://remediation"))
    learning = json.loads(handler._read_resource("agent://learning/patterns"))
    service = json.loads(handler._read_resource("navig://agent/service"))

    assert status["installed"] is True
    assert goals["count"] == 1
    assert remediation["count"] == 0
    assert learning["total_errors"] == 0
    assert service["platform"] == "linux"
