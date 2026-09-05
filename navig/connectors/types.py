"""
Connector Engine — Shared Type Definitions

Canonical types used across all connectors:
- ``ConnectorDomain`` — domain classification enum
- ``ConnectorStatus`` — lifecycle status enum
- ``Resource`` — unified search/fetch result
- ``Action`` / ``ActionResult`` — write-operation contracts
- ``HealthStatus`` — health-check response
- ``ResourceType`` — semantic type of a returned resource
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConnectorDomain(str, Enum):
    """Domain classification for connectors."""

    COMMUNICATION = "communication"
    CALENDAR = "calendar"
    FILE_STORAGE = "file_storage"
    PROJECT_MANAGEMENT = "project_management"
    KNOWLEDGE = "knowledge"
    CRM = "crm"
    DEV = "dev"
    DATA = "data"
    PAYMENTS = "payments"
    AI_RESEARCH = "ai_research"
    EVENTS = "events"
    AUTH = "auth"
    MARKETING = "marketing"


class ConnectorStatus(str, Enum):
    """Lifecycle status of a connector instance."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"


class ResourceType(str, Enum):
    """Semantic type of a resource returned by a connector."""

    EMAIL = "email"
    EVENT = "event"
    FILE = "file"
    TASK = "task"
    MESSAGE = "message"
    DOCUMENT = "document"
    CONTACT = "contact"
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    NOTE = "note"
    PAYMENT = "payment"
    GENERIC = "generic"


class ActionType(str, Enum):
    """Standard action types supported across connectors."""

    REPLY = "reply"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ARCHIVE = "archive"
    LABEL = "label"
    SEND = "send"
    MOVE = "move"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Resource:
    """
    Unified result type returned by every connector.

    All connector ``search()`` and ``fetch()`` methods return ``Resource``
    instances so consumers never deal with provider-specific shapes.
    """

    id: str
    source: str  # connector_id, e.g. "gmail", "google_calendar"
    title: str
    preview: str  # truncated body / description
    url: str = ""
    timestamp: str = ""  # ISO 8601
    resource_type: ResourceType = ResourceType.GENERIC
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON / CLI output."""
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "preview": self.preview,
            "url": self.url,
            "timestamp": self.timestamp,
            "resource_type": self.resource_type.value,
            "metadata": self.metadata,
        }


@dataclass
class Action:
    """
    A write-operation request sent to a connector.

    ``params`` carries action-specific data (e.g. email body for reply,
    event summary for create).
    """

    action_type: ActionType
    connector_id: str | None = None
    resource_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Outcome of an ``Action`` execution."""

    success: bool
    resource: Resource | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"success": self.success}
        if self.resource:
            payload["resource"] = self.resource.to_dict()
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class HealthStatus:
    """Result of a connector health check."""

    ok: bool
    # Defaults to 0.0 so a failure detected BEFORE any request was timed (missing dependency,
    # not connected, an exception) can report itself. Five connectors independently wrote
    # `HealthStatus(ok=False, message=…)` on exactly those paths and raised TypeError instead —
    # a health check that crashes precisely when the connector is unhealthy is worse than none.
    latency_ms: float = 0.0
    degraded: bool = False
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "degraded": self.degraded,
            "message": self.message,
            "checked_at": self.checked_at,
        }
