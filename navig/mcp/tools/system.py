import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def register(server: Any) -> None:
    """Register system and context tools."""
    server.tools.update(
        {
            "navig_list_databases": {
                "name": "navig_list_databases",
                "description": "List configured database connections in NAVIG.",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "navig_get_context": {
                "name": "navig_get_context",
                "description": "Get full NAVIG context including recent errors, active hosts, and system state for AI debugging.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "include_errors": {
                            "type": "boolean",
                            "description": "Include recent error logs",
                            "default": True,
                        }
                    },
                    "required": [],
                },
            },
            "navig_run_command": {
                "name": "navig_run_command",
                "description": "Execute a NAVIG CLI command and return the result. Use for any navig operation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "NAVIG command to run (without 'navig' prefix)",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command arguments",
                        },
                    },
                    "required": ["command"],
                },
            },
            "navig_web_fetch": {
                "name": "navig_web_fetch",
                "description": "Fetch a URL and extract readable content. Converts HTML to markdown or plain text for analysis.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTP or HTTPS URL to fetch",
                        },
                        "extract_mode": {
                            "type": "string",
                            "enum": ["markdown", "text"],
                            "description": "Extraction mode: 'markdown' (default) or 'text'",
                            "default": "markdown",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to return (truncates when exceeded)",
                            "default": 50000,
                        },
                    },
                    "required": ["url"],
                },
            },
            "navig_web_search": {
                "name": "navig_web_search",
                "description": "Search the web for information. Uses Brave Search API (if configured) or DuckDuckGo as fallback.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of results to return (1-10)",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
            "navig_search_docs": {
                "name": "navig_search_docs",
                "description": "Search NAVIG's local documentation for commands, guides, and configuration help.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for documentation",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
            "firecrawl_scrape": {
                "name": "firecrawl_scrape",
                "description": "Scrape or crawl a URL using Firecrawl. Returns markdown-oriented content and metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTP or HTTPS URL to scrape/crawl",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["scrape", "crawl"],
                            "default": "scrape",
                            "description": "Use 'scrape' for a single page or 'crawl' for multi-page discovery.",
                        },
                        "maxPages": {
                            "type": "integer",
                            "description": "Maximum pages for crawl mode",
                            "minimum": 1,
                        },
                    },
                    "required": ["url"],
                },
            },
            "firecrawl_crawl": {
                "name": "firecrawl_crawl",
                "description": "Crawl a site using Firecrawl and return crawl job metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTP or HTTPS URL to crawl",
                        },
                        "maxPages": {
                            "type": "integer",
                            "description": "Maximum pages to discover",
                            "minimum": 1,
                        },
                    },
                    "required": ["url"],
                },
            },
            "firecrawl_search": {
                "name": "firecrawl_search",
                "description": "Search the web using Firecrawl with optional inline scrape.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Maximum results",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 20,
                        },
                        "scrapeInline": {
                            "type": "boolean",
                            "description": "Whether to include inline markdown scraping",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "navig_list_databases": _tool_list_databases,
            "navig_get_context": _tool_get_context,
            "navig_run_command": _tool_run_command,
            "navig_web_fetch": _tool_web_fetch,
            "navig_web_search": _tool_web_search,
            "navig_search_docs": _tool_search_docs,
            "firecrawl_scrape": _tool_firecrawl_scrape,
            "firecrawl_crawl": _tool_firecrawl_crawl,
            "firecrawl_search": _tool_firecrawl_search,
        }
    )


def _tool_list_databases(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """List database connections."""
    databases = server._config.get_databases()
    return [
        {"name": name, **{k: v for k, v in config.items() if k != "password"}}
        for name, config in databases.items()
    ]


def _tool_get_context(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Get full NAVIG context."""
    from navig.ai_context import get_ai_context_manager
    from navig.mcp.tools.inventory import _tool_list_apps, _tool_list_hosts

    context_mgr = get_ai_context_manager()
    include_errors = args.get("include_errors", True)

    context = {
        "hosts": _tool_list_hosts(server, {}),
        "apps": _tool_list_apps(server, {}),
        "databases": _tool_list_databases(server, {}),
        "timestamp": datetime.now().isoformat(),
    }

    if include_errors:
        context["recent_errors"] = context_mgr.get_recent_errors_for_ai(limit=10)

    return context


def _tool_run_command(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a NAVIG CLI command."""
    import subprocess

    command = args.get("command", "")
    cmd_args = args.get("args", [])

    # Safety: only allow read operations by default
    safe_commands = [
        "host list",
        "host show",
        "host info",
        "app list",
        "app show",
        "app info",
        "db list",
        "db show",
        "db tables",
        "wiki list",
        "wiki show",
        "wiki search",
        "tunnel list",
        "backup list",
        "status",
        "version",
        "help",
    ]

    full_cmd = command + " " + " ".join(cmd_args)
    is_safe = any(full_cmd.startswith(safe) for safe in safe_commands)

    if not is_safe:
        return {
            "error": f"Command not allowed: {full_cmd}",
            "hint": "Only read-only commands are allowed via MCP for safety",
        }

    try:
        result = subprocess.run(
            ["python", "-m", "navig.cli", command] + cmd_args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}


def _tool_web_fetch(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a URL and extract readable content."""
    try:
        from navig.tools.web import get_web_config, web_fetch

        url = args.get("url")
        if not url:
            return {"error": "URL is required"}

        # Get config
        config = get_web_config(server._config)
        fetch_config = config.get("fetch", {})

        if not fetch_config.get("enabled", True):
            return {"error": "Web fetch is disabled in configuration"}

        # Execute fetch
        result = web_fetch(
            url=url,
            extract_mode=args.get("extract_mode", "markdown"),
            max_chars=args.get("max_chars", fetch_config.get("max_chars", 50000)),
            timeout_seconds=fetch_config.get("timeout_seconds", 30),
        )

        if not result.success:
            return {"error": result.error}

        return {
            "success": True,
            "text": result.text,
            "title": result.title,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "truncated": result.truncated,
            "cached": result.cached,
        }

    except ImportError as e:
        return {"error": f"Web tools not available: {e}"}
    except Exception as e:
        return {"error": f"Fetch failed: {str(e)}"}


def _tool_web_search(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Search the web for information."""
    try:
        from navig.tools.web import get_web_config, web_search

        query = args.get("query")
        if not query:
            return {"error": "Search query is required"}

        # Get config
        config = get_web_config(server._config)
        search_config = config.get("search", {})

        if not search_config.get("enabled", True):
            return {"error": "Web search is disabled in configuration"}

        # Execute search
        result = web_search(
            query=query,
            count=args.get("count", 5),
            provider=search_config.get("provider", "auto"),
            api_key=search_config.get("api_key"),
        )

        if not result.success:
            return {"error": result.error}

        return {
            "success": True,
            "query": result.query,
            "provider": result.provider,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet, "age": r.age}
                for r in result.results
            ],
            "cached": result.cached,
        }

    except ImportError as e:
        return {"error": f"Web tools not available: {e}"}
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}


def _tool_search_docs(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Search NAVIG's local documentation."""
    try:
        from pathlib import Path

        import navig
        from navig.tools.web import search_docs

        query = args.get("query")
        if not query:
            return {"error": "Search query is required"}

        max_results = args.get("max_results", 5)

        # Find docs path
        navig_root = Path(navig.__file__).parent.parent
        docs_path = navig_root / "docs"

        results = search_docs(query, docs_path, max_results)

        if not results:
            return {
                "success": True,
                "message": f"No documentation found for '{query}'",
                "results": [],
                "suggestion": "Try the web search tool for broader results",
            }

        return {"success": True, "query": query, "results": results}

    except Exception as e:
        return {"error": f"Doc search failed: {str(e)}"}


def _firecrawl_call_mcp_or_rest(
    server: Any,
    *,
    rest_callable: str,
    rest_kwargs: dict[str, Any],
    mcp_tool: str,
    mcp_args: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Run Firecrawl via MCP client when available, else via local REST client."""
    mcp_client = getattr(server, "_mcp_client", None)
    if mcp_client is not None and hasattr(mcp_client, "call_tool"):
        try:
            result = mcp_client.call_tool(mcp_tool, mcp_args)
            return {
                "success": True,
                "route": "mcp",
                "mode": mode,
                "result": result,
            }
        except Exception:
            logger.info("[firecrawl] MCP unavailable, using REST")
    else:
        logger.info("[firecrawl] MCP unavailable, using REST")

    from navig.integrations.firecrawl import get_firecrawl_client

    client = get_firecrawl_client()
    call = getattr(client, rest_callable)
    result = call(**rest_kwargs)
    return {
        "success": True,
        "route": "rest",
        "mode": mode,
        "result": result,
    }


def _tool_firecrawl_scrape(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Scrape or crawl a URL using Firecrawl with MCP-first fallback semantics."""
    try:
        from navig.integrations.firecrawl import FirecrawlError

        url = str(args.get("url") or "").strip()
        if not url:
            return {"error": "URL is required"}

        mode = str(args.get("mode") or "scrape").strip().lower()
        if mode not in {"scrape", "crawl"}:
            return {"error": "mode must be 'scrape' or 'crawl'"}

        max_pages = args.get("maxPages")
        if max_pages is not None:
            try:
                max_pages = int(max_pages)
            except (TypeError, ValueError):
                return {"error": "maxPages must be an integer"}

        mcp_args: dict[str, Any] = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
        if mode == "crawl" and max_pages is not None:
            mcp_args["limit"] = max_pages

        return _firecrawl_call_mcp_or_rest(
            server,
            rest_callable="scrape",
            rest_kwargs={"url": url, "mode": mode, "max_pages": max_pages},
            mcp_tool="mcp_firecrawl_fir_firecrawl_scrape",
            mcp_args=mcp_args,
            mode=mode,
        )

    except FirecrawlError as e:
        return {
            "error": str(e),
            "status_code": e.status_code,
            "retryable": e.retryable,
        }
    except Exception as e:
        return {"error": f"Firecrawl request failed: {str(e)}"}


def _tool_firecrawl_crawl(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Crawl a URL using Firecrawl with MCP-first fallback semantics."""
    try:
        from navig.integrations.firecrawl import FirecrawlError

        url = str(args.get("url") or "").strip()
        if not url:
            return {"error": "URL is required"}

        max_pages = args.get("maxPages")
        if max_pages is not None:
            try:
                max_pages = int(max_pages)
            except (TypeError, ValueError):
                return {"error": "maxPages must be an integer"}

        mcp_args: dict[str, Any] = {
            "url": url,
            "limit": max_pages,
            "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
        }
        if max_pages is None:
            mcp_args.pop("limit", None)

        return _firecrawl_call_mcp_or_rest(
            server,
            rest_callable="crawl",
            rest_kwargs={"url": url, "max_pages": max_pages},
            mcp_tool="mcp_firecrawl_fir_firecrawl_crawl",
            mcp_args=mcp_args,
            mode="crawl",
        )

    except FirecrawlError as e:
        return {
            "error": str(e),
            "status_code": e.status_code,
            "retryable": e.retryable,
        }
    except Exception as e:
        return {"error": f"Firecrawl request failed: {str(e)}"}


def _tool_firecrawl_search(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Search via Firecrawl with MCP-first fallback semantics."""
    try:
        from navig.integrations.firecrawl import FirecrawlError

        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "Search query is required"}

        count = args.get("count", 5)
        try:
            count = int(count)
        except (TypeError, ValueError):
            return {"error": "count must be an integer"}

        scrape_inline = bool(args.get("scrapeInline", False))

        mcp_args: dict[str, Any] = {
            "query": query,
            "limit": count,
            "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True}
            if scrape_inline
            else None,
        }
        if not scrape_inline:
            mcp_args.pop("scrapeOptions", None)

        return _firecrawl_call_mcp_or_rest(
            server,
            rest_callable="search",
            rest_kwargs={"query": query, "limit": count, "scrape_inline": scrape_inline},
            mcp_tool="mcp_firecrawl_fir_firecrawl_search",
            mcp_args=mcp_args,
            mode="search",
        )

    except FirecrawlError as e:
        return {
            "error": str(e),
            "status_code": e.status_code,
            "retryable": e.retryable,
        }
    except Exception as e:
        return {"error": f"Firecrawl request failed: {str(e)}"}
