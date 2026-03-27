"""
Matrix Homeserver Admin Client

Abstracts admin API calls for Conduit and Synapse homeservers.
Used by ``navig matrix registration`` and ``navig matrix admin`` commands.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class MatrixAdminClient:
    """
    Admin API client for Matrix homeservers.

    Supports:
      - Conduit (/_conduit/ endpoints)
      - Synapse (/_synapse/admin/ endpoints)

    Falls back gracefully when an endpoint is unavailable.
    """

    def __init__(
        self,
        homeserver_url: str = "http://localhost:6167",
        admin_token: str = "",
        container_name: str = "navig-conduit",
    ):
        self.homeserver_url = homeserver_url.rstrip("/")
        self.admin_token = admin_token
        self.container_name = container_name
        self._server_type: str | None = (
            None  # "conduit" | "synapse" | "dendrite" | None
        )

    async def _detect_server(self) -> str:
        """Detect which homeserver software is running."""
        if self._server_type:
            return self._server_type

        import httpx

        # Try Conduit
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.homeserver_url}/_conduit/server_version"
                )
                if resp.status_code == 200:
                    self._server_type = "conduit"
                    return "conduit"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Try Synapse admin
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.homeserver_url}/_synapse/admin/v1/server_version",
                    headers=self._auth_headers(),
                )
                if resp.status_code == 200:
                    self._server_type = "synapse"
                    return "synapse"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        self._server_type = "unknown"
        return "unknown"

    def _auth_headers(self) -> dict[str, str]:
        if self.admin_token:
            return {"Authorization": f"Bearer {self.admin_token}"}
        return {}

    # ── Registration management ──

    async def get_registration_status(self) -> bool:
        """Check if open registration is enabled. Returns True if open."""
        server = await self._detect_server()
        import httpx

        if server == "conduit":
            # Conduit: check /_conduit/server_version or ENV-based config
            # Conduit doesn't expose registration status via API; we check via env
            return False  # Default to closed — set via container restart

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        f"{self.homeserver_url}/_synapse/admin/v1/registration_tokens",
                        headers=self._auth_headers(),
                    )
                    # If tokens endpoint works, we know the server; reg status
                    # is in homeserver.yaml, not queryable via API
                    return False
            except Exception:
                return False

        return False

    async def set_registration(self, enabled: bool) -> bool:
        """
        Toggle open registration.

        For Conduit: restarts container with updated env.
        For Synapse: modifies config via admin API (if supported).
        """
        server = await self._detect_server()

        if server == "conduit":
            # Conduit manages registration via config/env
            # Update the config file and restart
            try:
                return await self._conduit_set_registration(enabled)
            except Exception:
                logger.exception("Failed to toggle Conduit registration")
                return False

        logger.warning("Registration toggle not yet implemented for %s", server)
        return False

    async def _conduit_set_registration(self, enabled: bool) -> bool:
        """Toggle registration for Conduit by updating config and restarting."""
        import subprocess

        # Try local docker approach
        value = "true" if enabled else "false"
        try:
            # Update the conduit.toml file
            subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container_name,
                    "sh",
                    "-c",
                    f"sed -i 's/allow_registration = .*/allow_registration = {value}/' /config/conduit.toml",
                ],
                check=True,
                capture_output=True,
            )
            # Restart to pick up config
            subprocess.run(
                ["docker", "restart", self.container_name],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.exception("Docker restart failed for Conduit")
            return False

    # ── Registration tokens ──

    async def create_registration_token(
        self,
        uses_allowed: int = 1,
        expiry: str = "7d",
    ) -> str | None:
        """Create a registration token (Synapse only, Conduit uses open/closed)."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            # Parse expiry → milliseconds
            expiry_ms = self._parse_duration_ms(expiry)

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    data: dict[str, Any] = {}
                    if uses_allowed:
                        data["uses_allowed"] = uses_allowed
                    if expiry_ms:
                        import time

                        data["expiry_time"] = int(time.time() * 1000) + expiry_ms

                    resp = await client.post(
                        f"{self.homeserver_url}/_synapse/admin/v1/registration_tokens/new",
                        headers=self._auth_headers(),
                        json=data,
                    )
                    if resp.status_code == 200:
                        return resp.json().get("token")
            except Exception:
                logger.exception("create_registration_token failed")

        logger.warning("Registration tokens not supported on %s", server)
        return None

    async def list_registration_tokens(self) -> list[dict[str, Any]]:
        """List active registration tokens (Synapse only)."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.homeserver_url}/_synapse/admin/v1/registration_tokens",
                        headers=self._auth_headers(),
                    )
                    if resp.status_code == 200:
                        return resp.json().get("registration_tokens", [])
            except Exception:
                logger.exception("list_registration_tokens failed")

        return []

    async def revoke_registration_token(self, token: str) -> bool:
        """Delete/revoke a registration token (Synapse only)."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.delete(
                        f"{self.homeserver_url}/_synapse/admin/v1/registration_tokens/{token}",
                        headers=self._auth_headers(),
                    )
                    return resp.status_code == 200
            except Exception:
                logger.exception("revoke_registration_token failed")

        return False

    # ── User management ──

    async def list_users(self) -> list[dict[str, Any]]:
        """List all registered users."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.homeserver_url}/_synapse/admin/v2/users",
                        headers=self._auth_headers(),
                        params={"limit": 100},
                    )
                    if resp.status_code == 200:
                        return resp.json().get("users", [])
            except Exception:
                logger.exception("list_users failed")

        if server == "conduit":
            # Conduit has limited admin API — try via /_conduit/admin
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.homeserver_url}/_conduit/admin/users",
                        headers=self._auth_headers(),
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            return [{"user_id": u} for u in data]
                        return data.get("users", [])
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        return []

    async def get_user(self, mxid: str) -> dict[str, Any] | None:
        """Get info about a specific user."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.homeserver_url}/_synapse/admin/v2/users/{mxid}",
                        headers=self._auth_headers(),
                    )
                    if resp.status_code == 200:
                        return resp.json()
            except Exception:
                logger.exception("get_user failed")

        return None

    async def deactivate_user(self, mxid: str) -> bool:
        """Deactivate a user account."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self.homeserver_url}/_synapse/admin/v1/deactivate/{mxid}",
                        headers=self._auth_headers(),
                        json={"erase": False},
                    )
                    return resp.status_code == 200
            except Exception:
                logger.exception("deactivate_user failed")

        return False

    async def reset_password(self, mxid: str, new_password: str) -> bool:
        """Reset a user's password."""
        server = await self._detect_server()
        import httpx

        if server == "synapse":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.put(
                        f"{self.homeserver_url}/_synapse/admin/v1/reset_password/{mxid}",
                        headers=self._auth_headers(),
                        json={"new_password": new_password, "logout_devices": True},
                    )
                    return resp.status_code == 200
            except Exception:
                logger.exception("reset_password failed")

        return False

    # ── Helpers ──

    @staticmethod
    def _parse_duration_ms(duration: str) -> int:
        """Parse duration string (e.g. '7d', '24h', '30m') to milliseconds."""
        match = re.match(r"^(\d+)([dhms])$", duration.strip().lower())
        if not match:
            return 7 * 24 * 3600 * 1000  # Default 7 days

        value = int(match.group(1))
        unit = match.group(2)
        multipliers = {"d": 86400000, "h": 3600000, "m": 60000, "s": 1000}
        return value * multipliers.get(unit, 86400000)


# =============================================================================
# Singleton
# =============================================================================

_admin_client: MatrixAdminClient | None = None


def get_admin_client() -> MatrixAdminClient:
    """Get or create the global admin client from config."""
    global _admin_client
    if _admin_client:
        return _admin_client

    try:
        from navig.core.config import get_global_config

        cfg = get_global_config()
        matrix_cfg = cfg.get("comms", {}).get("matrix", {})
        hs_cfg = matrix_cfg.get("homeserver", {})
        _admin_client = MatrixAdminClient(
            homeserver_url=matrix_cfg.get("homeserver_url", "http://localhost:6167"),
            admin_token=hs_cfg.get("admin_token", ""),
            container_name=hs_cfg.get("container_name", "navig-conduit"),
        )
    except Exception:
        _admin_client = MatrixAdminClient()

    return _admin_client
