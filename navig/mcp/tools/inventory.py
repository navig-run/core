from typing import Any


def register(server: Any) -> None:
    """Register inventory tools (hosts and apps)."""
    server.tools.update(
        {
            "navig_list_hosts": {
                "name": "navig_list_hosts",
                "description": "List all configured SSH hosts in NAVIG. Returns host names, addresses, and connection details.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Optional filter pattern for host names",
                        }
                    },
                    "required": [],
                },
            },
            "navig_list_apps": {
                "name": "navig_list_apps",
                "description": "List all configured applications in NAVIG. Returns app names, types, and associated hosts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "host": {
                            "type": "string",
                            "description": "Filter apps by host name",
                        }
                    },
                    "required": [],
                },
            },
            "navig_host_info": {
                "name": "navig_host_info",
                "description": "Get detailed information about a specific SSH host including connection settings, paths, and apps.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Host name to get info for",
                        }
                    },
                    "required": ["name"],
                },
            },
            "navig_app_info": {
                "name": "navig_app_info",
                "description": "Get detailed information about a specific application including config, paths, and status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Application name to get info for",
                        }
                    },
                    "required": ["name"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "navig_list_hosts": _tool_list_hosts,
            "navig_list_apps": _tool_list_apps,
            "navig_host_info": _tool_host_info,
            "navig_app_info": _tool_app_info,
        }
    )


def _tool_list_hosts(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """List all configured hosts."""
    hosts = server._config.get_hosts()
    filter_pattern = args.get("filter", "").lower()

    result = []
    for name, config in hosts.items():
        if filter_pattern and filter_pattern not in name.lower():
            continue
        result.append(
            {
                "name": name,
                "host": config.get("host"),
                "user": config.get("user"),
                "port": config.get("port", 22),
                "key": config.get("key"),
                "apps": (list(config.get("apps", {}).keys()) if config.get("apps") else []),
            }
        )

    return result


def _tool_list_apps(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """List all configured apps."""
    host_filter = args.get("host")
    apps = server._config.get_apps()
    hosts = server._config.get_hosts()

    result = []

    # Apps from global apps config
    for name, config in apps.items():
        app_host = config.get("host")
        if host_filter and app_host != host_filter:
            continue
        result.append(
            {
                "name": name,
                "type": config.get("type"),
                "host": app_host,
                "path": config.get("path"),
                "url": config.get("url"),
            }
        )

    # Apps embedded in hosts
    for host_name, host_config in hosts.items():
        if host_filter and host_name != host_filter:
            continue
        for app_name, app_config in host_config.get("apps", {}).items():
            result.append(
                {
                    "name": app_name,
                    "type": app_config.get("type"),
                    "host": host_name,
                    "path": app_config.get("path"),
                    "url": app_config.get("url"),
                }
            )

    return result


def _tool_host_info(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed host information."""
    name = args.get("name")
    hosts = server._config.get_hosts()

    if name not in hosts:
        return {"error": f"Host not found: {name}"}

    return {"name": name, **hosts[name]}


def _tool_app_info(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed app information."""
    name = args.get("name")
    apps = server._config.get_apps()

    if name in apps:
        return {"name": name, **apps[name]}

    # Search in host apps
    hosts = server._config.get_hosts()
    for host_name, host_config in hosts.items():
        if name in host_config.get("apps", {}):
            return {"name": name, "host": host_name, **host_config["apps"][name]}

    return {"error": f"App not found: {name}"}
