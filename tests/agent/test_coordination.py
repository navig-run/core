"""
Hermetic unit tests for navig.agent.coordination

Covers:
- AgentRole / MessageType enum values
- AgentInfo defaults
- AgentMessage to_dict / from_dict roundtrip
- TaskRequest / TaskResult to_dict
- AgentRegistry register / unregister / find / update_heartbeat
"""

from datetime import datetime
from unittest.mock import patch

import pytest

# Suppress debug logger noise
with patch("navig.debug_logger.get_debug_logger"):
    from navig.agent.coordination import (
        AgentInfo,
        AgentMessage,
        AgentRegistry,
        AgentRole,
        MessageType,
        TaskRequest,
        TaskResult,
    )


# ─────────────────────────────────────────────────────────────
# Enum tests
# ─────────────────────────────────────────────────────────────


class TestAgentRole:
    def test_coordinator_value(self):
        assert AgentRole.COORDINATOR.value == "coordinator"

    def test_specialist_value(self):
        assert AgentRole.SPECIALIST.value == "specialist"

    def test_worker_value(self):
        assert AgentRole.WORKER.value == "worker"

    def test_monitor_value(self):
        assert AgentRole.MONITOR.value == "monitor"

    def test_all_roles_unique(self):
        values = [r.value for r in AgentRole]
        assert len(values) == len(set(values))


class TestMessageType:
    def test_request_value(self):
        assert MessageType.REQUEST.value == "request"

    def test_response_value(self):
        assert MessageType.RESPONSE.value == "response"

    def test_broadcast_value(self):
        assert MessageType.BROADCAST.value == "broadcast"

    def test_heartbeat_value(self):
        assert MessageType.HEARTBEAT.value == "heartbeat"

    def test_handoff_value(self):
        assert MessageType.HANDOFF.value == "handoff"

    def test_context_value(self):
        assert MessageType.CONTEXT.value == "context"

    def test_error_value(self):
        assert MessageType.ERROR.value == "error"


# ─────────────────────────────────────────────────────────────
# AgentInfo
# ─────────────────────────────────────────────────────────────


class TestAgentInfo:
    def _make(self, **kwargs):
        defaults = dict(
            agent_id="a1",
            name="Worker1",
            role=AgentRole.WORKER,
            capabilities=["ssh", "docker"],
        )
        defaults.update(kwargs)
        return AgentInfo(**defaults)

    def test_defaults(self):
        agent = self._make()
        assert agent.endpoint is None
        assert agent.metadata == {}
        assert agent.last_heartbeat is None
        assert agent.status == "active"

    def test_custom_endpoint(self):
        agent = self._make(endpoint="http://localhost:9000")
        assert agent.endpoint == "http://localhost:9000"

    def test_capabilities_stored(self):
        agent = self._make(capabilities=["a", "b", "c"])
        assert "b" in agent.capabilities


# ─────────────────────────────────────────────────────────────
# AgentMessage
# ─────────────────────────────────────────────────────────────


class TestAgentMessage:
    def _make(self, **kwargs):
        ts = datetime(2024, 1, 15, 10, 0, 0)
        defaults = dict(
            message_id="msg-001",
            message_type=MessageType.REQUEST,
            from_agent="alpha",
            to_agent="beta",
            content={"cmd": "deploy"},
            timestamp=ts,
        )
        defaults.update(kwargs)
        return AgentMessage(**defaults)

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        assert d["message_id"] == "msg-001"
        assert d["message_type"] == "request"
        assert d["from_agent"] == "alpha"
        assert d["to_agent"] == "beta"
        assert d["content"] == {"cmd": "deploy"}

    def test_to_dict_timestamp_is_string(self):
        d = self._make().to_dict()
        assert isinstance(d["timestamp"], str)
        assert "2024" in d["timestamp"]

    def test_to_dict_default_ttl(self):
        d = self._make().to_dict()
        assert d["ttl"] == 60

    def test_from_dict_roundtrip(self):
        original = self._make()
        d = original.to_dict()
        restored = AgentMessage.from_dict(d)
        assert restored.message_id == original.message_id
        assert restored.message_type == original.message_type
        assert restored.from_agent == original.from_agent
        assert restored.content == original.content
        assert restored.ttl == original.ttl

    def test_from_dict_optional_reply_to(self):
        original = self._make(reply_to="msg-000")
        restored = AgentMessage.from_dict(original.to_dict())
        assert restored.reply_to == "msg-000"

    def test_from_dict_missing_reply_to_defaults_none(self):
        d = self._make().to_dict()
        d.pop("reply_to", None)
        restored = AgentMessage.from_dict(d)
        assert restored.reply_to is None

    def test_broadcast_to_agent(self):
        msg = self._make(to_agent="*", message_type=MessageType.BROADCAST)
        assert msg.to_agent == "*"
        assert msg.message_type == MessageType.BROADCAST


# ─────────────────────────────────────────────────────────────
# TaskRequest / TaskResult
# ─────────────────────────────────────────────────────────────


class TestTaskRequest:
    def _make(self, **kwargs):
        defaults = dict(
            task_id="t-001",
            task_type="deploy",
            description="Deploy to staging",
            parameters={"env": "staging"},
        )
        defaults.update(kwargs)
        return TaskRequest(**defaults)

    def test_defaults(self):
        req = self._make()
        assert req.priority == 5
        assert req.timeout == 300
        assert req.require_confirmation is False

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert d["task_id"] == "t-001"
        assert d["task_type"] == "deploy"
        assert d["parameters"] == {"env": "staging"}
        assert d["priority"] == 5
        assert d["require_confirmation"] is False

    def test_custom_priority(self):
        d = self._make(priority=9, timeout=60, require_confirmation=True).to_dict()
        assert d["priority"] == 9
        assert d["timeout"] == 60
        assert d["require_confirmation"] is True


class TestTaskResult:
    def _make(self, **kwargs):
        defaults = dict(task_id="t-001", success=True, result="deployed")
        defaults.update(kwargs)
        return TaskResult(**defaults)

    def test_success_defaults(self):
        r = self._make()
        assert r.error is None
        assert r.execution_time == 0.0

    def test_failure(self):
        r = self._make(success=False, error="timeout", execution_time=30.5)
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"
        assert d["execution_time"] == 30.5

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert "task_id" in d
        assert "success" in d
        assert "result" in d
        assert "error" in d
        assert "execution_time" in d


# ─────────────────────────────────────────────────────────────
# AgentRegistry
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def registry():
    return AgentRegistry()


def _agent(aid="a1", name="A1", role=AgentRole.WORKER, caps=None):
    return AgentInfo(
        agent_id=aid,
        name=name,
        role=role,
        capabilities=caps or ["ssh"],
    )


class TestAgentRegistry:
    def test_register_returns_true(self, registry):
        assert registry.register(_agent()) is True

    def test_get_after_register(self, registry):
        a = _agent()
        registry.register(a)
        assert registry.get("a1") is not None
        assert registry.get("a1").name == "A1"

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("does-not-exist") is None

    def test_list_all_empty(self, registry):
        assert registry.list_all() == []

    def test_list_all_after_register(self, registry):
        registry.register(_agent("a1"))
        registry.register(_agent("a2", name="A2"))
        ids = {a.agent_id for a in registry.list_all()}
        assert ids == {"a1", "a2"}

    def test_unregister_known(self, registry):
        registry.register(_agent())
        assert registry.unregister("a1") is True
        assert registry.get("a1") is None

    def test_unregister_unknown_returns_false(self, registry):
        assert registry.unregister("ghost") is False

    def test_find_by_capability_exact(self, registry):
        registry.register(_agent("a1", caps=["ssh", "docker"]))
        registry.register(_agent("a2", name="A2", caps=["docker"]))
        ssh_agents = registry.find_by_capability("ssh")
        assert len(ssh_agents) == 1
        assert ssh_agents[0].agent_id == "a1"

    def test_find_by_capability_multiple(self, registry):
        registry.register(_agent("a1", caps=["docker"]))
        registry.register(_agent("a2", name="A2", caps=["docker"]))
        docker_agents = registry.find_by_capability("docker")
        assert len(docker_agents) == 2

    def test_find_by_capability_missing_returns_empty(self, registry):
        registry.register(_agent("a1", caps=["ssh"]))
        assert registry.find_by_capability("unknown") == []

    def test_find_by_role(self, registry):
        registry.register(_agent("a1", role=AgentRole.WORKER))
        registry.register(_agent("a2", name="A2", role=AgentRole.COORDINATOR))
        workers = registry.find_by_role(AgentRole.WORKER)
        assert len(workers) == 1
        assert workers[0].agent_id == "a1"

    def test_update_heartbeat_sets_timestamp(self, registry):
        registry.register(_agent("a1"))
        registry.update_heartbeat("a1")
        agent = registry.get("a1")
        assert agent.last_heartbeat is not None
        assert isinstance(agent.last_heartbeat, datetime)

    def test_update_heartbeat_unknown_noop(self, registry):
        # Should not raise
        registry.update_heartbeat("ghost")

    def test_capabilities_index_cleared_on_unregister(self, registry):
        registry.register(_agent("a1", caps=["docker"]))
        registry.unregister("a1")
        assert registry.find_by_capability("docker") == []
