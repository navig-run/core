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
    filter_pattern = args.get("filter", "").lower()
    host_names = server._config.list_hosts()

    result = []
    for name in host_names:
        if filter_pattern and filter_pattern not in name.lower():
            continue
        try:
            config = server._config.load_host_config(name)
        except Exception:
            config = {}
        result.append(
            {
                "name": name,
                "host": config.get("host"),
                "user": config.get("user"),
                "port": config.get("port", 22),
                "key": config.get("ssh_key") or config.get("key"),
                "apps": list(config.get("apps", {}).keys()),
            }
        )

    return result


def _tool_list_apps(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """List all configured apps (individual files + host-embedded)."""
    host_filter = args.get("host")
    result = []
    seen: set[str] = set()

    # Apps from individual app files (new format)
    for app_name in server._config.list_apps_from_files():
        try:
            config = server._config.load_app_from_file(app_name) or {}
        except Exception:
            config = {}
        app_host = config.get("host")
        if host_filter and app_host != host_filter:
            continue
        result.append(
            {
                "name": app_name,
                "type": config.get("type"),
                "host": app_host,
                "path": config.get("path"),
                "url": config.get("url"),
            }
        )
        seen.add(app_name)

    # Apps embedded in host configs (legacy format)
    for host_name in server._config.list_hosts():
        if host_filter and host_name != host_filter:
            continue
        try:
            host_cfg = server._config.load_host_config(host_name)
        except Exception:
            continue
        for app_name, app_config in host_cfg.get("apps", {}).items():
            if app_name in seen:
                continue
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
    name = args.get("name", "").strip()
    if not name:
        return {"error": "name is required"}
    if not server._config.host_exists(name):
        available = server._config.list_hosts()
        return {"error": f"Host not found: {name!r}", "available": available}
    try:
        config = server._config.load_host_config(name)
        # Strip credentials from response
        safe = {k: v for k, v in config.items() if k not in ("ssh_password", "root_password")}
        return {"name": name, **safe}
    except Exception as exc:
        return {"error": str(exc)}


def _tool_app_info(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed app information."""
    name = args.get("name", "").strip()
    if not name:
        return {"error": "name is required"}

    # Try individual app file first
    config = server._config.load_app_from_file(name)
    if config is not None:
        safe = {k: v for k, v in config.items() if "password" not in k.lower()}
        return {"name": name, **safe}

    # Search in host-embedded apps
    for host_name in server._config.list_hosts():
        try:
            host_cfg = server._config.load_host_config(host_name)
        except Exception:
            continue
        if name in host_cfg.get("apps", {}):
            app_cfg = host_cfg["apps"][name]
            return {"name": name, "host": host_name, **app_cfg}

    return {"error": f"App not found: {name!r}"}
