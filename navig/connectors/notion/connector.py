"""Notion connector — search pages and databases."""
from __future__ import annotations

import logging

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.types import (
    Action, ActionResult, ActionType, ConnectorDomain, HealthStatus, Resource, ResourceType,
)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

logger = logging.getLogger("navig.connectors.notion")
_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 15.0


class NotionConnector(BaseConnector):
    """Notion connector — search pages, databases, and create pages."""

    manifest = ConnectorManifest(
        id="notion",
        display_name="Notion",
        description="Search pages and databases. Create and update Notion pages.",
        domain=ConnectorDomain.KNOWLEDGE,
        icon="◻",
        oauth_scopes=[],
        oauth_provider="notion",
        requires_oauth=True,
        can_search=True,
        can_fetch=True,
        can_act=True,
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def search(self, query: str, limit: int = 10) -> list[Resource]:
        if not HTTPX_AVAILABLE:
            return []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{_API_BASE}/search",
                headers=self._headers(),
                json={"query": query, "page_size": min(limit, 20)},
            )
            r.raise_for_status()
            results = r.json().get("results", [])
        resources = []
        for item in results:
            obj_type = item.get("object", "page")
            title = ""
            if obj_type == "page":
                props = item.get("properties", {})
                title_prop = props.get("title") or props.get("Name") or {}
                title_parts = title_prop.get("title", []) if isinstance(title_prop, dict) else []
                title = "".join(p.get("plain_text", "") for p in title_parts) if title_parts else "Untitled"
            elif obj_type == "database":
                title_parts = item.get("title", [])
                title = "".join(p.get("plain_text", "") for p in title_parts) if title_parts else "Untitled DB"
            resources.append(Resource(
                id=item["id"],
                source="notion",
                title=title,
                preview=item.get("url", ""),
                url=item.get("url", ""),
                timestamp=item.get("last_edited_time"),
                resource_type=ResourceType.DOCUMENT,
                metadata={"object": obj_type},
            ))
        return resources

    async def fetch(self, resource_id: str) -> Resource | None:
        if not HTTPX_AVAILABLE:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{_API_BASE}/pages/{resource_id}", headers=self._headers())
            if r.status_code == 404:
                return None
            r.raise_for_status()
            item = r.json()
        props = item.get("properties", {})
        title_prop = props.get("title") or props.get("Name") or {}
        title_parts = title_prop.get("title", []) if isinstance(title_prop, dict) else []
        title = "".join(p.get("plain_text", "") for p in title_parts) if title_parts else "Untitled"
        return Resource(
            id=item["id"], source="notion", title=title,
            preview="", url=item.get("url", ""),
            timestamp=item.get("last_edited_time"),
            resource_type=ResourceType.DOCUMENT, metadata={},
        )

    async def act(self, action: Action) -> ActionResult:
        if action.action_type == ActionType.CREATE:
            if not HTTPX_AVAILABLE:
                return ActionResult(success=False, error="httpx not available")
            parent_id = action.params.get("parent_id", "")
            title = action.params.get("title", "New Page")
            if not parent_id:
                return ActionResult(success=False, error="parent_id required")
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/pages",
                    headers=self._headers(),
                    json={
                        "parent": {"page_id": parent_id},
                        "properties": {"title": {"title": [{"text": {"content": title}}]}},
                    },
                )
                r.raise_for_status()
                item = r.json()
            return ActionResult(success=True, resource=Resource(
                id=item["id"], source="notion", title=title,
                preview="", url=item.get("url", ""), timestamp=item.get("created_time"),
                resource_type=ResourceType.DOCUMENT, metadata={},
            ))
        return ActionResult(success=False, error=f"Unsupported action: {action.action_type}")

    async def health_check(self) -> HealthStatus:
        if not HTTPX_AVAILABLE:
            return HealthStatus(ok=False, message="httpx not installed")
        try:
            import time
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{_API_BASE}/users/me", headers=self._headers())
            latency = int((time.monotonic() - t0) * 1000)
            ok = r.status_code == 200
            name = r.json().get("name", "OK") if ok else r.text[:100]
            return HealthStatus(ok=ok, latency_ms=latency, message=name)
        except Exception as exc:
            return HealthStatus(ok=False, message=str(exc))

    async def disconnect(self) -> None:
        self._access_token = None
