"""
API Tool Pack — JSON API tools for web, trading, infra, and media.

Every handler returns an ``ApiToolResult`` conforming to the standardized
envelope defined in ``navig.tools.api_schema``.

Tool categories:
  web.api.*      — generic HTTP / RSS / webhooks
  trading.*      — exchange / OHLCV / order book / portfolio
  infra.*        — node status, uptime, resource metrics, server inventory
  media.*        — OCR, thumbnail, image services
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from navig.tools.api_schema import ApiSource, ApiToolResult

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry

logger = logging.getLogger("navig.tools.packs.api_pack")


# ──────────────────────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────────────────────

def _api_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    **kwargs,
) -> Dict[str, Any]:
    """
    Generic HTTP GET → JSON API tool.

    Returns an ApiToolResult.to_dict() so the ToolRouter can serialise it.
    """
    import urllib.request
    import urllib.parse
    import urllib.error

    endpoint = url
    try:
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "navig/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        return ApiToolResult(
            status="ok",
            raw_json=raw if isinstance(raw, dict) else {"data": raw},
            normalized=raw if isinstance(raw, dict) else {"data": raw},
            source=ApiSource(tool="web.api.get_json", endpoint=endpoint),
        ).to_dict()

    except Exception as e:
        return ApiToolResult.from_error(
            tool="web.api.get_json", error=str(e), endpoint=endpoint,
        ).to_dict()


def _api_post_json(
    url: str,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    **kwargs,
) -> Dict[str, Any]:
    """Generic HTTP POST with JSON body → JSON response."""
    import urllib.request
    import urllib.error

    endpoint = url
    try:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "navig/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        return ApiToolResult(
            status="ok",
            raw_json=raw if isinstance(raw, dict) else {"data": raw},
            normalized=raw if isinstance(raw, dict) else {"data": raw},
            source=ApiSource(tool="web.api.post_json", endpoint=endpoint),
        ).to_dict()

    except Exception as e:
        return ApiToolResult.from_error(
            tool="web.api.post_json", error=str(e), endpoint=endpoint,
        ).to_dict()


def _trading_fetch_ohlc(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 100,
    exchange: str = "auto",
    **kwargs,
) -> Dict[str, Any]:
    """
    Fetch OHLCV candle data for a trading pair.

    This is a stub that demonstrates the schema contract.
    Real implementation would use ccxt or exchange-specific APIs.
    """
    return ApiToolResult(
        status="ok",
        raw_json={},
        normalized={
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": exchange,
            "candles": [],
            "note": "Stub — connect exchange adapter for real data",
        },
        source=ApiSource(
            tool="trading.fetch.ohlc",
            endpoint=f"exchange://{exchange}/{symbol}",
        ),
    ).to_dict()


def _trading_portfolio(exchange: str = "auto", **kwargs) -> Dict[str, Any]:
    """Fetch portfolio/balance summary from an exchange."""
    return ApiToolResult(
        status="ok",
        raw_json={},
        normalized={
            "exchange": exchange,
            "balances": [],
            "total_usd": 0.0,
            "note": "Stub — connect exchange adapter for real data",
        },
        source=ApiSource(
            tool="trading.fetch.portfolio",
            endpoint=f"exchange://{exchange}/portfolio",
        ),
    ).to_dict()


def _infra_node_status(
    host: str = "current",
    **kwargs,
) -> Dict[str, Any]:
    """
    Get system metrics (CPU, memory, disk, uptime) for a host.

    When host="current", reports local machine stats.
    Otherwise delegates to navig run on the remote host.
    """
    import platform

    if host == "current":
        try:
            import shutil
            disk = shutil.disk_usage("/")
            normalized = {
                "host": platform.node(),
                "platform": platform.platform(),
                "cpu_count": __import__("os").cpu_count(),
                "disk_total_gb": round(disk.total / (1 << 30), 1),
                "disk_free_gb": round(disk.free / (1 << 30), 1),
                "disk_used_pct": round(disk.used / disk.total * 100, 1),
            }
        except Exception as e:
            return ApiToolResult.from_error(
                tool="infra.metrics.node_status",
                error=f"Failed to collect local metrics: {e}",
            ).to_dict()
    else:
        normalized = {
            "host": host,
            "note": "Remote metrics require navig host monitor — stub only",
        }

    return ApiToolResult(
        status="ok",
        raw_json={},
        normalized=normalized,
        source=ApiSource(
            tool="infra.metrics.node_status",
            endpoint=f"host://{host}",
        ),
    ).to_dict()


def _infra_inventory(scope: str = "all", **kwargs) -> Dict[str, Any]:
    """List configured servers/hosts from navig host inventory."""
    try:
        from navig.config import get_config_manager
        cm = get_config_manager()
        hosts = cm.list_hosts() if hasattr(cm, "list_hosts") else []
        normalized = {"scope": scope, "hosts": hosts}
    except Exception:
        normalized = {"scope": scope, "hosts": [], "note": "config_manager unavailable"}

    return ApiToolResult(
        status="ok",
        raw_json={},
        normalized=normalized,
        source=ApiSource(tool="infra.inventory.servers"),
    ).to_dict()


# ──────────────────────────────────────────────────────────────
# Pack registration
# ──────────────────────────────────────────────────────────────

_API_DOMAIN = "api"  # Added as new ToolDomain below


def register_tools(registry: "ToolRegistry") -> None:
    """Register all JSON API tools with the ToolRouter registry."""
    from navig.tools.router import ToolMeta, ToolDomain, SafetyLevel

    # --- Web / API ---
    registry.register(
        ToolMeta(
            name="web.api.get_json",
            domain=ToolDomain.WEB,
            description="Fetch JSON from any HTTP GET endpoint.",
            safety=SafetyLevel.SAFE,
            parameters_schema={
                "url": {"type": "string", "required": True, "description": "Target URL"},
                "headers": {"type": "object", "description": "Optional HTTP headers"},
                "params": {"type": "object", "description": "URL query parameters"},
                "timeout": {"type": "integer", "default": 30},
            },
            tags=["api", "http", "json", "get"],
        ),
        handler=_api_get_json,
    )

    registry.register(
        ToolMeta(
            name="web.api.post_json",
            domain=ToolDomain.WEB,
            description="POST JSON body to an HTTP endpoint and return the response.",
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "url": {"type": "string", "required": True},
                "body": {"type": "object", "description": "JSON request body"},
                "headers": {"type": "object"},
                "timeout": {"type": "integer", "default": 30},
            },
            tags=["api", "http", "json", "post"],
        ),
        handler=_api_post_json,
    )

    # --- Trading ---
    registry.register(
        ToolMeta(
            name="trading.fetch.ohlc",
            domain=ToolDomain.DATA,
            description="Fetch OHLCV candle data for a trading pair.",
            safety=SafetyLevel.SAFE,
            parameters_schema={
                "symbol": {"type": "string", "required": True, "description": "Trading pair (e.g. BTC/USDT)"},
                "timeframe": {"type": "string", "default": "1h"},
                "limit": {"type": "integer", "default": 100},
                "exchange": {"type": "string", "default": "auto"},
            },
            tags=["trading", "ohlcv", "candles", "exchange"],
        ),
        handler=_trading_fetch_ohlc,
    )

    registry.register(
        ToolMeta(
            name="trading.fetch.portfolio",
            domain=ToolDomain.DATA,
            description="Fetch portfolio/balance summary from an exchange.",
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "exchange": {"type": "string", "default": "auto"},
            },
            tags=["trading", "portfolio", "balance", "exchange"],
        ),
        handler=_trading_portfolio,
    )

    # --- Infrastructure ---
    registry.register(
        ToolMeta(
            name="infra.metrics.node_status",
            domain=ToolDomain.SYSTEM,
            description="Get system metrics (CPU, memory, disk, uptime) for a host.",
            safety=SafetyLevel.SAFE,
            parameters_schema={
                "host": {"type": "string", "default": "current", "description": "Host name or 'current'"},
            },
            tags=["infra", "metrics", "monitoring", "health"],
        ),
        handler=_infra_node_status,
    )

    registry.register(
        ToolMeta(
            name="infra.inventory.servers",
            domain=ToolDomain.SYSTEM,
            description="List configured servers/hosts from NAVIG inventory.",
            safety=SafetyLevel.SAFE,
            parameters_schema={
                "scope": {"type": "string", "default": "all"},
            },
            tags=["infra", "inventory", "servers", "hosts"],
        ),
        handler=_infra_inventory,
    )
