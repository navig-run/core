"""
Perplexity AI Connector

Provides AI-powered web search through the Perplexity Sonar API.
Authentication: API key (set PERPLEXITY_API_KEY or store in navig vault).

Usage:
    connector = PerplexityConnector()
    await connector.connect()
    results = await connector.search("latest news about LLMs")
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

logger = logging.getLogger("navig.connectors.perplexity")

_API_BASE = "https://api.perplexity.ai"
_SEARCH_MODEL = "sonar"


class PerplexityConnector(BaseConnector):
    """Connector for Perplexity AI Sonar search API.

    Auth: API key via ``PERPLEXITY_API_KEY`` env variable or navig vault.
    No OAuth required.
    """

    manifest = ConnectorManifest(
        id="perplexity",
        display_name="Perplexity AI",
        description="AI-powered web search via Perplexity Sonar. Returns cited, up-to-date answers.",
        domain=ConnectorDomain.AI_RESEARCH,
        icon="🔍",
        oauth_scopes=[],
        oauth_provider="",
        requires_oauth=False,
        can_search=True,
        can_fetch=False,
        can_act=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str | None = None

    # -- Connection lifecycle ----------------------------------------------

    async def connect(self) -> None:
        """Load API key from env or mark as error.

        Reads ``PERPLEXITY_API_KEY`` from the environment.
        Store the key in your shell profile or pass it via the env when starting NAVIG.
        """
        api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()

        if not api_key:
            self._status = ConnectorStatus.ERROR
            raise ValueError(
                "Perplexity API key not found. "
                "Set PERPLEXITY_API_KEY environment variable."
            )

        self._api_key = api_key
        self._status = ConnectorStatus.CONNECTED
        logger.debug("Perplexity connector connected.")

    async def disconnect(self) -> None:
        self._api_key = None
        self._status = ConnectorStatus.DISCONNECTED

    # -- Core operations ---------------------------------------------------

    async def search(
        self,
        query: str,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[Resource]:
        """Search the web using Perplexity Sonar.

        Args:
            query: Natural-language search query
            limit: Max number of results to return (Perplexity returns one
                   synthesised answer; ``limit`` controls citation count)
            **kwargs: Optional overrides: model, system_prompt, max_tokens

        Returns:
            List of :class:`~navig.connectors.types.Resource` objects.
            The first resource is the synthesised answer; subsequent entries
            are individual cited sources (if the API returns them).
        """
        self._ensure_connected()

        model = kwargs.get("model", _SEARCH_MODEL)
        system_prompt = kwargs.get(
            "system_prompt",
            "Be precise and cite sources. Return a concise answer.",
        )
        max_tokens = int(kwargs.get("max_tokens", 1024))

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            "max_tokens": max_tokens,
        }

        t0 = time.perf_counter()
        data = await self._post("/chat/completions", payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        resources: list[Resource] = []

        choices = data.get("choices") or []
        if choices:
            answer_text = choices[0].get("message", {}).get("content", "")
            resources.append(
                Resource(
                    id=f"perplexity:answer:{abs(hash(query))}",
                    source="perplexity",
                    title=f"Perplexity: {query[:60]}",
                    preview=answer_text[:500],
                    resource_type=ResourceType.DOCUMENT,
                    metadata={
                        "full_text": answer_text,
                        "model": model,
                        "latency_ms": elapsed_ms,
                        "usage": data.get("usage", {}),
                    },
                )
            )

        # Cited sources returned in `search_results` or `citations` fields
        citations = data.get("citations") or data.get("search_results") or []
        for i, cite in enumerate(citations[:limit]):
            if isinstance(cite, str):
                url = cite
                title = cite
                snippet = ""
            elif isinstance(cite, dict):
                url = cite.get("url", "")
                title = cite.get("title", url)
                snippet = cite.get("snippet", "")
            else:
                continue

            resources.append(
                Resource(
                    id=f"perplexity:cite:{abs(hash(url))}",
                    source="perplexity",
                    title=title,
                    preview=snippet,
                    url=url,
                    resource_type=ResourceType.DOCUMENT,
                    metadata={"rank": i + 1},
                )
            )

        return resources

    async def fetch(self, resource_id: str, **kwargs: Any) -> Resource | None:
        """Perplexity has no persistent resource store — always returns None."""
        return None

    async def act(self, action: Action) -> ActionResult:
        """Perplexity is read-only — no write actions supported."""
        return ActionResult(
            success=False,
            error="Perplexity connector does not support write actions.",
        )

    async def health_check(self) -> HealthStatus:
        """Verify API key is valid with a minimal request."""
        if self._status != ConnectorStatus.CONNECTED:
            return HealthStatus(ok=False, latency_ms=0.0, message="Not connected")

        t0 = time.perf_counter()
        try:
            await self._post(
                "/chat/completions",
                {
                    "model": _SEARCH_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(ok=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(ok=False, latency_ms=latency_ms, message=str(exc))

    # -- Internal helpers --------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._status != ConnectorStatus.CONNECTED or not self._api_key:
            raise RuntimeError(
                "PerplexityConnector is not connected. Call await connector.connect() first."
            )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to the Perplexity API and return parsed JSON."""
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for PerplexityConnector. pip install httpx") from exc

        url = f"{_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code == 401:
            self._status = ConnectorStatus.ERROR
            raise ValueError("Perplexity API key is invalid or expired.")
        if resp.status_code == 429:
            raise RuntimeError("Perplexity rate limit exceeded. Retry later.")
        if not resp.is_success:
            raise RuntimeError(
                f"Perplexity API error {resp.status_code}: {resp.text[:200]}"
            )

        return resp.json()
