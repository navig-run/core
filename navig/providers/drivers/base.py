"""
Driver contract (Phase 0) — the pluggable backend behind a Connection.

A driver owns the *mechanics* for one class of provider: authentication,
model discovery, validation, and (optionally) inference. The connection
service owns persistence, vault, defaults, and workspace resolution and never
reaches past this interface — so drivers (native HTTP, local probe, the Pi
bridge, official-runtime delegation, or the in-memory fake) are interchangeable.

Contract invariants:
  * Drivers are stateless w.r.t. business data — they receive everything they
    need per call and return structured results.
  * Drivers MUST NOT write secrets to disk and MUST NOT log secret values.
  * Every method returns a typed result or raises ``DriverError`` — never a bare
    exception that leaks provider internals.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from navig.providers.connection_types import Capability


@dataclass
class ModelInfo:
    id: str
    display_name: str | None = None
    context_window: int | None = None
    capabilities: set[Capability] = field(default_factory=set)


@dataclass
class ValidationResult:
    ok: bool
    health: str  # HealthState value
    error_code: str | None = None
    error_message: str | None = None
    models: list[ModelInfo] = field(default_factory=list)


@dataclass
class AuthStart:
    """Returned by ``start_auth``. Carries whatever the surface must show the
    user to complete auth — a browser URL, a device code, or nothing (already
    authenticated / delegated to an external runtime)."""

    flow: str  # "oauth_redirect" | "device_code" | "none" | "api_key"
    auth_url: str | None = None
    user_code: str | None = None
    verification_uri: str | None = None
    handle: str | None = None  # opaque token to poll auth_status / cancel_auth
    poll_interval_s: float | None = None


@dataclass
class AuthStatus:
    state: str  # AuthState value
    secret_ref: str | None = None  # vault label, if a secret was minted/stored
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class ConnectResult:
    auth_state: str
    health_state: str
    secret_ref: str | None = None
    capabilities: set[Capability] = field(default_factory=set)
    models: list[ModelInfo] = field(default_factory=list)
    default_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderDriver(abc.ABC):
    """The backend contract. Implementations: native, local, pi (bridge),
    external (official-runtime delegation), and fake (tests)."""

    #: Stable driver key (matches :class:`navig.providers.connection_types.Driver`).
    driver: str = "abstract"

    #: Capabilities this driver can provide for a connected connection.
    advertised_capabilities: set[Capability] = set()

    # ── detection (external runtimes) ───────────────────────────────────────
    def detect(self) -> list[dict[str, Any]]:
        """Return non-secret descriptors of externally-detected installs/logins.

        Default: drivers that don't represent an external runtime detect nothing.
        Must never read or copy credential files — probe install + authed-state
        signals only.
        """
        return []

    # ── authentication ──────────────────────────────────────────────────────
    # I/O-bound methods are async: native drivers hit HTTP endpoints and the Pi
    # bridge speaks JSON-RPC over a subprocess — both are naturally async.
    @abc.abstractmethod
    async def start_auth(
        self,
        template_id: str,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        **kwargs: Any,
    ) -> AuthStart:
        """Begin authentication. For api-key drivers this may complete inline."""

    async def auth_status(self, handle: str) -> AuthStatus:
        """Poll an in-flight auth flow. Override for OAuth/device flows."""
        raise NotImplementedError

    def cancel_auth(self, handle: str) -> None:
        """Cancel an in-flight auth flow. Best-effort, sync."""
        return None

    # ── validation / discovery ──────────────────────────────────────────────
    @abc.abstractmethod
    async def validate(self, *, secret_ref: str | None, endpoint: str | None = None,
                       model: str | None = None) -> ValidationResult:
        """Test a connection end-to-end and report health + discovered models."""

    async def list_models(self, *, secret_ref: str | None, endpoint: str | None = None) -> list[ModelInfo]:
        """Discover available models. Default: none."""
        return []

    async def refresh(self, *, secret_ref: str | None) -> AuthStatus:
        """Refresh credentials/tokens if supported. Default: no-op (still connected)."""
        return AuthStatus(state="connected", secret_ref=secret_ref)

    # ── lifecycle ───────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        return None
