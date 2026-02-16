"""NAVIG MCP Server

Model Context Protocol server that exposes NAVIG's capabilities to AI assistants.
This allows Copilot, Claude, and other MCP-compatible assistants to:
- Query hosts, apps, and database configurations
- Search the wiki knowledge base
- Execute commands on remote servers
- Access project context

Usage:
    navig mcp serve                          # Start MCP server (stdio mode)
    navig mcp serve --transport websocket    # Start WebSocket server on port 3001
    navig mcp serve --port 3001             # Same as above (port implies websocket)

VS Code Integration:
    Add to .vscode/mcp.json or VS Code settings
"""

import json
import sys
import asyncio
import secrets
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta

from navig import console_helper as ch
from navig.config import ConfigManager


class MCPProtocolHandler:
    """Handles MCP JSON-RPC protocol over stdio."""
    
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.prompts: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, Callable] = {}
        self._running = False
        self._config = ConfigManager()
        
        # Register built-in methods
        self._setup_protocol_handlers()
        self._setup_navig_tools()
        self._setup_navig_resources()
    
    def _setup_protocol_handlers(self):
        """Setup MCP protocol method handlers."""
        self._handlers = {}
        self._handlers["initialize"] = self._handle_initialize
        self._handlers["initialized"] = self._handle_initialized
        self._handlers["tools/list"] = self._handle_tools_list
        self._handlers["tools/call"] = self._handle_tools_call
        self._handlers["resources/list"] = self._handle_resources_list
        self._handlers["resources/read"] = self._handle_resources_read
        self._handlers["prompts/list"] = self._handle_prompts_list
        self._handlers["prompts/get"] = self._handle_prompts_get
        self._handlers["ping"] = self._handle_ping
    
    def _setup_navig_tools(self):
        """Register NAVIG tools for MCP."""
        self.tools = {
            "navig_list_hosts": {
                "name": "navig_list_hosts",
                "description": "List all configured SSH hosts in NAVIG. Returns host names, addresses, and connection details.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Optional filter pattern for host names"
                        }
                    },
                    "required": []
                }
            },
            "navig_list_apps": {
                "name": "navig_list_apps",
                "description": "List all configured applications in NAVIG. Returns app names, types, and associated hosts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "host": {
                            "type": "string",
                            "description": "Filter apps by host name"
                        }
                    },
                    "required": []
                }
            },
            "navig_host_info": {
                "name": "navig_host_info",
                "description": "Get detailed information about a specific SSH host including connection settings, paths, and apps.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Host name to get info for"
                        }
                    },
                    "required": ["name"]
                }
            },
            "navig_app_info": {
                "name": "navig_app_info",
                "description": "Get detailed information about a specific application including config, paths, and status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Application name to get info for"
                        }
                    },
                    "required": ["name"]
                }
            },
            "navig_search_wiki": {
                "name": "navig_search_wiki",
                "description": "Search the NAVIG wiki knowledge base for relevant documentation, guides, and notes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            },
            "navig_list_wiki_pages": {
                "name": "navig_list_wiki_pages",
                "description": "List all pages in the NAVIG wiki knowledge base.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Filter by folder (knowledge, technical, hub, external)"
                        }
                    },
                    "required": []
                }
            },
            "navig_read_wiki_page": {
                "name": "navig_read_wiki_page",
                "description": "Read the content of a specific wiki page.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Wiki page path (e.g., 'knowledge/concepts/overview')"
                        }
                    },
                    "required": ["path"]
                }
            },
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
            },
            "navig_agent_status_get": {
                "name": "navig_agent_status_get",
                "description": "Get autonomous agent runtime/config status including mode, personality, workspace, PID, and running state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            "navig_agent_goal_list": {
                "name": "navig_agent_goal_list",
                "description": "List autonomous agent goals with state/progress summary.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "description": "Optional state filter: pending, in_progress, blocked, completed, failed, cancelled"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum goals to return",
                            "default": 50
                        }
                    },
                    "required": []
                }
            },
            "navig_agent_goal_add": {
                "name": "navig_agent_goal_add",
                "description": "Create a new autonomous agent goal.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Goal description"
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional metadata object"
                        }
                    },
                    "required": ["description"]
                }
            },
            "navig_agent_goal_start": {
                "name": "navig_agent_goal_start",
                "description": "Start execution for a pending/blocked autonomous agent goal.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Goal ID"
                        }
                    },
                    "required": ["id"]
                }
            },
            "navig_agent_goal_cancel": {
                "name": "navig_agent_goal_cancel",
                "description": "Cancel an autonomous agent goal by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Goal ID"
                        }
                    },
                    "required": ["id"]
                }
            },
            "navig_agent_remediation_list": {
                "name": "navig_agent_remediation_list",
                "description": "List persisted remediation actions and recent remediation log entries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum remediation actions to return",
                            "default": 100
                        }
                    },
                    "required": []
                }
            },
            "navig_agent_learning_run": {
                "name": "navig_agent_learning_run",
                "description": "Analyze recent agent debug/remediation logs and return recurring error patterns with recommendations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Analyze logs from the last N days",
                            "default": 7
                        },
                        "export": {
                            "type": "boolean",
                            "description": "Export pattern report to ~/.navig/workspace/error-patterns.json",
                            "default": False
                        }
                    },
                    "required": []
                }
            },
            "navig_agent_service_status": {
                "name": "navig_agent_service_status",
                "description": "Get OS-level service status for NAVIG agent (systemd/launchd/windows service).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            "navig_agent_component_restart": {
                "name": "navig_agent_component_restart",
                "description": "Queue a remediation restart action for an agent component.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component": {
                            "type": "string",
                            "description": "Component name (brain, eyes, ears, hands, soul, heart, nervous_system)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why restart is requested",
                            "default": "requested_via_mcp"
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional metadata"
                        }
                    },
                    "required": ["component"]
                }
            },
            "navig_agent_remediation_retry": {
                "name": "navig_agent_remediation_retry",
                "description": "Retry a remediation action by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Remediation action ID"
                        },
                        "reset_attempts": {
                            "type": "boolean",
                            "description": "Reset attempt counter before retrying",
                            "default": True
                        }
                    },
                    "required": ["id"]
                }
            },
            "navig_agent_service_install": {
                "name": "navig_agent_service_install",
                "description": "Install NAVIG agent as an OS service.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "start_now": {
                            "type": "boolean",
                            "description": "Start service immediately after install",
                            "default": True
                        }
                    },
                    "required": []
                }
            },
            "navig_agent_service_uninstall": {
                "name": "navig_agent_service_uninstall",
                "description": "Uninstall NAVIG agent OS service.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
    
    def _setup_navig_resources(self):
        """Register NAVIG resources for MCP."""
        self.resources = {
            "navig://config/hosts": {
                "uri": "navig://config/hosts",
                "name": "NAVIG Hosts Configuration",
                "description": "All configured SSH hosts",
                "mimeType": "application/json"
            },
            "navig://config/apps": {
                "uri": "navig://config/apps",
                "name": "NAVIG Apps Configuration",
                "description": "All configured applications",
                "mimeType": "application/json"
            },
            "navig://wiki": {
                "uri": "navig://wiki",
                "name": "NAVIG Wiki",
                "description": "Project knowledge base",
                "mimeType": "text/markdown"
            },
            "navig://context": {
                "uri": "navig://context",
                "name": "NAVIG Context",
                "description": "Current system state and recent errors",
                "mimeType": "application/json"
            },
            "navig://agent/status": {
                "uri": "navig://agent/status",
                "name": "NAVIG Agent Status",
                "description": "Agent runtime status and configuration",
                "mimeType": "application/json"
            },
            "navig://agent/goals": {
                "uri": "navig://agent/goals",
                "name": "NAVIG Agent Goals",
                "description": "Autonomous goal list and progress",
                "mimeType": "application/json"
            },
            "navig://agent/remediation": {
                "uri": "navig://agent/remediation",
                "name": "NAVIG Agent Remediation",
                "description": "Remediation actions and recent remediation logs",
                "mimeType": "application/json"
            },
            "navig://agent/learning": {
                "uri": "navig://agent/learning",
                "name": "NAVIG Agent Learning Report",
                "description": "Latest error pattern analysis report",
                "mimeType": "application/json"
            },
            "navig://agent/service": {
                "uri": "navig://agent/service",
                "name": "NAVIG Agent Service Status",
                "description": "Service installer/status integration",
                "mimeType": "application/json"
            },
            "agent://status": {
                "uri": "agent://status",
                "name": "Agent Status",
                "description": "Alias for NAVIG agent runtime status",
                "mimeType": "application/json"
            },
            "agent://goals": {
                "uri": "agent://goals",
                "name": "Agent Goals",
                "description": "Alias for agent goals",
                "mimeType": "application/json"
            },
            "agent://remediation": {
                "uri": "agent://remediation",
                "name": "Agent Remediation",
                "description": "Alias for remediation actions",
                "mimeType": "application/json"
            },
            "agent://learning/patterns": {
                "uri": "agent://learning/patterns",
                "name": "Agent Learning Patterns",
                "description": "Alias for learning report",
                "mimeType": "application/json"
            },
            "agent://service": {
                "uri": "agent://service",
                "name": "Agent Service",
                "description": "Alias for service status",
                "mimeType": "application/json"
            }
        }
    
    # =========================================================================
    # MCP Protocol Handlers
    # =========================================================================
    
    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MCP initialize request."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {}
            },
            "serverInfo": {
                "name": "navig-mcp-server",
                "version": "1.0.0"
            }
        }
    
    def _handle_initialized(self, params: Dict[str, Any]) -> None:
        """Handle MCP initialized notification."""
        return None
    
    def _handle_ping(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle ping request."""
        return {}
    
    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return list of available tools."""
        return {
            "tools": list(self.tools.values())
        }
    
    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool call."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name not in self.tools:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]
            }
        
        try:
            result = self._execute_tool(tool_name, arguments)
            return {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
            }
        except Exception as e:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool error: {str(e)}"}]
            }
    
    def _handle_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return list of available resources."""
        return {
            "resources": list(self.resources.values())
        }
    
    def _handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Read a resource."""
        uri = params.get("uri")
        
        if uri not in self.resources:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown resource: {uri}"}]
            }
        
        try:
            content = self._read_resource(uri)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": self.resources[uri].get("mimeType", "text/plain"),
                    "text": content
                }]
            }
        except Exception as e:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Resource error: {str(e)}"}]
            }
    
    def _handle_prompts_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return list of available prompts."""
        return {"prompts": []}
    
    def _handle_prompts_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get a specific prompt."""
        return {"messages": []}
    
    # =========================================================================
    # Tool Implementations
    # =========================================================================
    
    def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool and return result."""
        if tool_name == "navig_list_hosts":
            return self._tool_list_hosts(arguments)
        elif tool_name == "navig_list_apps":
            return self._tool_list_apps(arguments)
        elif tool_name == "navig_host_info":
            return self._tool_host_info(arguments)
        elif tool_name == "navig_app_info":
            return self._tool_app_info(arguments)
        elif tool_name == "navig_search_wiki":
            return self._tool_search_wiki(arguments)
        elif tool_name == "navig_list_wiki_pages":
            return self._tool_list_wiki_pages(arguments)
        elif tool_name == "navig_read_wiki_page":
            return self._tool_read_wiki_page(arguments)
        elif tool_name == "navig_list_databases":
            return self._tool_list_databases(arguments)
        elif tool_name == "navig_get_context":
            return self._tool_get_context(arguments)
        elif tool_name == "navig_run_command":
            return self._tool_run_command(arguments)
        elif tool_name == "navig_web_fetch":
            return self._tool_web_fetch(arguments)
        elif tool_name == "navig_web_search":
            return self._tool_web_search(arguments)
        elif tool_name == "navig_search_docs":
            return self._tool_search_docs(arguments)
        elif tool_name == "navig_agent_status_get":
            return self._tool_agent_status_get(arguments)
        elif tool_name == "navig_agent_goal_list":
            return self._tool_agent_goal_list(arguments)
        elif tool_name == "navig_agent_goal_add":
            return self._tool_agent_goal_add(arguments)
        elif tool_name == "navig_agent_goal_start":
            return self._tool_agent_goal_start(arguments)
        elif tool_name == "navig_agent_goal_cancel":
            return self._tool_agent_goal_cancel(arguments)
        elif tool_name == "navig_agent_remediation_list":
            return self._tool_agent_remediation_list(arguments)
        elif tool_name == "navig_agent_learning_run":
            return self._tool_agent_learning_run(arguments)
        elif tool_name == "navig_agent_service_status":
            return self._tool_agent_service_status(arguments)
        elif tool_name == "navig_agent_component_restart":
            return self._tool_agent_component_restart(arguments)
        elif tool_name == "navig_agent_remediation_retry":
            return self._tool_agent_remediation_retry(arguments)
        elif tool_name == "navig_agent_service_install":
            return self._tool_agent_service_install(arguments)
        elif tool_name == "navig_agent_service_uninstall":
            return self._tool_agent_service_uninstall(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _tool_list_hosts(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List all configured hosts."""
        hosts = self._config.get_hosts()
        filter_pattern = args.get("filter", "").lower()
        
        result = []
        for name, config in hosts.items():
            if filter_pattern and filter_pattern not in name.lower():
                continue
            result.append({
                "name": name,
                "host": config.get("host"),
                "user": config.get("user"),
                "port": config.get("port", 22),
                "key": config.get("key"),
                "apps": list(config.get("apps", {}).keys()) if config.get("apps") else []
            })
        
        return result
    
    def _tool_list_apps(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List all configured apps."""
        host_filter = args.get("host")
        apps = self._config.get_apps()
        hosts = self._config.get_hosts()
        
        result = []
        
        # Apps from global apps config
        for name, config in apps.items():
            app_host = config.get("host")
            if host_filter and app_host != host_filter:
                continue
            result.append({
                "name": name,
                "type": config.get("type"),
                "host": app_host,
                "path": config.get("path"),
                "url": config.get("url")
            })
        
        # Apps embedded in hosts
        for host_name, host_config in hosts.items():
            if host_filter and host_name != host_filter:
                continue
            for app_name, app_config in host_config.get("apps", {}).items():
                result.append({
                    "name": app_name,
                    "type": app_config.get("type"),
                    "host": host_name,
                    "path": app_config.get("path"),
                    "url": app_config.get("url")
                })
        
        return result
    
    def _tool_host_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed host information."""
        name = args.get("name")
        hosts = self._config.get_hosts()
        
        if name not in hosts:
            return {"error": f"Host not found: {name}"}
        
        return {
            "name": name,
            **hosts[name]
        }
    
    def _tool_app_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed app information."""
        name = args.get("name")
        apps = self._config.get_apps()
        
        if name in apps:
            return {"name": name, **apps[name]}
        
        # Search in host apps
        hosts = self._config.get_hosts()
        for host_name, host_config in hosts.items():
            if name in host_config.get("apps", {}):
                return {
                    "name": name,
                    "host": host_name,
                    **host_config["apps"][name]
                }
        
        return {"error": f"App not found: {name}"}
    
    def _tool_search_wiki(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Search wiki pages."""
        from navig.commands.wiki import search_wiki, get_wiki_path
        
        wiki_path = get_wiki_path(self._config)
        if not wiki_path.exists():
            return {"error": "Wiki not initialized"}
        
        query = args.get("query", "")
        limit = args.get("limit", 10)
        
        results = search_wiki(wiki_path, query)
        return results[:limit]
    
    def _tool_list_wiki_pages(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List wiki pages."""
        from navig.commands.wiki import list_wiki_pages, get_wiki_path
        
        wiki_path = get_wiki_path(self._config)
        if not wiki_path.exists():
            return {"error": "Wiki not initialized"}
        
        folder = args.get("folder")
        pages = list_wiki_pages(wiki_path, folder)
        
        return [
            {
                "path": p["path"],
                "title": p["title"],
                "folder": p["folder"],
                "modified": p["modified"].isoformat() if hasattr(p["modified"], "isoformat") else str(p["modified"])
            }
            for p in pages
        ]
    
    def _tool_read_wiki_page(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Read a wiki page."""
        from navig.commands.wiki import get_wiki_path, resolve_wiki_link
        
        wiki_path = get_wiki_path(self._config)
        if not wiki_path.exists():
            return {"error": "Wiki not initialized"}
        
        page_path = args.get("path", "")
        resolved = resolve_wiki_link(wiki_path, page_path)
        
        if not resolved:
            return {"error": f"Page not found: {page_path}"}
        
        content = resolved.read_text(encoding="utf-8")
        return {
            "path": str(resolved.relative_to(wiki_path)),
            "content": content
        }
    
    def _tool_list_databases(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List database connections."""
        databases = self._config.get_databases()
        return [
            {"name": name, **{k: v for k, v in config.items() if k != "password"}}
            for name, config in databases.items()
        ]
    
    def _tool_get_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get full NAVIG context."""
        from navig.ai_context import get_ai_context_manager
        
        context_mgr = get_ai_context_manager()
        include_errors = args.get("include_errors", True)
        
        context = {
            "hosts": self._tool_list_hosts({}),
            "apps": self._tool_list_apps({}),
            "databases": self._tool_list_databases({}),
            "timestamp": datetime.now().isoformat()
        }
        
        if include_errors:
            context["recent_errors"] = context_mgr.get_recent_errors_for_ai(limit=10)
        
        return context
    
    def _tool_run_command(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a NAVIG command."""
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
    
    def _tool_web_fetch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a URL and extract readable content."""
        try:
            from navig.tools.web import web_fetch, get_web_config
            
            url = args.get("url")
            if not url:
                return {"error": "URL is required"}
            
            # Get config
            config = get_web_config(self._config)
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
    
    def _tool_web_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search the web for information."""
        try:
            from navig.tools.web import web_search, get_web_config
            
            query = args.get("query")
            if not query:
                return {"error": "Search query is required"}
            
            # Get config
            config = get_web_config(self._config)
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
    
    def _tool_search_docs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search NAVIG's local documentation."""
        try:
            from navig.tools.web import search_docs
            from pathlib import Path
            
            query = args.get("query")
            if not query:
                return {"error": "Search query is required"}
            
            max_results = args.get("max_results", 5)
            
            # Find docs path
            navig_root = Path(__file__).parent.parent
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

    def _tool_agent_status_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return agent install/runtime status for control plane clients."""
        import os
        import platform
        import subprocess

        config_path = Path.home() / '.navig' / 'agent' / 'config.yaml'
        pid_path = Path.home() / '.navig' / 'agent' / 'agent.pid'
        installed = config_path.exists()
        running = False
        pid: Optional[int] = None

        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding='utf-8').strip())
                if platform.system().lower().startswith('win'):
                    result = subprocess.run(
                        ['tasklist', '/FI', f'PID eq {pid}'],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    running = str(pid) in result.stdout
                else:
                    os.kill(pid, 0)
                    running = True
            except Exception:
                running = False

        mode = None
        personality = None
        workspace = None
        if installed:
            try:
                from navig.agent.config import AgentConfig
                cfg = AgentConfig.load(config_path)
                mode = cfg.mode
                personality = cfg.personality.profile
                workspace = str(cfg.workspace)
            except Exception:
                mode = "unknown"
                personality = "unknown"

        return {
            "installed": installed,
            "running": running,
            "pid": pid if running else None,
            "config_path": str(config_path),
            "mode": mode,
            "personality": personality,
            "workspace": workspace,
            "timestamp": datetime.now().isoformat(),
        }

    def _resolve_goal_storage_dir(self) -> Path:
        """Resolve the most likely goal storage directory."""
        candidates: List[Path] = []
        try:
            from navig.agent.config import AgentConfig
            cfg_path = Path.home() / '.navig' / 'agent' / 'config.yaml'
            if cfg_path.exists():
                cfg = AgentConfig.load(cfg_path)
                candidates.append(cfg.workspace)
        except Exception:
            pass

        candidates.append(Path.home() / '.navig' / 'workspace')
        if not candidates:
            return Path.home() / '.navig' / 'workspace'

        for candidate in candidates:
            if (candidate / 'goals.json').exists():
                return candidate
        return candidates[0]

    def _get_goal_planner(self):
        """Create a GoalPlanner against resolved storage."""
        from navig.agent.goals import GoalPlanner
        storage_dir = self._resolve_goal_storage_dir()
        return GoalPlanner(storage_dir=storage_dir)

    def _tool_agent_goal_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List goals with summary fields for dashboard clients."""
        from navig.agent.goals import GoalState

        planner = self._get_goal_planner()
        limit = max(1, int(args.get("limit", 50)))
        state_name = args.get("state")
        state_filter = None
        if state_name:
            try:
                state_filter = GoalState(state_name)
            except ValueError:
                return {"error": f"Invalid state: {state_name}"}

        goals = planner.list_goals(state_filter)[:limit]
        return {
            "storage_dir": str(planner.storage_dir),
            "count": len(goals),
            "goals": [
                {
                    "id": g.id,
                    "description": g.description,
                    "state": g.state.value,
                    "progress": g.progress,
                    "subtasks": len(g.subtasks),
                    "created_at": g.created_at.isoformat(),
                    "started_at": g.started_at.isoformat() if g.started_at else None,
                    "completed_at": g.completed_at.isoformat() if g.completed_at else None,
                    "metadata": g.metadata,
                }
                for g in goals
            ],
        }

    def _tool_agent_goal_add(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new agent goal."""
        planner = self._get_goal_planner()
        description = args.get("description", "").strip()
        if not description:
            return {"error": "description is required"}

        metadata = args.get("metadata", {})
        if metadata is None or not isinstance(metadata, dict):
            metadata = {}

        goal_id = planner.add_goal(description, metadata=metadata)
        return {
            "ok": True,
            "goal_id": goal_id,
            "storage_dir": str(planner.storage_dir),
        }

    def _tool_agent_goal_start(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Start an existing goal."""
        planner = self._get_goal_planner()
        goal_id = str(args.get("id", "")).strip()
        if not goal_id:
            return {"error": "id is required"}

        success = planner.start_goal(goal_id)
        goal = planner.get_goal(goal_id)
        return {
            "ok": success,
            "goal_id": goal_id,
            "state": goal.state.value if goal else None,
        }

    def _tool_agent_goal_cancel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Cancel an existing goal."""
        planner = self._get_goal_planner()
        goal_id = str(args.get("id", "")).strip()
        if not goal_id:
            return {"error": "id is required"}

        success = planner.cancel_goal(goal_id)
        goal = planner.get_goal(goal_id)
        return {
            "ok": success,
            "goal_id": goal_id,
            "state": goal.state.value if goal else None,
        }

    def _read_recent_remediation_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Parse recent remediation log lines into structured entries."""
        import re

        log_path = Path.home() / '.navig' / 'logs' / 'remediation.log'
        if not log_path.exists():
            return []

        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
        entries: List[Dict[str, Any]] = []
        regex = re.compile(r'^\[(?P<ts>[^\]]+)\] \[(?P<level>[^\]]+)\] (?P<msg>.*)$')
        for line in lines[-limit:]:
            m = regex.match(line)
            if not m:
                continue
            entries.append(
                {
                    "timestamp": m.group('ts'),
                    "level": m.group('level').lower(),
                    "message": m.group('msg'),
                }
            )
        return entries

    def _tool_agent_remediation_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List remediation actions and recent remediation log lines."""
        limit = max(1, int(args.get("limit", 100)))
        actions: List[Dict[str, Any]] = []
        source = "none"

        try:
            from navig.agent.remediation import RemediationEngine
            engine = RemediationEngine()
            actions = engine.get_all_actions()
            if actions:
                source = "actions_file"
        except Exception:
            actions = []

        log_entries = self._read_recent_remediation_log(limit=limit)
        if source == "none" and log_entries:
            source = "log_only"

        return {
            "source": source,
            "count": len(actions),
            "actions": actions[:limit],
            "recent_log_entries": log_entries,
        }

    def _tool_agent_learning_run(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze agent logs and return pattern counts plus recommendations."""
        import re
        from collections import defaultdict

        days = max(1, int(args.get("days", 7)))
        export = bool(args.get("export", False))
        cutoff = datetime.now() - timedelta(days=days)
        log_dir = Path.home() / '.navig' / 'logs'
        debug_log = log_dir / 'debug.log'
        remediation_log = log_dir / 'remediation.log'

        patterns = {
            'connection_failed': r'connection.*(failed|refused|timeout)',
            'permission_denied': r'permission denied|access denied',
            'config_error': r'config.*error|invalid.*config',
            'component_error': r'component.*error|failed to start',
            'resource_exhausted': r'out of memory|disk full|quota exceeded',
        }

        counts = defaultdict(int)
        examples = defaultdict(list)
        ts_regex = re.compile(r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')

        for log_file in (debug_log, remediation_log):
            if not log_file.exists():
                continue
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    ts_match = ts_regex.match(line)
                    if ts_match:
                        try:
                            line_ts = datetime.strptime(ts_match.group('ts'), '%Y-%m-%d %H:%M:%S')
                            if line_ts < cutoff:
                                continue
                        except ValueError:
                            pass

                    for pattern_name, pattern in patterns.items():
                        if re.search(pattern, line, re.IGNORECASE):
                            counts[pattern_name] += 1
                            if len(examples[pattern_name]) < 3:
                                examples[pattern_name].append(line)

        recommendations: List[str] = []
        if counts.get('connection_failed', 0) > 10:
            recommendations.append('Review network connectivity and firewall rules.')
        if counts.get('permission_denied', 0) > 5:
            recommendations.append('Check file permissions and user access rights.')
        if counts.get('config_error', 0) > 3:
            recommendations.append('Validate configuration files for syntax/structure errors.')
        if counts.get('component_error', 0) > 5:
            recommendations.append('Investigate recurring component lifecycle failures.')
        if counts.get('resource_exhausted', 0) > 0:
            recommendations.append('Critical: check host memory/disk pressure immediately.')

        result = {
            'analyzed_at': datetime.now().isoformat(),
            'days': days,
            'total_errors': int(sum(counts.values())),
            'patterns': {
                name: {'count': count, 'examples': examples[name]}
                for name, count in counts.items()
            },
            'recommendations': recommendations,
        }

        if export:
            output_path = Path.home() / '.navig' / 'workspace' / 'error-patterns.json'
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
            result['exported_to'] = str(output_path)

        return result

    def _tool_agent_service_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return OS service status for NAVIG agent."""
        capabilities = self._get_service_capabilities()
        try:
            from navig.agent.service import ServiceInstaller
            installer = ServiceInstaller()
            is_running, status_text = installer.status()
            return {
                "running": bool(is_running),
                "platform": installer.system,
                "status": status_text,
                **capabilities,
            }
        except Exception as e:
            return {
                "error": f"service status failed: {e}",
                **capabilities,
            }

    def _get_service_capabilities(self) -> Dict[str, Any]:
        """Return platform/elevation capability flags for service operations."""
        import os
        import platform

        system = platform.system().lower()
        is_elevated = False
        if system == "windows":
            try:
                import ctypes
                is_elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
            except Exception:
                is_elevated = False
            return {
                "can_install": is_elevated,
                "can_uninstall": is_elevated,
                "requires_elevation": not is_elevated,
                "is_elevated": is_elevated,
            }

        if system in ("linux", "darwin"):
            if hasattr(os, "geteuid"):
                try:
                    is_elevated = os.geteuid() == 0
                except Exception:
                    is_elevated = False
            return {
                "can_install": True,
                "can_uninstall": True,
                "requires_elevation": False,
                "is_elevated": is_elevated,
            }

        return {
            "can_install": False,
            "can_uninstall": False,
            "requires_elevation": False,
            "is_elevated": False,
        }

    def _tool_agent_component_restart(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Queue a component restart through remediation engine."""
        from navig.agent.remediation import RemediationEngine

        component = str(args.get("component", "")).strip()
        if not component:
            return {"error": "component is required"}

        reason = str(args.get("reason", "requested_via_mcp")).strip() or "requested_via_mcp"
        metadata = args.get("metadata")
        if metadata is None or not isinstance(metadata, dict):
            metadata = {}

        engine = RemediationEngine()
        action_id = engine.schedule_restart_sync(
            component=component,
            reason=reason,
            metadata=metadata,
        )
        return {
            "ok": True,
            "action_id": action_id,
            "action": engine.get_action_status(action_id),
        }

    def _tool_agent_remediation_retry(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Retry a remediation action by ID."""
        from navig.agent.remediation import RemediationEngine

        action_id = str(args.get("id", "")).strip()
        if not action_id:
            return {"error": "id is required"}

        reset_attempts = bool(args.get("reset_attempts", True))
        engine = RemediationEngine()
        ok = engine.retry_action(action_id, reset_attempts=reset_attempts)
        if not ok:
            return {"ok": False, "error": f"action not found: {action_id}", "action_id": action_id}

        return {
            "ok": True,
            "action_id": action_id,
            "action": engine.get_action_status(action_id),
        }

    def _tool_agent_service_install(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Install NAVIG agent as service."""
        try:
            from navig.agent.service import ServiceInstaller
            installer = ServiceInstaller()
            start_now = bool(args.get("start_now", True))
            success, message = installer.install(start_now=start_now)
            return {
                "ok": bool(success),
                "platform": installer.system,
                "message": message,
            }
        except Exception as e:
            return {"error": f"service install failed: {e}"}

    def _tool_agent_service_uninstall(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Uninstall NAVIG agent service."""
        try:
            from navig.agent.service import ServiceInstaller
            installer = ServiceInstaller()
            success, message = installer.uninstall()
            return {
                "ok": bool(success),
                "platform": installer.system,
                "message": message,
            }
        except Exception as e:
            return {"error": f"service uninstall failed: {e}"}
    
    # =========================================================================
    # Resource Implementations
    # =========================================================================
    
    def _read_resource(self, uri: str) -> str:
        """Read resource content."""
        if uri == "navig://config/hosts":
            return json.dumps(self._tool_list_hosts({}), indent=2)
        elif uri == "navig://config/apps":
            return json.dumps(self._tool_list_apps({}), indent=2)
        elif uri == "navig://wiki":
            pages = self._tool_list_wiki_pages({})
            if isinstance(pages, dict) and "error" in pages:
                return f"# Wiki\n\n{pages['error']}"
            return "# Wiki Index\n\n" + "\n".join(
                f"- [{p['title']}]({p['path']})" for p in pages
            )
        elif uri == "navig://context":
            return json.dumps(self._tool_get_context({}), indent=2)
        elif uri in ("navig://agent/status", "agent://status"):
            return json.dumps(self._tool_agent_status_get({}), indent=2)
        elif uri in ("navig://agent/goals", "agent://goals"):
            return json.dumps(self._tool_agent_goal_list({"limit": 100}), indent=2)
        elif uri in ("navig://agent/remediation", "agent://remediation"):
            return json.dumps(self._tool_agent_remediation_list({"limit": 100}), indent=2)
        elif uri in ("navig://agent/learning", "agent://learning/patterns"):
            report_path = Path.home() / ".navig" / "workspace" / "error-patterns.json"
            if report_path.exists():
                return report_path.read_text(encoding="utf-8", errors="replace")
            return json.dumps(self._tool_agent_learning_run({"days": 7, "export": False}), indent=2)
        elif uri in ("navig://agent/service", "agent://service"):
            return json.dumps(self._tool_agent_service_status({}), indent=2)
        else:
            raise ValueError(f"Unknown resource: {uri}")
    
    # =========================================================================
    # Server Loop
    # =========================================================================
    
    def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a single JSON-RPC message."""
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")
        
        if method not in self._handlers:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
            return None
        
        try:
            result = self._handlers[method](params)
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result
                }
            return None
        except Exception as e:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }
            return None
    
    def run_stdio(self):
        """Run MCP server in stdio mode."""
        self._running = True
        
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                
                message = json.loads(line)
                response = self.handle_message(message)
                
                if response:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    
            except json.JSONDecodeError as e:
                sys.stderr.write(f"JSON decode error: {e}\n")
            except Exception as e:
                sys.stderr.write(f"Error: {e}\n")
    
    def stop(self):
        """Stop the server."""
        self._running = False


def start_mcp_server(mode: str = "stdio", port: int = 3001, token: Optional[str] = None):
    """Start the NAVIG MCP server.
    
    Args:
        mode: Server mode - 'stdio' for stdin/stdout, 'websocket' for WS
        port: Port for WebSocket mode (default 3001)
        token: Optional auth token; auto-generated if None in websocket mode
    """
    handler = MCPProtocolHandler()
    
    if mode == "stdio":
        handler.run_stdio()
    elif mode == "websocket":
        _run_websocket_server(handler, port, token)
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'stdio' or 'websocket'.")


def _run_websocket_server(handler: MCPProtocolHandler, port: int, token: Optional[str] = None):
    """Start a WebSocket MCP server with optional token auth.
    
    The server speaks JSON-RPC 2.0 over WebSocket frames.
    Each frame is one JSON-RPC request/response.
    Notifications (no 'id') are fire-and-forget.
    
    Auth: if token is set, clients must send it as the first message
    or via the ``Authorization`` header (``Bearer <token>``).
    """
    try:
        import websockets
        import websockets.asyncio.server
    except ImportError:
        ch.error("WebSocket mode requires the 'websockets' package.")
        ch.info("Install with:  pip install websockets")
        raise SystemExit(1)

    # Generate a session token if not provided
    session_token = token or secrets.token_urlsafe(32)
    authenticated_clients: set = set()
    authenticated_websockets: set = set()

    # --- Event Bridge integration (push pipeline) ---
    from navig.event_bridge import EventBridge, SubscriptionFilter
    event_bridge = EventBridge(debounce_seconds=1.0)

    notification_sources = {
        "agent.status.changed": "navig://agent/status",
        "agent.goal.changed": "navig://agent/goals",
        "agent.remediation.changed": "navig://agent/remediation",
    }
    debounce_seconds = 1.5
    poll_interval_seconds = 1.0
    max_notification_bytes = 131072
    notification_state: Dict[str, Dict[str, Any]] = {
        topic: {
            "last_seen_digest": None,
            "last_seen_at": None,
            "last_payload": None,
            "last_emitted_digest": None,
        }
        for topic in notification_sources
    }

    async def _broadcast_notification(payload: str) -> None:
        """Broadcast notification to authenticated clients with drop-safe sends."""
        if not authenticated_websockets:
            return

        async def _send_safe(websocket_obj) -> bool:
            try:
                await asyncio.wait_for(websocket_obj.send(payload), timeout=0.25)
                return True
            except Exception:
                return False

        sockets = list(authenticated_websockets)
        results = await asyncio.gather(
            *[_send_safe(ws) for ws in sockets],
            return_exceptions=True,
        )
        for ws, result in zip(sockets, results):
            if result is not True:
                authenticated_websockets.discard(ws)

    async def _notification_loop() -> None:
        """Poll runtime resources and emit debounced MCP notifications on change."""
        import hashlib

        while True:
            await asyncio.sleep(poll_interval_seconds)
            if not authenticated_websockets:
                continue

            now = datetime.now()
            for topic, uri in notification_sources.items():
                try:
                    resource_text = handler._read_resource(uri)
                except Exception:
                    continue

                digest = hashlib.sha256(
                    resource_text.encode("utf-8", errors="replace")
                ).hexdigest()

                state = notification_state[topic]
                if digest != state["last_seen_digest"]:
                    state["last_seen_digest"] = digest
                    state["last_seen_at"] = now

                    params: Dict[str, Any] = {
                        "uri": uri,
                        "changed_at": now.isoformat(),
                    }
                    try:
                        params["data"] = json.loads(resource_text)
                    except json.JSONDecodeError:
                        params["text"] = resource_text[:4000]

                    payload_obj = {
                        "jsonrpc": "2.0",
                        "method": topic,
                        "params": params,
                    }
                    payload = json.dumps(payload_obj, default=str)
                    if len(payload.encode("utf-8", errors="replace")) > max_notification_bytes:
                        payload = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": topic,
                                "params": {
                                    "uri": uri,
                                    "changed_at": now.isoformat(),
                                    "truncated": True,
                                },
                            },
                            default=str,
                        )
                    state["last_payload"] = payload

                # Debounce: only emit after value has stayed unchanged for a short window.
                if (
                    state["last_seen_digest"] is not None
                    and state["last_seen_digest"] != state["last_emitted_digest"]
                    and state["last_seen_at"] is not None
                    and (now - state["last_seen_at"]).total_seconds() >= debounce_seconds
                    and state["last_payload"] is not None
                ):
                    await _broadcast_notification(state["last_payload"])
                    # Also push through EventBridge for filtered delivery
                    from navig.event_bridge import Severity
                    await event_bridge.push_direct(
                        topic=topic,
                        source="mcp.resource_poll",
                        data={"uri": uri, "changed_at": now.isoformat()},
                        severity=Severity.INFO,
                    )
                    state["last_emitted_digest"] = state["last_seen_digest"]

    async def _handle_client(websocket):
        client_id = id(websocket)
        
        # Check auth header first
        auth_header = websocket.request.headers.get("Authorization", "")
        if auth_header == f"Bearer {session_token}":
            authenticated_clients.add(client_id)
            authenticated_websockets.add(websocket)
            event_bridge.register_client(websocket)

        try:
            async for raw in websocket:
                # If not yet authenticated, expect token as first message
                if session_token and client_id not in authenticated_clients:
                    if raw.strip() == session_token:
                        authenticated_clients.add(client_id)
                        authenticated_websockets.add(websocket)
                        event_bridge.register_client(websocket)
                        ack = json.dumps({"jsonrpc": "2.0", "result": {"authenticated": True}, "id": 0})
                        await websocket.send(ack)
                        continue
                    else:
                        err = json.dumps({
                            "jsonrpc": "2.0",
                            "error": {"code": -32000, "message": "Authentication required"},
                            "id": None,
                        })
                        await websocket.send(err)
                        await websocket.close(4001, "Authentication failed")
                        return

                # Normal JSON-RPC handling
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    err = json.dumps({
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None,
                    })
                    await websocket.send(err)
                    continue

                response = handler.handle_message(message)

                # --- EventBridge: handle subscription requests ---
                method = message.get("method", "")
                msg_id = message.get("id")

                if method == "navig.subscribe" and msg_id is not None:
                    params = message.get("params", {})
                    filt = SubscriptionFilter.from_dict(params)
                    event_bridge.update_client_filter(websocket, filt)
                    sub_resp = {
                        "jsonrpc": "2.0",
                        "result": {"subscribed": True, "filter": filt.to_dict()},
                        "id": msg_id,
                    }
                    await websocket.send(json.dumps(sub_resp, default=str))
                    continue

                if method == "navig.bridge.stats" and msg_id is not None:
                    stats_resp = {
                        "jsonrpc": "2.0",
                        "result": event_bridge.get_stats(),
                        "id": msg_id,
                    }
                    await websocket.send(json.dumps(stats_resp, default=str))
                    continue

                if response:
                    await websocket.send(json.dumps(response, default=str))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            authenticated_clients.discard(client_id)
            authenticated_websockets.discard(websocket)
            event_bridge.unregister_client(websocket)

    async def _serve():
        import contextlib

        ch.success(f"NAVIG MCP WebSocket server listening on ws://localhost:{port}")
        ch.info(f"Session token: {session_token}")
        ch.dim("Clients must authenticate with the token before sending requests.")
        ch.dim("Press Ctrl+C to stop.")

        notifier_task = asyncio.create_task(_notification_loop())
        async with websockets.asyncio.server.serve(
            _handle_client,
            "0.0.0.0",
            port,
            ping_interval=30,
            ping_timeout=10,
        ):
            try:
                await asyncio.Future()  # run forever
            finally:
                notifier_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await notifier_task

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        ch.info("MCP WebSocket server stopped.")


def generate_vscode_mcp_config() -> Dict[str, Any]:
    """Generate VS Code MCP configuration for Copilot integration."""
    import sys
    
    python_path = sys.executable
    
    return {
        "mcpServers": {
            "navig": {
                "command": python_path,
                "args": ["-m", "navig.mcp_server"],
                "env": {}
            }
        }
    }


def generate_claude_mcp_config() -> Dict[str, Any]:
    """Generate Claude Desktop MCP configuration."""
    import sys
    
    python_path = sys.executable
    
    return {
        "mcpServers": {
            "navig": {
                "command": python_path,
                "args": ["-m", "navig.mcp_server"],
                "env": {}
            }
        }
    }


# Allow running as module: python -m navig.mcp_server
# Usage:
#   python -m navig.mcp_server                  # stdio mode
#   python -m navig.mcp_server --websocket      # WebSocket on port 3001
#   python -m navig.mcp_server --port 3001      # Same as above
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NAVIG MCP Server")
    parser.add_argument("--websocket", action="store_true", help="Run in WebSocket mode")
    parser.add_argument("--port", type=int, default=3001, help="WebSocket port (default 3001)")
    parser.add_argument("--token", type=str, default=None, help="Auth token (auto-generated if omitted)")
    args = parser.parse_args()

    mode = "websocket" if args.websocket or args.port != 3001 else "stdio"
    start_mcp_server(mode=mode, port=args.port, token=args.token)
