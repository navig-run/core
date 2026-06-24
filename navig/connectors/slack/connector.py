"""Slack connector — search messages, post to channels."""
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

logger = logging.getLogger("navig.connectors.slack")
_API_BASE = "https://slack.com/api"
_TIMEOUT = 15.0


class SlackConnector(BaseConnector):
    """Slack connector — search messages and post to channels."""

    manifest = ConnectorManifest(
        id="slack",
        display_name="Slack",
        description="Search messages across channels and post new messages to your Slack workspace.",
        domain=ConnectorDomain.COMMUNICATION,
        icon="◆",
        oauth_scopes=["channels:read", "channels:history", "chat:write", "search:read"],
        oauth_provider="slack",
        requires_oauth=True,
        can_search=True,
        can_fetch=True,
        can_act=True,
    )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token()}"}

    async def search(self, query: str, limit: int = 10) -> list[Resource]:
        if not HTTPX_AVAILABLE:
            return []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_API_BASE}/search.messages",
                headers=self._headers(),
                params={"query": query, "count": min(limit, 20)},
            )
            r.raise_for_status()
            data = r.json()
        if not data.get("ok"):
            # search.messages requires a user token with search:read — bot
            # tokens return "not_allowed_token_type". Surface clearly, don't crash.
            err = data.get("error", "unknown")
            if err == "not_allowed_token_type":
                logger.info("Slack search needs a user token (search:read); bot token can't search.")
            else:
                logger.warning("Slack search failed: %s", err)
            return []
        messages = data.get("messages", {}).get("matches", [])
        return [
            Resource(
                id=f"{m.get('channel', {}).get('id', '')}:{m.get('ts', '')}",
                source="slack",
                title=f"#{m.get('channel', {}).get('name', '?')} — {m.get('username', '?')}",
                preview=m.get("text", "")[:300],
                url=m.get("permalink", ""),
                timestamp=m.get("ts"),
                resource_type=ResourceType.MESSAGE,
                metadata={"channel": m.get("channel", {}).get("name"), "user": m.get("username")},
            )
            for m in messages
        ]

    async def fetch(self, resource_id: str) -> Resource | None:
        # resource_id: "channel_id:ts"
        if not HTTPX_AVAILABLE or ":" not in resource_id:
            return None
        channel, ts = resource_id.split(":", 1)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_API_BASE}/conversations.replies",
                headers=self._headers(),
                params={"channel": channel, "ts": ts, "limit": 1},
            )
            r.raise_for_status()
            msgs = r.json().get("messages", [])
        if not msgs:
            return None
        m = msgs[0]
        return Resource(
            id=resource_id, source="slack", title=f"Message in {channel}",
            preview=m.get("text", "")[:500], url="", timestamp=m.get("ts"),
            resource_type=ResourceType.MESSAGE, metadata={"channel": channel},
        )

    async def act(self, action: Action) -> ActionResult:
        if action.action_type == ActionType.SEND:
            if not HTTPX_AVAILABLE:
                return ActionResult(success=False, error="httpx not available")
            channel = action.params.get("channel", "")
            text = action.params.get("text", "")
            if not channel or not text:
                return ActionResult(success=False, error="channel and text required")
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/chat.postMessage",
                    headers=self._headers(),
                    json={"channel": channel, "text": text},
                )
                r.raise_for_status()
                data = r.json()
            ok = data.get("ok", False)
            return ActionResult(success=ok, error=data.get("error") if not ok else None)
        return ActionResult(success=False, error=f"Unsupported action: {action.action_type}")

    async def health_check(self) -> HealthStatus:
        if not HTTPX_AVAILABLE:
            return HealthStatus(ok=False, message="httpx not installed")
        try:
            import time
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{_API_BASE}/auth.test", headers=self._headers())
            latency = int((time.monotonic() - t0) * 1000)
            data = r.json()
            ok = data.get("ok", False)
            return HealthStatus(ok=ok, latency_ms=latency, message=data.get("team", "OK") if ok else data.get("error", ""))
        except Exception as exc:
            return HealthStatus(ok=False, message=str(exc))

    async def disconnect(self) -> None:
        self._access_token = None
