from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from navig.vault.resolver import resolve_secret

logger = logging.getLogger(__name__)

FIRECRAWL_API_BASE = "https://api.firecrawl.dev/v1"


class FirecrawlError(RuntimeError):
    """Typed Firecrawl API failure with status metadata."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class FirecrawlClient:
    """Thin Firecrawl REST client supporting free-tier (no key) usage."""

    api_key: str | None = None
    base_url: str = FIRECRAWL_API_BASE
    timeout_seconds: int = 30

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - environment dependency guard
            raise FirecrawlError("requests library not available") from exc

        url = f"{self.base_url}{path}"
        response = requests.request(
            method,
            url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )

        content_type = response.headers.get("content-type", "")
        body: dict[str, Any] | str
        if content_type.startswith("application/json"):
            body = response.json()
        else:
            body = response.text

        if response.status_code >= 400:
            message = "Firecrawl request failed"
            if isinstance(body, dict):
                message = str(
                    body.get("error")
                    or body.get("message")
                    or body.get("detail")
                    or message
                )
            elif isinstance(body, str) and body.strip():
                message = body[:300]

            if response.status_code == 429:
                message = "Firecrawl quota reached. Add an API key for higher limits."
                raise FirecrawlError(message, status_code=429, retryable=False)

            if response.status_code >= 500:
                raise FirecrawlError(
                    "Firecrawl temporarily unavailable. Please retry.",
                    status_code=response.status_code,
                    retryable=True,
                )

            raise FirecrawlError(message, status_code=response.status_code, retryable=False)

        if isinstance(body, dict):
            return body
        return {"data": body}

    def scrape(self, *, url: str, mode: str = "scrape", max_pages: int | None = None) -> dict[str, Any]:
        """Run scrape or crawl through Firecrawl REST API."""
        if mode not in {"scrape", "crawl"}:
            raise FirecrawlError("mode must be 'scrape' or 'crawl'", status_code=400)

        if mode == "scrape":
            payload: dict[str, Any] = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            return self._request("POST", "/scrape", payload=payload)

        crawl_payload: dict[str, Any] = {
            "url": url,
            "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
        }
        if max_pages is not None:
            crawl_payload["limit"] = max_pages

        return self._request("POST", "/crawl", payload=crawl_payload)

    def crawl(self, *, url: str, max_pages: int | None = None) -> dict[str, Any]:
        """Run Firecrawl crawl API with markdown extraction."""
        return self.scrape(url=url, mode="crawl", max_pages=max_pages)

    def search(
        self,
        *,
        query: str,
        limit: int = 5,
        scrape_inline: bool = False,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run Firecrawl search API."""
        payload: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(int(limit), 20)),
            "sources": sources or ["web"],
        }
        if scrape_inline:
            payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
        return self._request("POST", "/search", payload=payload)

    def validate_api_key(self, api_key: str) -> tuple[bool, str]:
        """Validate key against Firecrawl account endpoint."""
        probe = FirecrawlClient(api_key=api_key, base_url=self.base_url, timeout_seconds=self.timeout_seconds)
        try:
            probe._request("GET", "/account")
            return True, "Firecrawl API key is valid"
        except FirecrawlError as exc:
            if exc.status_code in (401, 403):
                return False, "Invalid Firecrawl API key"
            if exc.status_code == 429:
                return True, "API key is valid but currently rate limited"
            return False, str(exc)


def get_firecrawl_client() -> FirecrawlClient:
    """Resolve Firecrawl API key from vault/env and return a ready client.

    No key is a supported path (Firecrawl free tier).
    """
    api_key = resolve_secret(
        env_vars=("FIRECRAWL_API_KEY",),
        vault_labels=(
            "FIRECRAWL_API_KEY",
            "firecrawl/api_key",
            "firecrawl/api-key",
            "firecrawl_api_key",
            "web/firecrawl_api_key",
        ),
    )
    if api_key:
        return FirecrawlClient(api_key=api_key)
    return FirecrawlClient(api_key=None)
