"""
NAVIG Connections — Phase 0 contracts (model, enums, error taxonomy, capabilities).

A *connection* is an **instance**, not a provider slug. It carries an immutable
UUID primary key so provider slugs are never used as destructive route keys.
Secrets live in the vault; a connection holds only a ``secret_ref`` (vault label).

This module is pure data + contracts (no I/O), so it is trivially testable and
importable from the store, the bridge client, the deck routes, and the CLI.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Enums — the closed vocabularies the whole system agrees on
# ─────────────────────────────────────────────────────────────────────────────


class Driver(str, Enum):
    """Who authenticates and who runs inference for a connection.

    native   — API key in NAVIG vault; inference via ``llm_router``.
    local    — loopback runtime (Ollama/LM Studio/vLLM); OpenAI-compatible.
    pi       — curated OpenAI-compatible / preset providers via the Pi bridge.
    external — a coding-agent subscription (Claude Code/Codex/Copilot) whose auth
               and inference are delegated to the OFFICIAL runtime; NAVIG stores
               only a reference, never copied credentials.
    codex / copilot — token-runtime variants routed through the bridge, exposed
               only once a routable adapter passes the capability matrix.
    """

    NATIVE = "native"
    LOCAL = "local"
    PI = "pi"
    EXTERNAL = "external"
    CODEX = "codex"
    COPILOT = "copilot"


class AuthState(str, Enum):
    DETECTED_EXTERNAL = "detected_external"
    AUTHORIZING = "authorizing"
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"


class HealthState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    INVALID = "invalid"


class Capability(str, Enum):
    """What a connection can actually do. The router/UI must never assume an
    unadvertised capability — this is how ``external`` connections show as
    "Connected externally — not yet routable" instead of falsely "Ready"."""

    AUTH = "auth"
    MODEL_DISCOVERY = "model_discovery"
    INFERENCE = "inference"
    TOOLS = "tools"


class UiState(str, Enum):
    """Honest, user-facing connection state. ``READY`` is reserved for a
    connection with real NAVIG inference capability."""

    AVAILABLE = "available"
    NOT_INSTALLED = "not_installed"
    DETECTED_EXTERNALLY = "detected_externally"
    AUTHORIZING = "authorizing"
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    CONNECTED_EXTERNAL_NOT_ROUTABLE = "connected_external_not_routable"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


# ─────────────────────────────────────────────────────────────────────────────
# Error taxonomy — structured, code-tagged, safe to serialize (no secrets)
# ─────────────────────────────────────────────────────────────────────────────


class ConnectionError(Exception):
    """Base for all connection-service errors. ``code`` is a stable machine tag."""

    code: str = "connection_error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class ConnectionNotFound(ConnectionError):
    code = "not_found"


class RevisionConflict(ConnectionError):
    """Optimistic-concurrency guard: the record changed under you."""

    code = "revision_conflict"


class ConnectionValidationError(ConnectionError):
    code = "validation_error"


class DriverError(ConnectionError):
    code = "driver_error"


class VaultUnavailable(ConnectionError):
    code = "vault_unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Connection — the instance record
# ─────────────────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


def new_connection_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Connection:
    """An instance-based AI provider connection (control-plane record).

    The secret never lives here — ``secret_ref`` is a vault label. ``revision``
    is bumped on every persisted mutation for optimistic concurrency.
    """

    connection_id: str
    template_id: str
    name: str
    driver: Driver
    secret_ref: str | None = None
    capabilities: set[Capability] = field(default_factory=set)
    auth_state: AuthState = AuthState.AUTHORIZING
    health_state: HealthState = HealthState.UNKNOWN
    revision: int = 1
    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    is_default: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)

    # ── capability helpers ──────────────────────────────────────────────────
    def has(self, cap: Capability) -> bool:
        return cap in self.capabilities

    @property
    def is_routable(self) -> bool:
        """True iff NAVIG can actually run inference through this connection."""
        return Capability.INFERENCE in self.capabilities and self.auth_state in (
            AuthState.CONNECTED,
        )

    # ── derived UI state (honest) ───────────────────────────────────────────
    def ui_state(self, *, enabled: bool = True) -> UiState:
        if not enabled:
            return UiState.DISABLED
        if self.auth_state == AuthState.AUTHORIZING:
            return UiState.AUTHORIZING
        if self.auth_state == AuthState.NEEDS_REAUTH:
            return UiState.NEEDS_REAUTH
        if self.auth_state == AuthState.DETECTED_EXTERNAL:
            return UiState.DETECTED_EXTERNALLY
        if self.auth_state == AuthState.CONNECTED:
            if self.health_state in (HealthState.UNREACHABLE, HealthState.INVALID):
                return UiState.UNHEALTHY
            if self.is_routable:
                return UiState.READY
            # connected (e.g. external runtime) but no NAVIG inference capability
            return UiState.CONNECTED_EXTERNAL_NOT_ROUTABLE
        return UiState.AVAILABLE

    # ── serialization (no secrets ever) ─────────────────────────────────────
    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for UI/CLI. Carries ``secret_ref`` (a label) but never a secret."""
        return {
            "connection_id": self.connection_id,
            "template_id": self.template_id,
            "name": self.name,
            "driver": self.driver.value,
            "secret_ref": self.secret_ref,
            "capabilities": sorted(c.value for c in self.capabilities),
            "auth_state": self.auth_state.value,
            "health_state": self.health_state.value,
            "ui_state": self.ui_state().value,
            "is_routable": self.is_routable,
            "revision": self.revision,
            "models": list(self.models),
            "default_model": self.default_model,
            "is_default": self.is_default,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
