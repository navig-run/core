"""Unit tests for navig.connectors.types — pure enums, dataclasses, and to_dict()."""

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

# ---------------------------------------------------------------------------
# ConnectorDomain
# ---------------------------------------------------------------------------


class TestConnectorDomain:
    def test_is_str_enum(self):
        assert ConnectorDomain.COMMUNICATION.value == "communication"

    def test_has_expected_members(self):
        names = {m.name for m in ConnectorDomain}
        for name in ("COMMUNICATION", "CALENDAR", "FILE_STORAGE", "DEV", "DATA"):
            assert name in names

    def test_str_equality(self):
        assert ConnectorDomain.DEV == "dev"


# ---------------------------------------------------------------------------
# ConnectorStatus
# ---------------------------------------------------------------------------


class TestConnectorStatus:
    def test_values(self):
        assert ConnectorStatus.DISCONNECTED.value == "disconnected"
        assert ConnectorStatus.CONNECTED.value == "connected"
        assert ConnectorStatus.ERROR.value == "error"

    def test_five_members(self):
        assert len(ConnectorStatus) == 5

    def test_degraded_exists(self):
        assert ConnectorStatus.DEGRADED.value == "degraded"


# ---------------------------------------------------------------------------
# ResourceType
# ---------------------------------------------------------------------------


class TestResourceType:
    def test_generic_default(self):
        assert ResourceType.GENERIC.value == "generic"

    def test_common_types_exist(self):
        for name in ("EMAIL", "FILE", "TASK", "MESSAGE", "DOCUMENT", "CONTACT"):
            assert hasattr(ResourceType, name)

    def test_pull_request_exists(self):
        assert ResourceType.PULL_REQUEST.value == "pull_request"


# ---------------------------------------------------------------------------
# ActionType
# ---------------------------------------------------------------------------


class TestActionType:
    def test_expected_actions(self):
        for name in ("CREATE", "UPDATE", "DELETE", "SEND", "MOVE"):
            assert hasattr(ActionType, name)

    def test_delete_value(self):
        assert ActionType.DELETE.value == "delete"

    def test_send_value(self):
        assert ActionType.SEND.value == "send"


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


_MINIMAL_RESOURCE = dict(
    id="res-1",
    source="gmail",
    title="Meeting invite",
    preview="Please join the standup at 10am",
)


class TestResource:
    def test_instantiation(self):
        r = Resource(**_MINIMAL_RESOURCE)
        assert r.id == "res-1"
        assert r.source == "gmail"

    def test_defaults(self):
        r = Resource(**_MINIMAL_RESOURCE)
        assert r.url == ""
        assert r.timestamp == ""
        assert r.resource_type == ResourceType.GENERIC
        assert r.metadata == {}

    def test_to_dict_keys(self):
        r = Resource(**_MINIMAL_RESOURCE)
        d = r.to_dict()
        for key in (
            "id",
            "source",
            "title",
            "preview",
            "url",
            "timestamp",
            "resource_type",
            "metadata",
        ):
            assert key in d

    def test_to_dict_resource_type_value(self):
        r = Resource(**_MINIMAL_RESOURCE)
        d = r.to_dict()
        assert d["resource_type"] == "generic"

    def test_to_dict_custom_type(self):
        r = Resource(**{**_MINIMAL_RESOURCE, "resource_type": ResourceType.EMAIL})
        assert r.to_dict()["resource_type"] == "email"

    def test_metadata_separate_instances(self):
        a = Resource(**_MINIMAL_RESOURCE)
        b = Resource(**_MINIMAL_RESOURCE)
        a.metadata["k"] = 1
        assert "k" not in b.metadata


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


class TestAction:
    def test_minimal_construction(self):
        a = Action(action_type=ActionType.CREATE)
        assert a.action_type == ActionType.CREATE
        assert a.connector_id is None
        assert a.resource_id is None
        assert a.params == {}

    def test_full_construction(self):
        a = Action(
            action_type=ActionType.SEND,
            connector_id="gmail",
            resource_id="thread-1",
            params={"body": "hello"},
        )
        assert a.connector_id == "gmail"
        assert a.params["body"] == "hello"

    def test_params_separate_instances(self):
        a = Action(action_type=ActionType.CREATE)
        b = Action(action_type=ActionType.CREATE)
        a.params["x"] = 1
        assert "x" not in b.params


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------


class TestActionResult:
    def test_success(self):
        r = ActionResult(success=True)
        assert r.success is True
        assert r.resource is None
        assert r.error is None

    def test_failure(self):
        r = ActionResult(success=False, error="Not found")
        assert r.success is False
        assert r.error == "Not found"

    def test_to_dict_success_minimal(self):
        r = ActionResult(success=True)
        d = r.to_dict()
        assert d == {"success": True}

    def test_to_dict_with_error(self):
        r = ActionResult(success=False, error="timeout")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"

    def test_to_dict_with_resource(self):
        res = Resource(**_MINIMAL_RESOURCE)
        r = ActionResult(success=True, resource=res)
        d = r.to_dict()
        assert "resource" in d
        assert d["resource"]["id"] == "res-1"


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatus:
    def test_construction(self):
        h = HealthStatus(ok=True, latency_ms=12.5)
        assert h.ok is True
        assert h.latency_ms == 12.5
        assert h.degraded is False
        assert h.message == ""

    def test_checked_at_is_set(self):
        h = HealthStatus(ok=True, latency_ms=5.0)
        assert isinstance(h.checked_at, str)
        assert len(h.checked_at) > 0

    def test_to_dict_keys(self):
        h = HealthStatus(ok=False, latency_ms=999.0, degraded=True, message="slow")
        d = h.to_dict()
        for key in ("ok", "latency_ms", "degraded", "message", "checked_at"):
            assert key in d

    def test_to_dict_rounds_latency(self):
        h = HealthStatus(ok=True, latency_ms=12.3456789)
        d = h.to_dict()
        assert d["latency_ms"] == round(12.3456789, 2)

    def test_to_dict_degraded(self):
        h = HealthStatus(ok=True, latency_ms=10.0, degraded=True)
        assert h.to_dict()["degraded"] is True
