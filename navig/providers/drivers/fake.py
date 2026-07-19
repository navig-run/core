"""
FakeDriver — a complete in-memory implementation of the driver contract.

Lets the connection service be exercised end-to-end (CRUD, auth, validate,
defaults, safe-remove, concurrency) with **no** Node bridge, network, or
official runtime. Configurable to simulate api-key, oauth, device, external,
and failure paths so the contract tests cover every branch.
"""

from __future__ import annotations

from typing import Any

from navig.providers.connection_types import Capability, HealthState
from navig.providers.drivers.base import (
    AuthStart,
    AuthStatus,
    ModelInfo,
    ProviderDriver,
    ValidationResult,
)


class FakeDriver(ProviderDriver):
    driver = "fake"
    advertised_capabilities = {
        Capability.AUTH,
        Capability.MODEL_DISCOVERY,
        Capability.INFERENCE,
    }

    def __init__(
        self,
        *,
        flow: str = "api_key",
        detectable: list[dict[str, Any]] | None = None,
        healthy: bool = True,
        models: list[str] | None = None,
    ):
        self._flow = flow
        self._detectable = detectable or []
        self._healthy = healthy
        self._models = models or ["fake-large", "fake-small"]
        self._pending: dict[str, dict[str, Any]] = {}
        self._counter = 0

    # ── detection ───────────────────────────────────────────────────────────
    def detect(self) -> list[dict[str, Any]]:
        return list(self._detectable)

    # ── auth ────────────────────────────────────────────────────────────────
    async def start_auth(self, template_id, *, api_key=None, endpoint=None, **kwargs) -> AuthStart:
        if self._flow == "api_key":
            # Completes inline: "store" the key and hand back a vault-ish ref.
            ref = f"fake/{template_id}"
            return AuthStart(flow="api_key", handle=None, auth_url=None,
                             user_code=None, verification_uri=None,
                             # secret_ref is conveyed via auth_status for async;
                             # for inline we stash it on the handle map by key
                             )
        if self._flow == "device_code":
            self._counter += 1
            h = f"handle-{self._counter}"
            self._pending[h] = {"polls": 0, "template_id": template_id}
            return AuthStart(flow="device_code", handle=h, user_code="WXYZ-1234",
                             verification_uri="https://example.test/device",
                             poll_interval_s=0.0)
        if self._flow == "oauth_redirect":
            self._counter += 1
            h = f"handle-{self._counter}"
            self._pending[h] = {"polls": 0, "template_id": template_id}
            return AuthStart(flow="oauth_redirect", handle=h,
                             auth_url="https://example.test/oauth", poll_interval_s=0.0)
        return AuthStart(flow="none")

    async def auth_status(self, handle: str) -> AuthStatus:
        st = self._pending.get(handle)
        if st is None:
            return AuthStatus(state="revoked", error_code="unknown_handle",
                              error_message="No such auth flow")
        st["polls"] += 1
        # Authorize on the second poll to exercise the polling loop.
        if st["polls"] >= 2:
            return AuthStatus(state="connected", secret_ref=f"fake/{st['template_id']}")
        return AuthStatus(state="authorizing")

    def cancel_auth(self, handle: str) -> None:
        self._pending.pop(handle, None)

    # ── validate / discovery ────────────────────────────────────────────────
    async def validate(self, *, secret_ref, endpoint=None, model=None) -> ValidationResult:
        if not self._healthy:
            return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                    error_code="validation_error",
                                    error_message="Fake validation failure")
        return ValidationResult(
            ok=True,
            health=HealthState.HEALTHY.value,
            models=[ModelInfo(id=m) for m in self._models],
        )

    async def list_models(self, *, secret_ref, endpoint=None) -> list[ModelInfo]:
        return [ModelInfo(id=m) for m in self._models]

    async def refresh(self, *, secret_ref) -> AuthStatus:
        return AuthStatus(state="connected", secret_ref=secret_ref)
