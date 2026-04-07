"""NAVIG Web Tools - URL Fetching and Web Search

Provides web content fetching and search capabilities:
- web_fetch: HTTP GET + HTML→markdown/text extraction
- web_search: Firecrawl-first routing with Brave/Tavily/DuckDuckGo fallback

Modeled after advanced web tools implementation.
"""

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# HTTP library (requests for sync, aiohttp for async)
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Optional: trafilatura for better content extraction
try:
    import trafilatura

    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False


# =============================================================================
# Constants
# =============================================================================

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CHARS = 50_000
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_CACHE_TTL_MINUTES = 15
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DUCKDUCKGO_ENDPOINT = "https://api.duckduckgo.com/"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"

_WEB_PROVIDER_ALIASES: dict[str, str] = {
    "": "auto",
    "auto": "auto",
    "firecrawl": "firecrawl",
    "fire-crawl": "firecrawl",
    "brave": "brave",
    "brave-search": "brave",
    "duckduckgo": "duckduckgo",
    "ddg": "duckduckgo",
    "perplexity": "perplexity",
    "gemini": "gemini",
    "google": "gemini",
    "grok": "grok",
    "xai": "grok",
    "kimi": "kimi",
    "moonshot": "kimi",
    "tavily": "tavily",
}

_WEB_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "firecrawl": ("FIRECRAWL_API_KEY",),
    "brave": ("BRAVE_API_KEY",),
    "perplexity": ("PERPLEXITY_API_KEY", "PPLX_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "grok": ("XAI_API_KEY", "GROK_KEY"),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "tavily": ("TAVILY_API_KEY",),
}

_WEB_PROVIDER_VAULT_LABELS: dict[str, tuple[str, ...]] = {
    "firecrawl": (
        "FIRECRAWL_API_KEY",
        "firecrawl/api_key",
        "firecrawl/api-key",
        "firecrawl_api_key",
        "web/firecrawl_api_key",
    ),
    "brave": ("web/brave_api_key", "brave/api_key", "brave_api_key"),
    "perplexity": ("web/perplexity_api_key", "perplexity/api_key", "pplx/api_key"),
    "gemini": ("web/gemini_api_key", "google/api_key", "google_api_key"),
    "grok": ("web/grok_api_key", "xai/api_key", "xai_api_key"),
    "kimi": ("web/kimi_api_key", "moonshot/api_key", "moonshot_api_key"),
    "tavily": ("web/tavily_api_key", "tavily/api_key", "tavily_api_key"),
}


# =============================================================================
# Cache
# =============================================================================


@dataclass
class CacheEntry:
    """Cache entry with expiration."""

    data: Any
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now() > self.expires_at


# Simple in-memory cache
_fetch_cache: dict[str, CacheEntry] = {}
_search_cache: dict[str, CacheEntry] = {}


def _cache_key(url_or_query: str, **params) -> str:
    """Generate a cache key from URL/query and params."""
    key_data = f"{url_or_query}:{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(key_data.encode()).hexdigest()


def _get_cached(cache: dict[str, CacheEntry], key: str) -> Any | None:
    """Get cached data if not expired."""
    entry = cache.get(key)
    if entry and not entry.is_expired():
        return entry.data
    if entry:
        del cache[key]
    return None


def _set_cached(cache: dict[str, CacheEntry], key: str, data: Any, ttl_minutes: int = 15):
    """Set cached data with TTL."""
    cache[key] = CacheEntry(data=data, expires_at=datetime.now() + timedelta(minutes=ttl_minutes))


# =============================================================================
# HTML to Markdown/Text Extraction
# =============================================================================


def _decode_entities(text: str) -> str:
    """Decode HTML entities."""
    text = html.unescape(text)
    # Additional common entities
    text = text.replace("&nbsp;", " ")
    return text


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities (text extraction helper, not a security sanitizer)."""
    from html.parser import HTMLParser as _HTMLParser

    class _Stripper(_HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self._parts.append(data)

    s = _Stripper()
    try:
        s.feed(text)
        return _decode_entities(" ".join(s._parts))
    except Exception:  # noqa: BLE001
        return _decode_entities(re.sub(r"<[^>]+>", "", text))


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text."""
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def html_to_markdown(html_content: str) -> dict[str, str | None]:
    """Convert HTML to markdown-like text.

    Args:
        html_content: Raw HTML string

    Returns:
        Dict with 'text' (markdown) and 'title' (page title if found)
    """
    # Extract title
    title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html_content, re.IGNORECASE)
    title = _normalize_whitespace(_strip_tags(title_match.group(1))) if title_match else None

    text = html_content

    # Remove scripts, styles, noscript using html.parser (text extraction, not a security filter)
    from html.parser import HTMLParser as _HTMLParser

    class _NoRenderStripper(_HTMLParser):
        _SKIP = frozenset({"script", "style", "noscript"})

        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self._parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag.lower() in self._SKIP:
                self._skip += 1
            else:
                attr_str = "".join(f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs)
                self._parts.append(f"<{tag}{attr_str}>")

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() in self._SKIP:
                self._skip = max(0, self._skip - 1)
            else:
                self._parts.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._parts.append(data)

        def handle_comment(self, data: str) -> None:
            pass  # Drop HTML comments

    _nrs = _NoRenderStripper()
    try:
        _nrs.feed(text)
        text = "".join(_nrs._parts)
    except Exception:  # noqa: BLE001
        pass  # Keep original; downstream conversion handles any remaining tags

    # Convert links
    def convert_link(match):
        href = match.group(1)
        body = _normalize_whitespace(_strip_tags(match.group(2)))
        if not body:
            return href
        return f"[{body}]({href})"

    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
        convert_link,
        text,
        flags=re.IGNORECASE,
    )

    # Convert headings
    def convert_heading(match):
        level = int(match.group(1))
        body = _normalize_whitespace(_strip_tags(match.group(2)))
        prefix = "#" * min(6, max(1, level))
        return f"\n{prefix} {body}\n"

    text = re.sub(r"<h([1-6])[^>]*>([\s\S]*?)</h\1>", convert_heading, text, flags=re.IGNORECASE)

    # Convert list items
    def convert_li(match):
        body = _normalize_whitespace(_strip_tags(match.group(1)))
        return f"\n- {body}" if body else ""

    text = re.sub(r"<li[^>]*>([\s\S]*?)</li>", convert_li, text, flags=re.IGNORECASE)

    # Convert code blocks
    text = re.sub(r"<pre[^>]*>([\s\S]*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<code[^>]*>([\s\S]*?)</code>", r"`\1`", text, flags=re.IGNORECASE)

    # Convert line breaks and block elements
    text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</(p|div|section|article|header|footer|table|tr|ul|ol)>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    # Strip remaining tags
    text = _strip_tags(text)
    text = _normalize_whitespace(text)

    return {"text": text, "title": title}


def markdown_to_text(markdown: str) -> str:
    """Convert markdown to plain text."""
    text = markdown
    # Remove images
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # Convert links to just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", ""), text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove heading markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove list markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    return _normalize_whitespace(text)


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate text to max_chars, returning (text, was_truncated)."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


# =============================================================================
# Web Fetch Tool
# =============================================================================


@dataclass
class WebFetchResult:
    """Result from web_fetch operation."""

    success: bool
    text: str = ""
    title: str | None = None
    final_url: str | None = None
    status_code: int | None = None
    error: str | None = None
    truncated: bool = False
    cached: bool = False


def web_fetch(
    url: str,
    extract_mode: str = "markdown",
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
    cache_ttl_minutes: int = DEFAULT_CACHE_TTL_MINUTES,
) -> WebFetchResult:
    """Fetch a URL and extract readable content.

    Args:
        url: HTTP or HTTPS URL to fetch
        extract_mode: 'markdown' or 'text'
        max_chars: Maximum characters to return (truncates when exceeded)
        timeout_seconds: Request timeout
        use_cache: Whether to use caching
        cache_ttl_minutes: Cache TTL in minutes

    Returns:
        WebFetchResult with extracted content or error
    """
    if not REQUESTS_AVAILABLE:
        return WebFetchResult(
            success=False,
            error="requests library not available. Install with: pip install requests",
        )

    # Validate URL
    if not url.startswith(("http://", "https://")):
        return WebFetchResult(
            success=False, error="Invalid URL: must start with http:// or https://"
        )

    # Check cache
    cache_key = _cache_key(url, mode=extract_mode, max_chars=max_chars)
    if use_cache:
        cached = _get_cached(_fetch_cache, cache_key)
        if cached:
            result = WebFetchResult(**cached)
            result.cached = True
            return result

    try:
        # Make request
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=True,
            verify=True,
        )

        final_url = response.url
        status_code = response.status_code

        if status_code >= 400:
            return WebFetchResult(
                success=False,
                status_code=status_code,
                final_url=final_url,
                error=f"HTTP error {status_code}: {response.reason}",
            )

        content = response.text

        # Extract readable content
        if TRAFILATURA_AVAILABLE:
            # Use trafilatura for better extraction
            extracted = trafilatura.extract(
                content,
                include_links=True,
                include_images=False,
                include_formatting=(extract_mode == "markdown"),
                output_format="markdown" if extract_mode == "markdown" else "txt",
            )
            if extracted:
                text = extracted
                # Try to get title
                title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", content, re.IGNORECASE)
                title = (
                    _normalize_whitespace(_strip_tags(title_match.group(1)))
                    if title_match
                    else None
                )
            else:
                # Fallback to basic extraction
                result = html_to_markdown(content)
                text = (
                    result["text"]
                    if extract_mode == "markdown"
                    else markdown_to_text(result["text"])
                )
                title = result["title"]
        else:
            # Use basic extraction
            result = html_to_markdown(content)
            if extract_mode == "markdown":
                text = result["text"]
            else:
                text = markdown_to_text(result["text"])
            title = result["title"]

        # Truncate if needed
        text, truncated = truncate_text(text, max_chars)

        result = WebFetchResult(
            success=True,
            text=text,
            title=title,
            final_url=final_url,
            status_code=status_code,
            truncated=truncated,
        )

        # Cache the result
        if use_cache:
            _set_cached(
                _fetch_cache,
                cache_key,
                {
                    "success": True,
                    "text": text,
                    "title": title,
                    "final_url": final_url,
                    "status_code": status_code,
                    "truncated": truncated,
                },
                cache_ttl_minutes,
            )

        return result

    except requests.Timeout:
        return WebFetchResult(
            success=False, error=f"Request timed out after {timeout_seconds} seconds"
        )
    except requests.RequestException as e:
        return WebFetchResult(success=False, error=f"Request failed: {str(e)}")
    except Exception as e:
        return WebFetchResult(success=False, error=f"Unexpected error: {str(e)}")


# =============================================================================
# Web Search Tool
# =============================================================================


@dataclass
class SearchResult:
    """Single search result."""

    title: str
    url: str
    snippet: str
    age: str | None = None


@dataclass
class WebSearchResult:
    """Result from web_search operation."""

    success: bool
    results: list[SearchResult] = field(default_factory=list)
    query: str = ""
    provider: str = ""
    error: str | None = None
    cached: bool = False


def _search_brave(
    query: str,
    api_key: str,
    count: int = 5,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> WebSearchResult:
    """Search using Brave Search API."""
    try:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }
        params = {
            "q": query,
            "count": min(count, 20),
        }

        response = requests.get(
            BRAVE_SEARCH_ENDPOINT,
            headers=headers,
            params=params,
            timeout=timeout_seconds,
        )

        if response.status_code != 200:
            return WebSearchResult(
                success=False,
                query=query,
                provider="brave",
                error=f"Brave API error {response.status_code}: {response.text[:500]}",
            )

        data = response.json()
        results = []

        web_results = data.get("web", {}).get("results", [])
        for item in web_results[:count]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    age=item.get("age"),
                )
            )

        return WebSearchResult(success=True, results=results, query=query, provider="brave")

    except Exception as e:
        return WebSearchResult(
            success=False,
            query=query,
            provider="brave",
            error=f"Brave search failed: {str(e)}",
        )


def _search_duckduckgo(
    query: str,
    count: int = 5,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> WebSearchResult:
    """Search using DuckDuckGo (limited, instant answer API)."""
    try:
        params = {
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
        }

        response = requests.get(
            DUCKDUCKGO_ENDPOINT,
            params=params,
            timeout=timeout_seconds,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

        if response.status_code != 200:
            return WebSearchResult(
                success=False,
                query=query,
                provider="duckduckgo",
                error=f"DuckDuckGo API error {response.status_code}",
            )

        data = response.json()
        results = []

        # DuckDuckGo Instant Answer API returns different formats
        # We'll try to extract useful results

        # Abstract (main result)
        if data.get("Abstract"):
            results.append(
                SearchResult(
                    title=data.get("Heading", "Result"),
                    url=data.get("AbstractURL", ""),
                    snippet=data.get("Abstract", ""),
                )
            )

        # Related topics
        for topic in data.get("RelatedTopics", [])[: count - len(results)]:
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append(
                    SearchResult(
                        title=topic.get("Text", "")[:100],
                        url=topic.get("FirstURL", ""),
                        snippet=topic.get("Text", ""),
                    )
                )

        # Results
        for item in data.get("Results", [])[: count - len(results)]:
            results.append(
                SearchResult(
                    title=item.get("Text", "")[:100],
                    url=item.get("FirstURL", ""),
                    snippet=item.get("Text", ""),
                )
            )

        if not results:
            return WebSearchResult(
                success=False,
                query=query,
                provider="duckduckgo",
                error="No results found. DuckDuckGo Instant Answer API is limited. Try Brave Search for better results.",
            )

        return WebSearchResult(success=True, results=results, query=query, provider="duckduckgo")

    except Exception as e:
        return WebSearchResult(
            success=False,
            query=query,
            provider="duckduckgo",
            error=f"DuckDuckGo search failed: {str(e)}",
        )


def _search_tavily(
    query: str,
    api_key: str,
    count: int = 5,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> WebSearchResult:
    """Search using Tavily Search API (RAG-optimized, LLM-native)."""
    try:
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": min(count, 10),
            "include_answer": False,
        }

        response = requests.post(
            TAVILY_SEARCH_ENDPOINT,
            json=payload,
            timeout=timeout_seconds,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code == 401:
            return WebSearchResult(
                success=False,
                query=query,
                provider="tavily",
                error="Tavily API key is invalid or expired.",
            )

        if response.status_code != 200:
            return WebSearchResult(
                success=False,
                query=query,
                provider="tavily",
                error=f"Tavily API error {response.status_code}: {response.text[:500]}",
            )

        data = response.json()
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                age=None,
            )
            for item in data.get("results", [])[:count]
        ]

        return WebSearchResult(success=True, results=results, query=query, provider="tavily")

    except Exception as e:
        return WebSearchResult(
            success=False,
            query=query,
            provider="tavily",
            error=f"Tavily search failed: {str(e)}",
        )


def _search_firecrawl(
    query: str,
    count: int = 5,
) -> WebSearchResult:
    """Search via Firecrawl with graceful error mapping.

    Firecrawl is treated as the premium default; callers should decide fallback behavior.
    """
    try:
        from navig.integrations.firecrawl import FirecrawlError, get_firecrawl_client

        client = get_firecrawl_client()
        data = client.search(query=query, limit=count, scrape_inline=False)

        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            if isinstance(data.get("data"), dict):
                nested = data.get("data") or {}
                if isinstance(nested.get("web"), list):
                    items = [i for i in nested.get("web", []) if isinstance(i, dict)]
                elif isinstance(nested.get("results"), list):
                    items = [i for i in nested.get("results", []) if isinstance(i, dict)]
            elif isinstance(data.get("results"), list):
                items = [i for i in data.get("results", []) if isinstance(i, dict)]

        results = [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(
                    item.get("description")
                    or item.get("snippet")
                    or item.get("content")
                    or ""
                ),
                age=item.get("age") if isinstance(item.get("age"), str) else None,
            )
            for item in items[:count]
            if item.get("url")
        ]

        if not results:
            return WebSearchResult(
                success=False,
                query=query,
                provider="firecrawl",
                error="No Firecrawl search results returned.",
            )

        return WebSearchResult(success=True, results=results, query=query, provider="firecrawl")

    except FirecrawlError as e:
        return WebSearchResult(
            success=False,
            query=query,
            provider="firecrawl",
            error=str(e),
        )
    except Exception as e:
        return WebSearchResult(
            success=False,
            query=query,
            provider="firecrawl",
            error=f"Firecrawl search failed: {str(e)}",
        )


def web_search(
    query: str,
    count: int = 5,
    provider: str = "auto",
    api_key: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
    cache_ttl_minutes: int = DEFAULT_CACHE_TTL_MINUTES,
) -> WebSearchResult:
    """Search the web for information.

    Args:
        query: Search query string
        count: Number of results to return (1-10)
        provider: 'brave', 'duckduckgo', or 'auto'
        api_key: API key for Brave Search (optional if BRAVE_API_KEY env var set)
        timeout_seconds: Request timeout
        use_cache: Whether to use caching
        cache_ttl_minutes: Cache TTL in minutes

    Returns:
        WebSearchResult with search results or error
    """
    if not REQUESTS_AVAILABLE:
        return WebSearchResult(
            success=False,
            query=query,
            error="requests library not available. Install with: pip install requests",
        )

    # Check cache
    cache_key = _cache_key(query, count=count, provider=provider)
    if use_cache:
        cached = _get_cached(_search_cache, cache_key)
        if cached:
            result = WebSearchResult(**cached)
            result.cached = True
            return result

    import os

    def _norm_provider(name: str) -> str:
        return _WEB_PROVIDER_ALIASES.get(str(name or "").strip().lower(), "auto")

    def _resolve_vault_key(provider_name: str) -> str:
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            for label in _WEB_PROVIDER_VAULT_LABELS.get(provider_name, ()):
                try:
                    value = (vault.get_secret(label) or "").strip()
                except Exception:
                    continue
                if value:
                    return value
        except Exception:
            pass  # best-effort: vault unavailable, skip key resolution
        return ""

    def _resolve_key(provider_name: str, search_cfg: dict[str, Any]) -> str:
        api_keys = search_cfg.get("api_keys") or {}

        explicit = (api_key or "").strip()
        if explicit:
            return explicit

        vault_value = _resolve_vault_key(provider_name)
        if vault_value:
            return vault_value

        if isinstance(api_keys, dict):
            candidate = str(api_keys.get(provider_name) or "").strip()
            if candidate:
                return candidate

        if provider_name == "brave":
            legacy_cfg = str(search_cfg.get("api_key") or "").strip()
            if legacy_cfg:
                return legacy_cfg

        for env_name in _WEB_PROVIDER_ENV_VARS.get(provider_name, ()):
            value = (os.environ.get(env_name) or "").strip()
            if value:
                return value

        return ""

    cfg = get_web_config()
    search_cfg = cfg.get("search", {}) if isinstance(cfg, dict) else {}
    config_provider = _norm_provider(str(search_cfg.get("provider") or "auto"))
    requested_provider = _norm_provider(provider)

    selected_provider = config_provider if requested_provider == "auto" else requested_provider
    if selected_provider == "auto":
        selected_provider = "firecrawl"

    selected_key = _resolve_key(selected_provider, search_cfg)

    # Runtime engine supports Firecrawl, Brave, DuckDuckGo, and Tavily.
    if selected_provider not in ("firecrawl", "brave", "duckduckgo", "tavily"):
        brave_key = _resolve_key("brave", search_cfg)
        if brave_key:
            selected_provider = "brave"
            selected_key = brave_key
        else:
            selected_provider = "duckduckgo"
            selected_key = ""

    # Perform search
    if selected_provider == "firecrawl":
        result = _search_firecrawl(query, count)
        if not result.success:
            if requested_provider == "firecrawl":
                return result

            tavily_key = _resolve_key("tavily", search_cfg)
            brave_key = _resolve_key("brave", search_cfg)
            if tavily_key:
                result = _search_tavily(query, tavily_key, count, timeout_seconds)
            elif brave_key:
                result = _search_brave(query, brave_key, count, timeout_seconds)
            else:
                result = _search_duckduckgo(query, count, timeout_seconds)
    elif selected_provider == "tavily":
        if not selected_key:
            result = _search_duckduckgo(query, count, timeout_seconds)
        else:
            result = _search_tavily(query, selected_key, count, timeout_seconds)
    elif selected_provider == "brave":
        if not selected_key:
            result = _search_duckduckgo(query, count, timeout_seconds)
        else:
            result = _search_brave(query, selected_key, count, timeout_seconds)
    else:
        result = _search_duckduckgo(query, count, timeout_seconds)

    # Cache successful results
    if use_cache and result.success:
        _set_cached(
            _search_cache,
            cache_key,
            {
                "success": True,
                "results": [
                    {"title": r.title, "url": r.url, "snippet": r.snippet, "age": r.age}
                    for r in result.results
                ],
                "query": result.query,
                "provider": result.provider,
            },
            cache_ttl_minutes,
        )

    return result


# =============================================================================
# Documentation Search (Local)
# =============================================================================


def search_docs(
    query: str,
    docs_path: Path | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Search NAVIG's local documentation.

    Args:
        query: Search query
        docs_path: Path to docs directory (defaults to navig/docs)
        max_results: Maximum results to return

    Returns:
        List of matching doc sections with file, title, and excerpt
    """
    if docs_path is None:
        # Try to find docs directory
        navig_root = Path(__file__).parent.parent
        docs_path = navig_root / "docs"
        if not docs_path.exists():
            docs_path = navig_root.parent / "docs"

    if not docs_path.exists():
        return []

    results = []
    query_lower = query.lower()
    query_words = set(query_lower.split())

    # Search through markdown files
    for md_file in docs_path.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            content_lower = content.lower()

            # Calculate relevance score
            score = 0

            # Check for query matches
            if query_lower in content_lower:
                score += 10

            # Check for word matches
            for word in query_words:
                if word in content_lower:
                    score += 1

            if score == 0:
                continue

            # Extract title (first # heading)
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_match.group(1) if title_match else md_file.stem

            # Find relevant excerpt
            excerpt = ""
            for line in content.split("\n"):
                if query_lower in line.lower():
                    excerpt = line.strip()[:200]
                    break

            if not excerpt:
                # Use first paragraph
                paragraphs = re.split(r"\n\n+", content)
                for p in paragraphs:
                    if not p.startswith("#") and len(p.strip()) > 50:
                        excerpt = p.strip()[:200]
                        break

            results.append(
                {
                    "file": str(md_file.relative_to(docs_path)),
                    "title": title,
                    "excerpt": excerpt,
                    "score": score,
                }
            )

        except Exception:
            continue

    # Sort by score and limit
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


# =============================================================================
# URL Detection Utilities
# =============================================================================

URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]]+', re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    return URL_PATTERN.findall(text)


def is_url_investigation_request(message: str) -> tuple[bool, str | None]:
    """Check if message is asking to investigate a URL.

    Args:
        message: User message

    Returns:
        (is_url_request, url_if_found)
    """
    message_lower = message.lower()

    # Keywords that suggest URL investigation
    url_triggers = [
        "investigate",
        "check this",
        "look at",
        "read this",
        "fetch",
        "analyze this url",
        "analyze this link",
        "analyze this page",
        "what does",
        "what is at",
        "summarize this",
        "tell me about this",
        "open this",
        "visit",
        "go to",
        "navigate to",
        "what's on",
        "what's at",
        "content of",
        "contents of",
    ]

    has_trigger = any(trigger in message_lower for trigger in url_triggers)

    # Extract URLs
    urls = extract_urls(message)

    if urls:
        # If there's a URL and a trigger, definitely a URL request
        if has_trigger:
            return True, urls[0]

        # If the message is mostly just a URL, treat as URL request
        other_text = message.replace(urls[0], "").strip()
        if len(other_text) < 20:  # Very little other text
            return True, urls[0]

    return False, None


# =============================================================================
# Configuration Helpers
# =============================================================================


def get_web_config(config_manager=None) -> dict[str, Any]:
    """Get web tools configuration.

    Returns config dict with:
        fetch:
            enabled: bool
            timeout_seconds: int
            max_chars: int
        search:
            enabled: bool
            provider: str
            api_key: str (from config or env)
    """
    import os

    default_config = {
        "fetch": {
            "enabled": True,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "max_chars": DEFAULT_MAX_CHARS,
        },
        "search": {
            "enabled": True,
            "provider": os.environ.get("NAVIG_WEB_SEARCH_PROVIDER", "auto"),
            "api_key": os.environ.get("BRAVE_API_KEY", ""),
            "api_keys": {},
        },
    }

    if config_manager is None:
        try:
            from navig.config import ConfigManager

            config_manager = ConfigManager()
        except Exception:
            return default_config

    try:
        web_config = config_manager.get_global_config_value("web") or {}

        # Merge with defaults
        for key in default_config:
            if key in web_config:
                default_config[key].update(web_config[key])

        # Get API key from env if not in config
        if not default_config["search"]["api_key"]:
            default_config["search"]["api_key"] = os.environ.get("BRAVE_API_KEY", "")

        return default_config

    except Exception:
        return default_config
