"""CyberAIGen Connector — external breach / password-leak lookups.

Authentication: personal access token (``Authorization: Bearer``), stored in the
navig vault under ``cyberaigen/token`` (or ``CYBERAIGEN_TOKEN``). The token is
minted on the CyberAIGen side with ``php artisan token:create <email>``.

The API is the machine endpoint ``POST {base_url}/api/v1/check`` on a
CyberAIGen deployment:

    {"search": "alice@example.com", "filter": "all"}
    → {"status": "found" | "secure", "count": N, "results": [...],
       "session_id": "...", "tokens_used": N, "balance": N}

It is credit-metered: one credit per search term, charged only when records are
found. ``base_url`` is REQUIRED and never defaulted — every operator points it
at their own deployment (``navig connector connect cyberaigen`` prompts for it
and stores it in the vault, like every other connector credential), and no
host's address is ever baked into the client, logged, or left in history.

Privacy: ``check()`` is the full-fidelity path (returns raw records, including
leaked passwords) and is what the breach monitor / ``navig vault breach`` CLI
use. The generic ``search()`` surface deliberately returns **masked** passwords
only, so a leak record never spills through an agent/MCP search by accident.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAPIError, ConnectorAuthError
from navig.connectors.secrets import KeyField, resolve
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

logger = logging.getLogger("navig.connectors.cyberaigen")

CYBERAIGEN_TOKEN = KeyField(
    name="token",
    label="CyberAIGen API token",
    vault_label="cyberaigen/token",
    env=("CYBERAIGEN_TOKEN",),
    placeholder="personal access token (token:create)",
)
CYBERAIGEN_BASE_URL = KeyField(
    name="base_url",
    label="Breach API base URL",
    vault_label="cyberaigen/base_url",
    env=("CYBERAIGEN_BASE_URL",),
    secret=False,
    placeholder="https://your-breach-api.example/app",
)

_REQUEST_TIMEOUT_S = 30.0


def scrub_endpoint(text: Any) -> str:
    """Replace every URL in *text* with ``<endpoint>``.

    Transport exceptions embed the full request URL, and those strings travel
    into state, logs and failure notifications — an endpoint must never land in
    history, so anything that reports an error passes through here first.
    """
    import re

    return re.sub(r"https?://\S+", "<endpoint>", str(text))


def mask_password(value: Any) -> str:
    """Render a leaked password safe for a notification / default CLI row.

    ``Hashed`` / ``Encrypted`` are labels the API returns instead of a plaintext
    password — they pass through. A real password shows its first and last
    character with asterisks between (``p******9``); anything ≤ 3 characters is
    fully asterisked so no useful fragment leaks.
    """
    text = str(value or "")
    if not text:
        return ""
    if text in ("Hashed", "Encrypted"):
        return text
    if len(text) <= 3:
        return "*" * len(text)
    return f"{text[0]}{'*' * min(len(text) - 2, 8)}{text[-1]}"


class CyberAigenConnector(BaseConnector):
    """Breach / password-leak lookups against a CyberAIGen deployment.

    Auth: personal access token via ``CYBERAIGEN_TOKEN`` or the vault label
    ``cyberaigen/token``. No OAuth.
    """

    manifest = ConnectorManifest(
        id="cyberaigen",
        display_name="CyberAIGen",
        description="Password-leak and data-breach lookups — check whether an email appears in known dumps.",
        domain=ConnectorDomain.AUTH,
        icon="🔓",
        oauth_scopes=[],
        oauth_provider="",
        requires_oauth=False,
        auth_mode="api_key",
        key_fields=(CYBERAIGEN_TOKEN, CYBERAIGEN_BASE_URL),
        can_search=True,
        can_fetch=False,
        can_act=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._token: str | None = None
        self._base_url: str = ""

    # -- Connection lifecycle ----------------------------------------------

    async def connect(self) -> None:
        """Load the token and base URL (env, then vault) — both required."""
        token = resolve(CYBERAIGEN_TOKEN)
        if not token:
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                "cyberaigen",
                "token not found. Set CYBERAIGEN_TOKEN or store it in the vault under "
                "'cyberaigen/token' (mint one with `php artisan token:create`).",
            )
        self._token = token
        base = resolve(CYBERAIGEN_BASE_URL).rstrip("/")
        if not base:
            # No baked-in default: every operator connects THEIR OWN deployment
            # (vault/env), and no host's address ships inside the client.
            self._status = ConnectorStatus.ERROR
            raise ValueError(
                "Breach API base URL not found. Set CYBERAIGEN_BASE_URL or store it in "
                "the vault under 'cyberaigen/base_url' (navig connector connect cyberaigen)."
            )
        self._base_url = base
        self._status = ConnectorStatus.CONNECTED
        # Deliberately no logging here: the endpoint itself must never enter
        # the log/history stream (operator requirement).

    async def disconnect(self) -> None:
        self._token = None
        self._status = ConnectorStatus.DISCONNECTED

    # -- Core operations ---------------------------------------------------

    async def check(self, search: str, filter_type: str = "all") -> dict[str, Any]:
        """Run one breach scan and return the raw API contract.

        Returns ``{"status", "count", "results", "session_id", "tokens_used",
        "balance"}`` — records include the leaked ``password`` field, so this is
        the full-fidelity path (monitor + CLI). Raises :class:`ConnectorAuthError`
        on 401/403, :class:`ConnectorAPIError` on 402 (out of credits) and other
        non-200 responses.
        """
        self._ensure_connected()
        return await self._post_check({"search": search, "filter": filter_type})

    async def search(self, query: str, limit: int = 5, **kwargs: Any) -> list[Resource]:
        """Breach lookup as generic resources — **passwords are masked here**.

        Use :meth:`check` when the caller (the breach monitor, the vault CLI)
        legitimately needs the raw records; this agent/MCP-facing surface only
        ever returns masked fragments.
        """
        data = await self.check(query)
        results = list(data.get("results") or [])
        resources: list[Resource] = []
        for i, rec in enumerate(results[:limit]):
            if not isinstance(rec, dict):
                continue
            email = str(rec.get("email") or query)
            db = str(rec.get("databaseName") or "unknown source")
            pw = mask_password(rec.get("password"))
            resources.append(
                Resource(
                    id=f"cyberaigen:{email}:{db}:{rec.get('createdAt', '')}",
                    source="cyberaigen",
                    title=f"Breach: {email} @ {db}",
                    preview=f"{db} ({rec.get('createdAt', 'date unknown')}) — password {pw}",
                    timestamp=str(rec.get("createdAt") or ""),
                    resource_type=ResourceType.GENERIC,
                    metadata={
                        "email": email,
                        "database_name": db,
                        "created_at": rec.get("createdAt"),
                        "password_masked": pw,
                        "compromised_data": rec.get("compromisedData"),
                    },
                )
            )
        return resources

    async def fetch(self, resource_id: str, **kwargs: Any) -> Resource | None:
        """CyberAIGen has no persistent resource store — always returns None."""
        return None

    async def act(self, action: Action) -> ActionResult:
        """CyberAIGen lookups are read-only — no write actions supported."""
        return ActionResult(
            success=False,
            error="CyberAigen connector does not support write actions.",
        )

    async def health_check(self) -> HealthStatus:
        """Cheap liveness probe: an UNAUTHENTICATED POST must return 401.

        That proves the deployment is up and the auth gate answers, without
        spending a scan credit. A token is not required for the probe itself.
        """
        import httpx

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/check",
                    json={"search": "health-probe@example.com"},
                    headers={"Accept": "application/json"},
                )
            latency_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code == 401:
                return HealthStatus(ok=True, latency_ms=latency_ms, message="API up (401 expected)")
            if resp.status_code in (400, 422):
                # Up and validating — the gate answered correctly enough.
                return HealthStatus(ok=True, latency_ms=latency_ms, message="API up")
            return HealthStatus(
                ok=False,
                latency_ms=latency_ms,
                message=f"unexpected status {resp.status_code}",
            )
        except Exception as exc:  # noqa: BLE001 — reported as an unhealthy probe
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                ok=False, latency_ms=latency_ms, message=scrub_endpoint(exc)
            )

    # -- Internal helpers --------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._status != ConnectorStatus.CONNECTED or not self._token:
            raise ConnectorAuthError(
                "cyberaigen", "connector is not connected — run connect() first."
            )

    async def _post_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/check and normalise error responses into connector errors."""
        import httpx

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/check", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            # Transport exceptions embed the request URL — scrub before the
            # message can reach state, logs or a notification.
            raise ConnectorAPIError(
                "cyberaigen", 0, f"CyberAIGen unreachable: {scrub_endpoint(exc)}"
            ) from exc

        if resp.status_code in (401, 403):
            raise ConnectorAuthError(
                "cyberaigen", "the API rejected the token (401) — refresh it."
            )
        if resp.status_code == 402:
            raise ConnectorAPIError("cyberaigen", 402, "insufficient scan credits")
        if resp.status_code >= 500:
            raise ConnectorAPIError(
                "cyberaigen", resp.status_code, "server error — the CyberAIGen app is failing"
            )
        if resp.status_code != 200:
            detail = ""
            try:
                detail = str(resp.json().get("error") or "")
            except Exception:  # noqa: BLE001 — non-JSON error body
                detail = resp.text[:200]
            raise ConnectorAPIError("cyberaigen", resp.status_code, detail)

        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorAPIError(
                "cyberaigen", 200, "returned non-JSON output"
            ) from exc
        if not isinstance(data, dict):
            raise ConnectorAPIError("cyberaigen", 200, "returned an unexpected payload")
        return data
