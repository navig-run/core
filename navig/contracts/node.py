"""
Node — the fundamental identity contract of the NAVIG runtime.

A Node is an autonomous agent instance running on a machine.
It has a stable identity, a set of declared capabilities, and a trust score
computed from its operational history.

State lifecycle:
    provisioning → online → offline / suspended / decommissioned
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

# ── Enums ─────────────────────────────────────────────────────────────────────

class NodeStatus(str, Enum):
    PROVISIONING = "provisioning"   # First-time setup in progress
    ONLINE       = "online"         # Running and accepting missions
    OFFLINE      = "offline"        # Unreachable but expected to return
    SUSPENDED    = "suspended"      # Intentionally paused by operator
    DECOMMISSIONED = "decommissioned"  # Permanently removed


class NodeOS(str, Enum):
    LINUX   = "linux"
    WINDOWS = "windows"
    MACOS   = "macos"
    UNKNOWN = "unknown"


# ── Model ──────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    """
    Typed Node identity contract.

    Attributes:
        node_id:      Globally unique node identifier (UUID4).
        hostname:     Human-readable machine name.
        os:           Operating system tag.
        formation:    Active formation name (project context).
        version:      navig-core version string running on this node.
        capabilities: Declared capability slugs, e.g. ["llm", "ssh", "browser"].
        status:       Lifecycle status.
        trust_score:  Aggregate trust [0.0–1.0] from operational history.
        gateway_url:  HTTP base URL for this node's gateway (e.g. http://host:7771).
        metadata:     Extension point for operator-defined fields.
        created_at:   ISO-8601 timestamp of first registration.
        last_seen:    ISO-8601 timestamp of most recent heartbeat.
    """

    # Required
    hostname: str

    # Auto-generated
    node_id: str            = field(default_factory=lambda: str(uuid.uuid4()))
    os: NodeOS              = NodeOS.UNKNOWN
    formation: str          = ""
    version: str            = "0.0.0"
    capabilities: List[str] = field(default_factory=list)
    status: NodeStatus      = NodeStatus.PROVISIONING
    trust_score: float      = 1.0
    gateway_url: str        = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str         = field(default_factory=lambda: _now_iso())
    last_seen: str          = field(default_factory=lambda: _now_iso())

    # ── Lifecycle transitions ─────────────────────────────────────────

    def go_online(self) -> None:
        if self.status not in (NodeStatus.OFFLINE, NodeStatus.SUSPENDED, NodeStatus.PROVISIONING):
            raise ValueError(f"Cannot go online from {self.status!r}")
        self.status = NodeStatus.ONLINE
        self.touch()

    def go_offline(self) -> None:
        if self.status == NodeStatus.DECOMMISSIONED:
            raise ValueError("Cannot take offline a decommissioned node")
        self.status = NodeStatus.OFFLINE
        self.touch()

    def suspend(self) -> None:
        if self.status != NodeStatus.ONLINE:
            raise ValueError(f"Can only suspend ONLINE nodes, got {self.status!r}")
        self.status = NodeStatus.SUSPENDED
        self.touch()

    def decommission(self) -> None:
        self.status = NodeStatus.DECOMMISSIONED
        self.touch()

    def touch(self) -> None:
        """Update last_seen to now."""
        self.last_seen = _now_iso()

    # ── Capability helpers ────────────────────────────────────────────

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities

    def add_capability(self, cap: str) -> None:
        if cap not in self.capabilities:
            self.capabilities.append(cap)

    def remove_capability(self, cap: str) -> None:
        self.capabilities = [c for c in self.capabilities if c != cap]

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["os"] = self.os.value
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        data = dict(data)
        data["os"] = NodeOS(data.get("os", "unknown"))
        data["status"] = NodeStatus(data.get("status", "provisioning"))
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "Node":
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return f"<Node {self.node_id[:8]} {self.hostname!r} {self.status.value}>"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
