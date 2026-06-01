"""GitHub connector — search repos, issues, pull requests."""
from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger("navig.connectors.github")
_API_BASE = "https://api.github.com"
_TIMEOUT = 15.0


class GitHubConnector(BaseConnector):
    """GitHub connector — search repos, list issues, create issues."""

    manifest = ConnectorManifest(
        id="github",
        display_name="GitHub",
        description="Search repositories, issues, and pull requests. Create and update issues.",
        domain=ConnectorDomain.DEV,
        icon="◈",
        oauth_scopes=["repo", "read:org", "read:user", "user:email"],
        oauth_provider="github",
        requires_oauth=True,
        can_search=True,
        can_fetch=True,
        can_act=True,
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def search(self, query: str, limit: int = 10) -> list[Resource]:
        if not HTTPX_AVAILABLE:
            return []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_API_BASE}/search/issues",
                headers=self._headers(),
                params={"q": query, "per_page": min(limit, 30), "sort": "updated"},
            )
            r.raise_for_status()
            items = r.json().get("items", [])

        resources = []
        for item in items:
            # GitHub search returns repository_url like
            # "https://api.github.com/repos/{owner}/{repo}". Derive the
            # "owner/repo" so fetch() can resolve by "owner/repo#number".
            repo_url = item.get("repository_url", "")
            repo_full = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
            number = item.get("number")
            # id is the fetch contract: "owner/repo#number" when derivable,
            # else fall back to the numeric id.
            rid = f"{repo_full}#{number}" if repo_full and number is not None else str(item["id"])
            resources.append(Resource(
                id=rid,
                source="github",
                title=item.get("title", ""),
                preview=item.get("body", "")[:200] if item.get("body") else "",
                url=item.get("html_url", ""),
                timestamp=item.get("updated_at"),
                resource_type=ResourceType.PULL_REQUEST if "pull_request" in item else ResourceType.ISSUE,
                metadata={"state": item.get("state"), "number": number, "repo": repo_full, "github_id": item.get("id")},
            ))
        return resources

    async def fetch(self, resource_id: str) -> Resource | None:
        # resource_id format: "owner/repo#number" e.g. "facebook/react#12345"
        if not HTTPX_AVAILABLE or "#" not in resource_id:
            return None
        repo, number = resource_id.rsplit("#", 1)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{_API_BASE}/repos/{repo}/issues/{number}", headers=self._headers())
            if r.status_code == 404:
                return None
            r.raise_for_status()
            item = r.json()
        return Resource(
            id=str(item["id"]),
            source="github",
            title=item.get("title", ""),
            preview=item.get("body", "")[:500] if item.get("body") else "",
            url=item.get("html_url", ""),
            timestamp=item.get("updated_at"),
            resource_type=ResourceType.ISSUE,
            metadata={"state": item.get("state"), "labels": [l["name"] for l in item.get("labels", [])]},
        )

    async def act(self, action: Action) -> ActionResult:
        if action.action_type == ActionType.CREATE and action.params.get("repo"):
            if not HTTPX_AVAILABLE:
                return ActionResult(success=False, error="httpx not available")
            repo = action.params["repo"]
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/repos/{repo}/issues",
                    headers=self._headers(),
                    json={"title": action.params.get("title", ""), "body": action.params.get("body", "")},
                )
                r.raise_for_status()
                item = r.json()
            return ActionResult(success=True, resource=Resource(
                id=str(item["id"]), source="github", title=item["title"],
                preview="", url=item.get("html_url", ""), timestamp=item.get("created_at"),
                resource_type=ResourceType.ISSUE, metadata={},
            ))
        return ActionResult(success=False, error=f"Unsupported action: {action.action_type}")

    async def health_check(self) -> HealthStatus:
        if not HTTPX_AVAILABLE:
            return HealthStatus(ok=False, message="httpx not installed")
        try:
            import time
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{_API_BASE}/user", headers=self._headers())
            latency = int((time.monotonic() - t0) * 1000)
            ok = r.status_code == 200
            return HealthStatus(ok=ok, latency_ms=latency, message="OK" if ok else r.text[:100])
        except Exception as exc:
            return HealthStatus(ok=False, message=str(exc))

    async def disconnect(self) -> None:
        self._access_token = None
