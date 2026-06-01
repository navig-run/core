"""Linear connector — search issues and projects, create issues."""
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

logger = logging.getLogger("navig.connectors.linear")
_API_BASE = "https://api.linear.app/graphql"
_TIMEOUT = 15.0


class LinearConnector(BaseConnector):
    """Linear connector — search issues, create issues, manage projects."""

    manifest = ConnectorManifest(
        id="linear",
        display_name="Linear",
        description="Search issues and projects. Create and update Linear issues.",
        domain=ConnectorDomain.PROJECT_MANAGEMENT,
        icon="◆",
        oauth_scopes=["read", "write", "issues:create"],
        oauth_provider="linear",
        requires_oauth=True,
        can_search=True,
        can_fetch=True,
        can_act=True,
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        if not HTTPX_AVAILABLE:
            return {}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                _API_BASE,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
            )
            r.raise_for_status()
            return r.json()

    async def search(self, query: str, limit: int = 10) -> list[Resource]:
        gql = """
        query SearchIssues($query: String!, $limit: Int!) {
          issueSearch(query: $query, first: $limit) {
            nodes { id title description url state { name } team { name } updatedAt }
          }
        }"""
        data = await self._gql(gql, {"query": query, "limit": min(limit, 20)})
        if data.get("errors"):
            logger.warning("Linear search GraphQL errors: %s", data["errors"])
            return []
        issues = ((data.get("data") or {}).get("issueSearch") or {}).get("nodes", [])
        return [
            Resource(
                id=i["id"],
                source="linear",
                title=i.get("title", ""),
                preview=i.get("description", "")[:300] if i.get("description") else "",
                url=i.get("url", ""),
                timestamp=i.get("updatedAt"),
                resource_type=ResourceType.ISSUE,
                metadata={"state": i.get("state", {}).get("name"), "team": i.get("team", {}).get("name")},
            )
            for i in issues
        ]

    async def fetch(self, resource_id: str) -> Resource | None:
        gql = """
        query GetIssue($id: String!) {
          issue(id: $id) { id title description url state { name } team { name } updatedAt }
        }"""
        data = await self._gql(gql, {"id": resource_id})
        i = (data.get("data") or {}).get("issue")
        if not i:
            return None
        return Resource(
            id=i["id"], source="linear", title=i.get("title", ""),
            preview=i.get("description", "")[:500] if i.get("description") else "",
            url=i.get("url", ""), timestamp=i.get("updatedAt"),
            resource_type=ResourceType.ISSUE,
            metadata={"state": i.get("state", {}).get("name")},
        )

    async def act(self, action: Action) -> ActionResult:
        if action.action_type == ActionType.CREATE:
            title = action.params.get("title", "")
            team_id = action.params.get("team_id", "")
            if not title or not team_id:
                return ActionResult(success=False, error="title and team_id required")
            gql = """
            mutation CreateIssue($title: String!, $teamId: String!, $description: String) {
              issueCreate(input: {title: $title, teamId: $teamId, description: $description}) {
                success issue { id title url }
              }
            }"""
            data = await self._gql(gql, {"title": title, "teamId": team_id, "description": action.params.get("description")})
            result = data.get("data", {}).get("issueCreate", {})
            if result.get("success"):
                i = result.get("issue", {})
                return ActionResult(success=True, resource=Resource(
                    id=i.get("id", ""), source="linear", title=i.get("title", ""),
                    preview="", url=i.get("url", ""), timestamp=None,
                    resource_type=ResourceType.ISSUE, metadata={},
                ))
            return ActionResult(success=False, error="Issue creation failed")
        return ActionResult(success=False, error=f"Unsupported action: {action.action_type}")

    async def health_check(self) -> HealthStatus:
        try:
            import time
            t0 = time.monotonic()
            data = await self._gql("query { viewer { id name email } }")
            latency = int((time.monotonic() - t0) * 1000)
            viewer = data.get("data", {}).get("viewer", {})
            ok = bool(viewer.get("id"))
            return HealthStatus(ok=ok, latency_ms=latency, message=viewer.get("email", "OK") if ok else "Auth failed")
        except Exception as exc:
            return HealthStatus(ok=False, message=str(exc))

    async def disconnect(self) -> None:
        self._access_token = None
