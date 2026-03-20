from datetime import datetime
from typing import Any, Dict, List


def register(server: Any) -> None:
    """Register system and context tools."""
    server.tools.update({
        "navig_list_databases": {
            "name": "navig_list_databases",
            "description": "List configured database connections in NAVIG.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
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
                        "default": True
                    }
                },
                "required": []
            }
        },
        "navig_run_command": {
            "name": "navig_run_command",
            "description": "Execute a NAVIG CLI command and return the result. Use for any navig operation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "NAVIG command to run (without 'navig' prefix)"
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command arguments"
                    }
                },
                "required": ["command"]
            }
        },
        "navig_web_fetch": {
            "name": "navig_web_fetch",
            "description": "Fetch a URL and extract readable content. Converts HTML to markdown or plain text for analysis.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to fetch"
                    },
                    "extract_mode": {
                        "type": "string",
                        "enum": ["markdown", "text"],
                        "description": "Extraction mode: 'markdown' (default) or 'text'",
                        "default": "markdown"
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (truncates when exceeded)",
                        "default": 50000
                    }
                },
                "required": ["url"]
            }
        },
        "navig_web_search": {
            "name": "navig_web_search",
            "description": "Search the web for information. Uses Brave Search API (if configured) or DuckDuckGo as fallback.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (1-10)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["query"]
            }
        },
        "navig_search_docs": {
            "name": "navig_search_docs",
            "description": "Search NAVIG's local documentation for commands, guides, and configuration help.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for documentation"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    })

    server._tool_handlers.update({
        "navig_list_databases": _tool_list_databases,
        "navig_get_context": _tool_get_context,
        "navig_run_command": _tool_run_command,
        "navig_web_fetch": _tool_web_fetch,
        "navig_web_search": _tool_web_search,
        "navig_search_docs": _tool_search_docs,
    })

def _tool_list_databases(server: Any, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List database connections."""
    databases = server._config.get_databases()
    return [
        {"name": name, **{k: v for k, v in config.items() if k != "password"}}
        for name, config in databases.items()
    ]

def _tool_get_context(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Get full NAVIG context."""
    from navig.ai_context import get_ai_context_manager
    from navig.mcp.tools.inventory import _tool_list_apps, _tool_list_hosts

    context_mgr = get_ai_context_manager()
    include_errors = args.get("include_errors", True)

    context = {
        "hosts": _tool_list_hosts(server, {}),
        "apps": _tool_list_apps(server, {}),
        "databases": _tool_list_databases(server, {}),
        "timestamp": datetime.now().isoformat()
    }

    if include_errors:
        context["recent_errors"] = context_mgr.get_recent_errors_for_ai(limit=10)

    return context

def _tool_run_command(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a NAVIG CLI command."""
    import subprocess

    command = args.get("command", "")
    cmd_args = args.get("args", [])

    # Safety: only allow read operations by default
    safe_commands = [
        "host list", "host show", "host info",
        "app list", "app show", "app info",
        "db list", "db show", "db tables",
        "wiki list", "wiki show", "wiki search",
        "tunnel list", "backup list",
        "status", "version", "help"
    ]

    full_cmd = command + " " + " ".join(cmd_args)
    is_safe = any(full_cmd.startswith(safe) for safe in safe_commands)

    if not is_safe:
        return {
            "error": f"Command not allowed: {full_cmd}",
            "hint": "Only read-only commands are allowed via MCP for safety"
        }

    try:
        result = subprocess.run(
            ["python", "-m", "navig.cli", command] + cmd_args,
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}

def _tool_web_fetch(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a URL and extract readable content."""
    try:
        from navig.tools.web import get_web_config, web_fetch

        url = args.get("url")
        if not url:
            return {"error": "URL is required"}

        # Get config
        config = get_web_config(server._config)
        fetch_config = config.get('fetch', {})

        if not fetch_config.get('enabled', True):
            return {"error": "Web fetch is disabled in configuration"}

        # Execute fetch
        result = web_fetch(
            url=url,
            extract_mode=args.get("extract_mode", "markdown"),
            max_chars=args.get("max_chars", fetch_config.get('max_chars', 50000)),
            timeout_seconds=fetch_config.get('timeout_seconds', 30)
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
            "cached": result.cached
        }

    except ImportError as e:
        return {"error": f"Web tools not available: {e}"}
    except Exception as e:
        return {"error": f"Fetch failed: {str(e)}"}

def _tool_web_search(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Search the web for information."""
    try:
        from navig.tools.web import get_web_config, web_search

        query = args.get("query")
        if not query:
            return {"error": "Search query is required"}

        # Get config
        config = get_web_config(server._config)
        search_config = config.get('search', {})

        if not search_config.get('enabled', True):
            return {"error": "Web search is disabled in configuration"}

        # Execute search
        result = web_search(
            query=query,
            count=args.get("count", 5),
            provider=search_config.get('provider', 'auto'),
            api_key=search_config.get('api_key')
        )

        if not result.success:
            return {"error": result.error}

        return {
            "success": True,
            "query": result.query,
            "provider": result.provider,
            "results": [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.snippet,
                    "age": r.age
                }
                for r in result.results
            ],
            "cached": result.cached
        }

    except ImportError as e:
        return {"error": f"Web tools not available: {e}"}
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}

def _tool_search_docs(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
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
        docs_path = navig_root / 'docs'

        results = search_docs(query, docs_path, max_results)

        if not results:
            return {
                "success": True,
                "message": f"No documentation found for '{query}'",
                "results": [],
                "suggestion": "Try the web search tool for broader results"
            }

        return {
            "success": True,
            "query": query,
            "results": results
        }

    except Exception as e:
        return {"error": f"Doc search failed: {str(e)}"}
