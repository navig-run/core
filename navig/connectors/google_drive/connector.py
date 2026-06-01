"""Google Drive connector — search and fetch files."""
from __future__ import annotations

import logging

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.types import (
    Action, ActionResult, ConnectorDomain, HealthStatus, Resource, ResourceType,
)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

logger = logging.getLogger("navig.connectors.google_drive")
_API_BASE = "https://www.googleapis.com/drive/v3"
_TIMEOUT = 15.0


class GoogleDriveConnector(BaseConnector):
    """Google Drive connector — search files and fetch metadata."""

    manifest = ConnectorManifest(
        id="google_drive",
        display_name="Google Drive",
        description="Search files and documents in your Google Drive. Fetch file metadata and content.",
        domain=ConnectorDomain.FILE_STORAGE,
        icon="◈",
        oauth_scopes=["https://www.googleapis.com/auth/drive.readonly", "openid", "email", "profile"],
        oauth_provider="google_drive",
        requires_oauth=True,
        can_search=True,
        can_fetch=True,
        can_act=False,
    )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token()}"}

    async def search(self, query: str, limit: int = 10) -> list[Resource]:
        if not HTTPX_AVAILABLE:
            return []
        # Build Drive query — search in name and full text
        drive_query = f"fullText contains '{query}' and trashed=false"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_API_BASE}/files",
                headers=self._headers(),
                params={
                    "q": drive_query,
                    "pageSize": min(limit, 20),
                    "fields": "files(id,name,mimeType,webViewLink,modifiedTime,description)",
                    "orderBy": "modifiedTime desc",
                },
            )
            r.raise_for_status()
            files = r.json().get("files", [])
        return [
            Resource(
                id=f["id"],
                source="google_drive",
                title=f.get("name", "Untitled"),
                preview=f.get("description", ""),
                url=f.get("webViewLink", ""),
                timestamp=f.get("modifiedTime"),
                resource_type=ResourceType.FILE,
                metadata={"mimeType": f.get("mimeType", "")},
            )
            for f in files
        ]

    async def fetch(self, resource_id: str) -> Resource | None:
        if not HTTPX_AVAILABLE:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_API_BASE}/files/{resource_id}",
                headers=self._headers(),
                params={"fields": "id,name,mimeType,webViewLink,modifiedTime,description"},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            f = r.json()
        return Resource(
            id=f["id"], source="google_drive", title=f.get("name", "Untitled"),
            preview=f.get("description", ""), url=f.get("webViewLink", ""),
            timestamp=f.get("modifiedTime"), resource_type=ResourceType.FILE,
            metadata={"mimeType": f.get("mimeType", "")},
        )

    async def act(self, action) -> ActionResult:
        return ActionResult(success=False, error="Google Drive connector is read-only")

    async def health_check(self) -> HealthStatus:
        if not HTTPX_AVAILABLE:
            return HealthStatus(ok=False, message="httpx not installed")
        try:
            import time
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{_API_BASE}/about",
                    headers=self._headers(),
                    params={"fields": "user"},
                )
            latency = int((time.monotonic() - t0) * 1000)
            ok = r.status_code == 200
            email = r.json().get("user", {}).get("emailAddress", "OK") if ok else r.text[:100]
            return HealthStatus(ok=ok, latency_ms=latency, message=email)
        except Exception as exc:
            return HealthStatus(ok=False, message=str(exc))

    async def disconnect(self) -> None:
        self._access_token = None
