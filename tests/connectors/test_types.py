"""Tests for navig.connectors.types — shared enums and dataclasses."""

from __future__ import annotations

from navig.connectors.types import (
    Action,
    ActionResult,
    ActionType,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)
import pytest

pytestmark = pytest.mark.unit


class TestConnectorDomain:
    def test_communication_value(self):
        assert ConnectorDomain.COMMUNICATION.value == "communication"

    def test_calendar_value(self):
        assert ConnectorDomain.CALENDAR.value == "calendar"

    def test_all_domains_are_strings(self):
        for d in ConnectorDomain:
            assert isinstance(d.value, str)


class TestConnectorStatus:
    def test_default_is_disconnected(self):
        assert ConnectorStatus.DISCONNECTED.value == "disconnected"

    def test_all_statuses(self):
        expected = {"disconnected", "connecting", "connected", "degraded", "error"}
        actual = {s.value for s in ConnectorStatus}
        assert actual == expected


class TestResource:
    def test_to_dict_contains_all_fields(self):
        r = Resource(
            id="123",
            source="gmail",
            title="Test Email",
            preview="Hello world",
            url="https://mail.google.com/123",
            timestamp="2024-01-01T00:00:00Z",
            resource_type=ResourceType.EMAIL,
            metadata={"from": "test@example.com"},
        )
        d = r.to_dict()
        assert d["id"] == "123"
        assert d["source"] == "gmail"
        assert d["title"] == "Test Email"
        assert d["resource_type"] == "email"
        assert d["metadata"]["from"] == "test@example.com"

    def test_to_dict_default_metadata(self):
        r = Resource(id="1", source="test", title="T", preview="P")
        d = r.to_dict()
        assert d["metadata"] == {}
        assert d["resource_type"] == "generic"
        assert d["url"] == ""
        assert d["timestamp"] == ""

    def test_resource_type_values(self):
        assert ResourceType.EMAIL.value == "email"
        assert ResourceType.EVENT.value == "event"
        assert ResourceType.FILE.value == "file"


class TestAction:
    def test_action_defaults(self):
        a = Action(action_type=ActionType.SEND)
        assert a.resource_id is None
        assert a.params == {}
        assert a.connector_id is None

    def test_action_with_all_fields(self):
        a = Action(
            action_type=ActionType.REPLY,
            resource_id="msg-1",
            params={"body": "Thanks!"},
            connector_id="gmail",
        )
        assert a.action_type == ActionType.REPLY
        assert a.resource_id == "msg-1"
        assert a.params["body"] == "Thanks!"
        assert a.connector_id == "gmail"


class TestActionResult:
    def test_success_result(self):
        r = ActionResult(success=True)
        assert r.success is True
        assert r.error is None
        assert r.resource is None

    def test_failure_result(self):
        r = ActionResult(success=False, error="Something broke")
        assert r.success is False
        assert r.error == "Something broke"


class TestHealthStatus:
    def test_healthy(self):
        h = HealthStatus(ok=True, latency_ms=42.5)
        assert h.ok is True
        assert h.latency_ms == 42.5
        assert h.message == ""

    def test_unhealthy(self):
        h = HealthStatus(ok=False, latency_ms=0.0, message="timeout")
        assert h.ok is False
        assert h.message == "timeout"
