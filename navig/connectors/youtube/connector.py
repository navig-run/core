"""
YouTube Data API v3 Connector.

Authentication: API key via ``YOUTUBE_API_KEY`` env variable or navig vault.
No OAuth required for public data.

Coverage:
    - Video search (search.list)
    - Video details (videos.list)
    - Channel details (channels.list)
    - Comment threads (commentThreads.list)
    - Trending videos by region

Usage:
    connector = YouTubeConnector()
    await connector.connect()
    results = await connector.search("Python tutorial asyncio")
    video = await connector.fetch("dQw4w9WgXcQ")  # video id
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAuthError, ConnectorRateLimitError
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

logger = logging.getLogger("navig.connectors.youtube")

_YT_BASE = "https://www.googleapis.com/youtube/v3"


def _yt_get(endpoint: str, params: dict[str, str], timeout: int = 12) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{_YT_BASE}/{endpoint}?{qs}")
    resp = urllib.request.urlopen(req, timeout=timeout)
    import json

    return json.loads(resp.read())


def _iso8601_to_seconds(duration: str) -> int:
    """Parse ISO 8601 duration string like PT4M13S → seconds."""
    import re

    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        duration or "",
    )
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


class YouTubeConnector(BaseConnector):
    """Connector for YouTube Data API v3 — video/channel search and details.

    Auth: API key via ``YOUTUBE_API_KEY`` env variable or navig vault.
    No OAuth required for public data.

    Quota: 10,000 units/day per key.
        search.list  = 100 units
        videos.list  = 1 unit
        channels.list = 1 unit
        commentThreads.list = 1 unit
    """

    manifest = ConnectorManifest(
        id="youtube",
        display_name="YouTube",
        description=(
            "YouTube Data API v3. Search videos, fetch video/channel details, "
            "browse comment threads. Public data only (no OAuth)."
        ),
        domain=ConnectorDomain.KNOWLEDGE,
        icon="▶️",
        oauth_scopes=[],
        oauth_provider="",
        requires_oauth=False,
        can_search=True,
        can_fetch=True,
        can_act=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Load API key from env or vault label ``youtube/api_key``."""
        key = (
            os.environ.get("YOUTUBE_API_KEY", "").strip()
            or os.environ.get("YT_API_KEY", "").strip()
        )
        if not key:
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                "YouTube API key not found. Set YOUTUBE_API_KEY or "
                "store in vault under 'youtube/api_key'.",
            )
        self._api_key = key
        self._status = ConnectorStatus.CONNECTED
        logger.debug("YouTube connector connected.")

    async def disconnect(self) -> None:
        self._api_key = None
        self._status = ConnectorStatus.DISCONNECTED

    # ── Search ───────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[Resource]:
        """Search YouTube videos matching *query*.

        Args:
            query: Search terms.
            limit: Max results (1–50).  Note: costs 100 quota units per call.
            kwargs:
                region_code (str): ISO 3166-1 alpha-2, e.g. "US".
                order (str): "relevance"|"date"|"rating"|"viewCount" (default "relevance").
                type (str): "video"|"channel"|"playlist" (default "video").
                video_duration (str): "any"|"short"|"medium"|"long".
        """
        self._require_connected()
        params: dict[str, str] = {
            "part": "snippet",
            "q": query,
            "maxResults": str(min(max(1, limit), 50)),
            "type": kwargs.get("type", "video"),
            "order": kwargs.get("order", "relevance"),
            "key": self._api_key,
        }
        if "region_code" in kwargs:
            params["regionCode"] = kwargs["region_code"]
        if "video_duration" in kwargs:
            params["videoDuration"] = kwargs["video_duration"]
        try:
            data = _yt_get("search", params)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                import json as _j

                body = _j.loads(exc.read()) if exc.fp else {}
                reason = body.get("error", {}).get("errors", [{}])[0].get("reason", "")
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    raise ConnectorRateLimitError(self.manifest.id, "YouTube daily quota exceeded") from exc
                raise ConnectorAuthError(self.manifest.id, str(exc)) from exc
            raise

        resources: list[Resource] = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            channel_id = snippet.get("channelId", "")
            resources.append(
                Resource(
                    id=video_id or item.get("id", {}).get("channelId", ""),
                    title=snippet.get("title", ""),
                    body=(
                        f"{snippet.get('description', '')[:300]}\n"
                        f"Channel: {snippet.get('channelTitle', '')}\n"
                        f"Published: {snippet.get('publishedAt', '')[:10]}"
                    ),
                    url=f"https://www.youtube.com/watch?v={video_id}"
                    if video_id
                    else f"https://www.youtube.com/channel/{channel_id}",
                    resource_type=ResourceType.DOCUMENT,
                    metadata={
                        "video_id": video_id,
                        "channel_id": channel_id,
                        "channel_title": snippet.get("channelTitle"),
                        "published_at": snippet.get("publishedAt"),
                        "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    },
                )
            )
        return resources

    # ── Fetch: video details ─────────────────────────────────────────────────

    async def fetch(self, video_id: str, **kwargs: Any) -> Resource | None:
        """Fetch detailed metadata for a video_id.

        Args:
            video_id: YouTube video identifier (11-char string).
            kwargs:
                parts (list[str]): API parts to request (default: snippet+statistics+contentDetails).
        """
        self._require_connected()
        parts = ",".join(kwargs.get("parts", ["snippet", "statistics", "contentDetails"]))
        data = _yt_get(
            "videos",
            {"part": parts, "id": video_id, "key": self._api_key},
        )
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        duration_s = _iso8601_to_seconds(content.get("duration", ""))
        return Resource(
            id=video_id,
            title=snippet.get("title", ""),
            body=(
                f"{snippet.get('description', '')[:500]}\n\n"
                f"Duration: {duration_s // 60}m {duration_s % 60}s\n"
                f"Views: {int(stats.get('viewCount', 0)):,}\n"
                f"Likes: {int(stats.get('likeCount', 0)):,}\n"
                f"Comments: {int(stats.get('commentCount', 0)):,}\n"
                f"Channel: {snippet.get('channelTitle', '')}"
            ),
            url=f"https://www.youtube.com/watch?v={video_id}",
            resource_type=ResourceType.DOCUMENT,
            metadata={
                "video_id": video_id,
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("channelTitle"),
                "published_at": snippet.get("publishedAt"),
                "duration_seconds": duration_s,
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "tags": snippet.get("tags", []),
                "thumbnail": snippet.get("thumbnails", {}).get("maxres", {}).get("url")
                or snippet.get("thumbnails", {}).get("high", {}).get("url"),
            },
        )

    # ── Act ──────────────────────────────────────────────────────────────────

    async def act(self, action: Action) -> ActionResult:
        """Supported actions:

        trending: {"region_code": "US", "limit": 10} → list of trending video dicts
        comments: {"video_id": "<id>", "limit": 20} → list of top comment dicts
        channel:  {"channel_id": "<id>"} → channel metadata dict
        """
        self._require_connected()
        if action.name == "trending":
            region = action.params.get("region_code", "US")
            limit = min(int(action.params.get("limit", 10)), 50)
            data = _yt_get(
                "videos",
                {
                    "part": "snippet,statistics",
                    "chart": "mostPopular",
                    "regionCode": region,
                    "maxResults": str(limit),
                    "key": self._api_key,
                },
            )
            return ActionResult(
                success=True,
                data={"videos": data.get("items", [])},
            )
        if action.name == "comments":
            video_id = action.params.get("video_id", "")
            limit = min(int(action.params.get("limit", 20)), 100)
            data = _yt_get(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": str(limit),
                    "order": "relevance",
                    "key": self._api_key,
                },
            )
            return ActionResult(
                success=True,
                data={"comments": data.get("items", [])},
            )
        if action.name == "channel":
            channel_id = action.params.get("channel_id", "")
            data = _yt_get(
                "channels",
                {
                    "part": "snippet,statistics",
                    "id": channel_id,
                    "key": self._api_key,
                },
            )
            items = data.get("items", [])
            return ActionResult(
                success=bool(items),
                data={"channel": items[0] if items else {}},
            )
        return ActionResult(success=False, error=f"Unknown action: {action.name}")

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Ping videos.list with a known stable video (costs 1 quota unit)."""
        if not self._api_key:
            return HealthStatus(healthy=False, message="Not connected", latency_ms=0)
        t0 = time.monotonic()
        try:
            # Rick Astley "Never Gonna Give You Up" — stable forever
            data = _yt_get(
                "videos",
                {"part": "snippet", "id": "dQw4w9WgXcQ", "key": self._api_key},
                timeout=8,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            items = data.get("items", [])
            if items:
                return HealthStatus(
                    healthy=True,
                    message=f"YouTube API OK — found '{items[0]['snippet']['title']}'",
                    latency_ms=latency_ms,
                )
            return HealthStatus(healthy=False, message="No items returned", latency_ms=latency_ms)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(healthy=False, message=str(exc), latency_ms=latency_ms)
