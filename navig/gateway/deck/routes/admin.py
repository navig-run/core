"""Admin-section handlers for the Deck API.

Exposes read-only endpoints that feed the Onyx-ported admin pages in
navig-deck.  All data is sourced from:
  - navig.routing.router.WELL_KNOWN_* registries  (providers)
  - navig.mcp_manager.MCPManager                  (MCP servers)
  - navig.config.get_config_manager()             (admin settings)

Routes registered in navig.gateway.deck.__init__.register_deck_routes():
  GET /api/deck/admin/llm-providers
  GET /api/deck/admin/search-providers
  GET /api/deck/admin/image-providers
  GET /api/deck/admin/voice-providers
  GET /api/deck/admin/mcp-servers
  GET /api/deck/admin/settings
  GET /api/deck/admin/agents
  GET /api/deck/admin/connectors
  GET /api/deck/admin/document-sets
  GET /api/deck/admin/service-accounts
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from aiohttp import web
except ImportError:
    web = None

if TYPE_CHECKING:
    pass  # aiohttp types only used at runtime

logger = logging.getLogger(__name__)

# ── Provider icon map (cosmetic — not stored in WELL_KNOWN_*) ─────────────────

_LLM_ICONS: dict[str, str] = {
    "openai": "🟢",
    "anthropic": "🟠",
    "vertex_ai": "🔵",
    "bedrock": "🟡",
    "azure": "🔷",
    "litellm_proxy": "🔁",
    "ollama": "🦙",
    "openrouter": "🔀",
    "lm_studio": "🖥️",
    "bifrost": "🌉",
    "openai_compat": "🔌",
    "custom": "⚙️",
}

_SEARCH_ICONS: dict[str, str] = {
    "exa": "✖️",
    "serper": "🔵",
    "brave": "🦁",
    "google_pse": "🔍",
    "searxng": "🔎",
}

_CRAWLER_ICONS: dict[str, str] = {
    "navig_crawler": "⚙️",
    "firecrawl": "🔥",
    "exa": "✖️",
}

_IMAGE_ICONS: dict[str, str] = {
    "openai": "🟢",
    "azure": "🔷",
    "vertex_ai": "🔵",
}

_VOICE_ICONS: dict[str, str] = {
    "openai": "🟢",
    "azure": "🔷",
    "elevenlabs": "⏸️",
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_llm_providers() -> list[dict]:
    try:
        from navig.routing.router import WELL_KNOWN_LLM_PROVIDERS

        result = []
        for key, meta in WELL_KNOWN_LLM_PROVIDERS.items():
            result.append(
                {
                    "key": key,
                    "icon": _LLM_ICONS.get(key, "🤖"),
                    "label": meta.get("name", key),
                    "subtitle": meta.get("company", ""),
                    "type": meta.get("type", "cloud"),
                    "action": "Set Up" if key == "custom" else "Connect",
                }
            )
        return result
    except Exception as exc:
        logger.warning("admin: failed to load LLM providers: %s", exc)
        return []


def _load_search_providers() -> dict:
    try:
        from navig.routing.router import WELL_KNOWN_CRAWLERS, WELL_KNOWN_SEARCH_PROVIDERS

        search = []
        for key, meta in WELL_KNOWN_SEARCH_PROVIDERS.items():
            search.append(
                {
                    "key": key,
                    "icon": _SEARCH_ICONS.get(key, "🔍"),
                    "label": meta.get("name", key),
                    "subtitle": meta.get("subtitle", ""),
                    "requires_api_key": meta.get("requires_api_key", "true") == "true",
                    "noToggle": False,
                }
            )

        crawlers = []
        for key, meta in WELL_KNOWN_CRAWLERS.items():
            built_in = meta.get("built_in", "false") == "true"
            crawlers.append(
                {
                    "key": key,
                    "icon": _CRAWLER_ICONS.get(key, "🕷️"),
                    "label": meta.get("name", key),
                    "subtitle": "",
                    "requires_api_key": meta.get("requires_api_key", "true") == "true",
                    "built_in": built_in,
                    "noToggle": built_in,
                }
            )

        return {"search": search, "crawlers": crawlers}
    except Exception as exc:
        logger.warning("admin: failed to load search providers: %s", exc)
        return {"search": [], "crawlers": []}


def _load_image_providers() -> list[dict]:
    try:
        from navig.routing.router import WELL_KNOWN_IMAGE_PROVIDERS

        result = []
        vendor_names = {
            "openai": "OpenAI",
            "azure": "Azure OpenAI",
            "vertex_ai": "Google Cloud Vertex AI",
        }
        for vendor_key, models in WELL_KNOWN_IMAGE_PROVIDERS.items():
            result.append(
                {
                    "vendor_key": vendor_key,
                    "vendor_name": vendor_names.get(vendor_key, vendor_key),
                    "icon": _IMAGE_ICONS.get(vendor_key, "🖼️"),
                    "models": [
                        {
                            "key": m.get("id", ""),
                            "label": m.get("title", ""),
                            "description": m.get("description", ""),
                        }
                        for m in models
                    ],
                }
            )
        return result
    except Exception as exc:
        logger.warning("admin: failed to load image providers: %s", exc)
        return []


def _load_voice_providers() -> dict:
    try:
        from navig.routing.router import WELL_KNOWN_VOICE_PROVIDERS

        def _enrich(items: list[dict]) -> list[dict]:
            out = []
            for item in items:
                provider = item.get("provider", "")
                out.append(
                    {
                        "key": item.get("id", ""),
                        "icon": _VOICE_ICONS.get(provider, "🎙️"),
                        "label": item.get("label", ""),
                        "subtitle": item.get("subtitle", ""),
                        "provider": provider,
                    }
                )
            return out

        return {
            "stt": _enrich(WELL_KNOWN_VOICE_PROVIDERS.get("stt", [])),  # type: ignore[arg-type]
            "tts": _enrich(WELL_KNOWN_VOICE_PROVIDERS.get("tts", [])),  # type: ignore[arg-type]
        }
    except Exception as exc:
        logger.warning("admin: failed to load voice providers: %s", exc)
        return {"stt": [], "tts": []}


def _load_mcp_servers() -> list[dict]:
    try:
        from navig.mcp_manager import MCPManager

        mgr = MCPManager()
        servers = mgr.list()
        return [
            {
                "key": s.name,
                "label": s.name,
                "subtitle": s.description if hasattr(s, "description") else "",
                "enabled": s.is_enabled(),
                "running": s.is_running(),
                "command": s.command if hasattr(s, "command") else "",
            }
            for s in servers
        ]
    except Exception as exc:
        logger.warning("admin: failed to load MCP servers: %s", exc)
        return []


def _load_admin_settings() -> dict:
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = cm.get_config() if hasattr(cm, "get_config") else {}
        ai_cfg = cfg.get("ai", {}) if isinstance(cfg, dict) else {}
        code_cfg = cfg.get("code_interpreter", {}) if isinstance(cfg, dict) else {}
        chat_cfg = cfg.get("chat", {}) if isinstance(cfg, dict) else {}
        index_cfg = cfg.get("index", {}) if isinstance(cfg, dict) else {}
        return {
            "code_interpreter": {
                "python_enabled": bool(code_cfg.get("python_enabled", True)),
                "node_enabled": bool(code_cfg.get("node_enabled", False)),
                "network_enabled": bool(code_cfg.get("network_enabled", False)),
                "timeout_seconds": int(code_cfg.get("timeout_seconds", 30)),
                "memory_mb": int(code_cfg.get("memory_mb", 512)),
            },
            "chat_preferences": {
                "verbosity": str(chat_cfg.get("verbosity", "standard")),
                "markdown": bool(chat_cfg.get("markdown", True)),
                "streaming": bool(chat_cfg.get("streaming", True)),
                "citations": str(chat_cfg.get("citations", "inline")),
                "context_messages": int(chat_cfg.get("context_messages", 20)),
            },
            "index": {
                "embedding_model": str(
                    index_cfg.get("embedding_model", "nomic-ai/nomic-embed-text-v1.5")
                ),
                "chunk_size": int(index_cfg.get("chunk_size", 512)),
                "chunk_overlap": int(index_cfg.get("chunk_overlap", 50)),
                "hybrid_search": bool(index_cfg.get("hybrid_search", True)),
                "reranking": bool(index_cfg.get("reranking", False)),
            },
        }
    except Exception as exc:
        logger.warning("admin: failed to load admin settings: %s", exc)
        return {
            "code_interpreter": {
                "python_enabled": True,
                "node_enabled": False,
                "network_enabled": False,
                "timeout_seconds": 30,
                "memory_mb": 512,
            },
            "chat_preferences": {
                "verbosity": "standard",
                "markdown": True,
                "streaming": True,
                "citations": "inline",
                "context_messages": 20,
            },
            "index": {
                "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "hybrid_search": True,
                "reranking": False,
            },
        }


# ── Route handlers ─────────────────────────────────────────────────────────────


async def handle_deck_admin_llm_providers(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/llm-providers"""
    return web.json_response({"providers": _load_llm_providers()})


async def handle_deck_admin_search_providers(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/search-providers"""
    return web.json_response(_load_search_providers())


async def handle_deck_admin_image_providers(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/image-providers"""
    return web.json_response({"groups": _load_image_providers()})


async def handle_deck_admin_voice_providers(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/voice-providers"""
    return web.json_response(_load_voice_providers())


async def handle_deck_admin_mcp_servers(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/mcp-servers"""
    return web.json_response({"servers": _load_mcp_servers()})


async def handle_deck_admin_settings(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/settings"""
    return web.json_response(_load_admin_settings())


# ── Agents / connectors / document-sets / service-accounts ────────────────────

_BUILTIN_AGENTS: list[dict] = [
    {
        "key": "assistant",
        "icon": "🤖",
        "label": "navig-assistant",
        "subtitle": "General-purpose conversational agent",
        "builtin": True,
        "enabled": True,
    },
    {
        "key": "coder",
        "icon": "💻",
        "label": "navig-coder",
        "subtitle": "Code generation, review, and debugging",
        "builtin": True,
        "enabled": True,
    },
    {
        "key": "researcher",
        "icon": "🔍",
        "label": "navig-researcher",
        "subtitle": "Web-search-augmented research and summarization",
        "builtin": True,
        "enabled": True,
    },
    {
        "key": "planner",
        "icon": "📋",
        "label": "navig-planner",
        "subtitle": "Multi-step task decomposition and orchestration",
        "builtin": True,
        "enabled": False,
    },
]


def _load_agents() -> dict:
    """Return built-in agents plus any custom agents from config."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = cm.get_config() if hasattr(cm, "get_config") else {}
        custom_raw = cfg.get("agents", {}).get("custom", []) if isinstance(cfg, dict) else []
        enabled_keys: set[str] = set(
            cfg.get("agents", {}).get("enabled", []) if isinstance(cfg, dict) else []
        )

        builtin = [
            {**a, "enabled": a["key"] in enabled_keys if enabled_keys else a["enabled"]}
            for a in _BUILTIN_AGENTS
        ]
        custom = [
            {
                "key": c.get("key", c.get("name", "")),
                "icon": c.get("icon", "⚙️"),
                "label": c.get("name", c.get("key", "")),
                "subtitle": c.get("description", c.get("url", "")),
                "builtin": False,
                "enabled": bool(c.get("enabled", True)),
            }
            for c in (custom_raw if isinstance(custom_raw, list) else [])
        ]
        return {"builtin": builtin, "custom": custom}
    except Exception as exc:
        logger.warning("admin: failed to load agents: %s", exc)
        return {"builtin": list(_BUILTIN_AGENTS), "custom": []}


def _load_connectors() -> list[dict]:
    """Return active connectors from config (empty list if none configured)."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = cm.get_config() if hasattr(cm, "get_config") else {}
        raw = cfg.get("connectors", []) if isinstance(cfg, dict) else []
        if not isinstance(raw, list):
            return []
        return [
            {
                "key": c.get("key", c.get("name", "")),
                "icon": c.get("icon", "🔗"),
                "name": c.get("name", c.get("key", "")),
                "kind": c.get("kind", ""),
                "status": c.get("status", "disconnected"),
                "last_sync": c.get("last_sync", ""),
            }
            for c in raw
        ]
    except Exception as exc:
        logger.warning("admin: failed to load connectors: %s", exc)
        return []


def _load_document_sets() -> list[dict]:
    """Return document sets from config (empty list if none configured)."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = cm.get_config() if hasattr(cm, "get_config") else {}
        raw = cfg.get("document_sets", []) if isinstance(cfg, dict) else []
        if not isinstance(raw, list):
            return []
        return [
            {
                "name": ds.get("name", ""),
                "icon": ds.get("icon", "📄"),
                "docs": int(ds.get("docs", 0)),
                "assignees": ds.get("assignees", ""),
                "color": ds.get("color", ""),
            }
            for ds in raw
        ]
    except Exception as exc:
        logger.warning("admin: failed to load document sets: %s", exc)
        return []


def _load_service_accounts() -> list[dict]:
    """Return service-account tokens from config/vault (empty list if none)."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = cm.get_config() if hasattr(cm, "get_config") else {}
        raw = cfg.get("service_accounts", []) if isinstance(cfg, dict) else []
        if not isinstance(raw, list):
            return []
        return [
            {
                "name": t.get("name", ""),
                "scopes": t.get("scopes", "read"),
                "created": t.get("created", ""),
                "last_used": t.get("last_used", ""),
            }
            for t in raw
        ]
    except Exception as exc:
        logger.warning("admin: failed to load service accounts: %s", exc)
        return []


async def handle_deck_admin_agents(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/agents"""
    return web.json_response(_load_agents())


async def handle_deck_admin_connectors(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/connectors"""
    return web.json_response({"connectors": _load_connectors()})


async def handle_deck_admin_document_sets(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/document-sets"""
    return web.json_response({"sets": _load_document_sets()})


async def handle_deck_admin_service_accounts(request: "web.Request") -> "web.Response":
    """GET /api/deck/admin/service-accounts"""
    return web.json_response({"tokens": _load_service_accounts()})
