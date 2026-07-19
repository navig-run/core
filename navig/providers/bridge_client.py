"""
bridge_client.py — core's JSON-RPC 2.0 client for the `core-providers-bridge`.

The bridge is a Node sidecar that runs the Pi SDK (Codex/Copilot + the
OpenAI-compatible provider matrix) which Python can't run natively. Core owns
all business state; the bridge is a stateless engine. Transport is JSON-RPC 2.0
over stdio, reusing the proven ndjson stdio plumbing in
:class:`navig.mcp.transport.StdioTransport` (subprocess spawn, id-correlated
response futures, timeouts).

Contract methods (versioned handshake): ``handshake · catalog · detect ·
startAuth · authStatus · cancelAuth · validate · listModels · refresh ·
shutdown``. The bridge never persists/logs secrets; secrets are passed per call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.mcp.transport import StdioTransport
from navig.providers.connection_types import (
    Capability,
    DriverError,
    HealthState,
)
from navig.providers.drivers.base import (
    AuthStart,
    AuthStatus,
    ModelInfo,
    ProviderDriver,
    ValidationResult,
)

logger = logging.getLogger(__name__)

BRIDGE_PROTOCOL = "1.0"


class BridgeError(DriverError):
    code = "bridge_error"


class BridgeClient:
    """Spawns the bridge subprocess and speaks JSON-RPC 2.0 over stdio."""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | None = None):
        self._transport = StdioTransport(command, args or [], env, cwd)
        self._id = 0
        self._started = False
        self.server_info: dict[str, Any] = {}

    async def start(self) -> None:
        await self._transport.connect()
        ack = await self.call("handshake", {"protocol": BRIDGE_PROTOCOL})
        server_proto = str((ack or {}).get("protocol", ""))
        if server_proto.split(".")[0] != BRIDGE_PROTOCOL.split(".")[0]:
            await self.shutdown()
            raise BridgeError(
                f"Bridge protocol mismatch: client {BRIDGE_PROTOCOL}, server {server_proto}"
            )
        self.server_info = ack or {}
        self._started = True

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        raw = await self._transport.send(json.dumps(req))
        if raw is None:
            raise BridgeError(f"No response for {method}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"Invalid bridge response for {method}: {exc}")
        if data.get("error"):
            err = data["error"]
            raise BridgeError(err.get("message", "bridge error"),
                              code=str(err.get("code", "bridge_error")))
        return data.get("result")

    async def shutdown(self) -> None:
        if self._started:
            try:
                await self.call("shutdown")
            except Exception:  # noqa: BLE001 — best-effort
                pass
        self._started = False
        await self._transport.disconnect()


class PiDriver(ProviderDriver):
    """Driver that delegates to the Pi engine via the bridge. Stateless w.r.t.
    business data; the orchestrator owns vault + connection records."""

    driver = "pi"
    advertised_capabilities = {
        Capability.AUTH,
        Capability.MODEL_DISCOVERY,
        Capability.INFERENCE,
    }

    def __init__(self, client: BridgeClient):
        self._client = client

    async def start_auth(self, template_id, *, api_key=None, endpoint=None, **kwargs) -> AuthStart:
        r = await self._client.call("startAuth", {
            "template_id": template_id, "api_key": api_key, "endpoint": endpoint,
        }) or {}
        return AuthStart(
            flow=r.get("flow", "none"), auth_url=r.get("auth_url"),
            user_code=r.get("user_code"), verification_uri=r.get("verification_uri"),
            handle=r.get("handle"), poll_interval_s=r.get("poll_interval_s"),
        )

    async def auth_status(self, handle: str) -> AuthStatus:
        r = await self._client.call("authStatus", {"handle": handle}) or {}
        return AuthStatus(state=r.get("state", "authorizing"), secret_ref=r.get("secret_ref"),
                          error_code=r.get("error_code"), error_message=r.get("error_message"))

    def cancel_auth(self, handle: str) -> None:
        # Fire-and-forget; the bridge tolerates an unknown handle.
        import asyncio

        try:
            asyncio.get_event_loop().create_task(self._client.call("cancelAuth", {"handle": handle}))
        except Exception:  # noqa: BLE001
            pass

    async def validate(self, *, secret_ref, endpoint=None, model=None) -> ValidationResult:
        r = await self._client.call("validate", {
            "secret_ref": secret_ref, "endpoint": endpoint, "model": model,
        }) or {}
        return ValidationResult(
            ok=bool(r.get("ok")),
            health=r.get("health", HealthState.UNKNOWN.value),
            error_code=r.get("error_code"),
            error_message=r.get("error_message"),
            models=[ModelInfo(id=m["id"], display_name=m.get("display_name"))
                    for m in (r.get("models") or [])],
        )

    async def list_models(self, *, secret_ref, endpoint=None) -> list[ModelInfo]:
        r = await self._client.call("listModels", {"secret_ref": secret_ref, "endpoint": endpoint}) or {}
        return [ModelInfo(id=m["id"], display_name=m.get("display_name"))
                for m in (r.get("models") or [])]

    async def refresh(self, *, secret_ref) -> AuthStatus:
        r = await self._client.call("refresh", {"secret_ref": secret_ref}) or {}
        return AuthStatus(state=r.get("state", "connected"), secret_ref=r.get("secret_ref", secret_ref))
