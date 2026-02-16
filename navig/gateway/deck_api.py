"""
NAVIG Deck API — REST endpoints for the Telegram Mini App.

Serves:
- Static files for the Deck SPA from /deck/
- API endpoints at /api/deck/ for status, settings, and mode management.

Authentication:
- All /api/deck/* routes require valid Telegram WebApp initData (HMAC-SHA256).
- User must be in allowed_users list when require_auth is enabled.
- Deck lifecycle is tightly coupled to Telegram bot — no bot = no Deck.

Mounted onto the existing gateway aiohttp application.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, unquote

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Module-level config (set during route registration)
# ────────────────────────────────────────────────────────────────

_deck_config: Dict[str, Any] = {
    "bot_token": "",
    "allowed_users": set(),
    "require_auth": True,
    "dev_mode": False,
    "auth_max_age": 86400,
}


def configure_deck_auth(
    bot_token: str,
    allowed_users: List[int],
    require_auth: bool = True,
    dev_mode: bool = False,
    auth_max_age: int = 3600,
) -> None:
    """Set the module-level auth config for Deck API. Called once at startup."""
    _deck_config["bot_token"] = bot_token
    _deck_config["allowed_users"] = set(allowed_users) if allowed_users else set()
    _deck_config["require_auth"] = require_auth
    _deck_config["dev_mode"] = dev_mode
    _deck_config["auth_max_age"] = auth_max_age
    logger.info(
        "Deck auth configured: require_auth=%s, allowed_users=%d, dev_mode=%s",
        require_auth, len(_deck_config["allowed_users"]), dev_mode,
    )

# ────────────────────────────────────────────────────────────────
# Telegram WebApp Init Data Validation
# ────────────────────────────────────────────────────────────────

def validate_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> Optional[Dict[str, Any]]:
    """
    Validate Telegram WebApp initData using HMAC-SHA256.
    Returns parsed data if valid, None otherwise.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        # Build data-check-string (sorted key=value pairs, excluding hash)
        items = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            items.append(f"{key}={unquote(values[0])}")
        items.sort()
        data_check_string = "\n".join(items)

        # HMAC-SHA256 with WebAppData secret
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        # Check auth_date freshness
        auth_date = int(parsed.get("auth_date", [0])[0])
        if time.time() - auth_date > max_age:
            return None

        # Parse user JSON
        user_str = parsed.get("user", [None])[0]
        user = json.loads(unquote(user_str)) if user_str else None

        return {
            "user": user,
            "auth_date": auth_date,
            "valid": True,
        }
    except Exception as e:
        logger.debug("initData validation failed: %s", e)
        return None


def _get_user_id(request: "web.Request", bot_token: str = "") -> Optional[int]:
    """Extract user ID from request headers (initData or fallback)."""
    token = bot_token or _deck_config["bot_token"]
    max_age = _deck_config["auth_max_age"]

    # Try full initData validation
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and token:
        result = validate_init_data(init_data, token, max_age)
        if result and result.get("user"):
            return result["user"]["id"]

    # Fallback: direct user ID header (dev mode only)
    if _deck_config["dev_mode"]:
        user_header = request.headers.get("X-Telegram-User", "")
        if user_header.isdigit():
            return int(user_header)

    return None


# ────────────────────────────────────────────────────────────────
# Auth Middleware for Deck API routes
# ────────────────────────────────────────────────────────────────

@web.middleware
async def deck_auth_middleware(request: "web.Request", handler):
    """
    Authenticate all /api/deck/* requests via Telegram WebApp initData.
    
    - Validates HMAC-SHA256 signature against bot_token
    - Checks user_id against allowed_users (when require_auth is true)
    - Stores authenticated user_id in request['deck_user_id']
    - Static /deck/* routes are NOT protected (they serve the SPA shell)
    """
    path = request.path

    # Only enforce auth on API routes, not static SPA files
    if not path.startswith("/api/deck"):
        return await handler(request)

    # OPTIONS preflight passes through
    if request.method == "OPTIONS":
        return await handler(request)

    user_id = _get_user_id(request)

    if user_id is None:
        logger.warning("Deck API unauthorized: no valid user from %s %s", request.method, path)
        return web.json_response(
            {"error": "unauthorized", "detail": "Valid Telegram WebApp initData required"},
            status=401,
        )

    # Check against allowed_users when auth is required
    allowed = _deck_config["allowed_users"]
    require_auth = _deck_config["require_auth"]
    if require_auth and allowed and user_id not in allowed:
        logger.warning("Deck API forbidden: user %d not in allowed_users", user_id)
        return web.json_response(
            {"error": "forbidden", "detail": "User not authorized for Deck"},
            status=403,
        )

    # Store authenticated user for route handlers
    request["deck_user_id"] = user_id
    return await handler(request)


# ────────────────────────────────────────────────────────────────
# Route Handlers
# ────────────────────────────────────────────────────────────────

def _get_tracker():
    """Lazily import UserStateTracker."""
    try:
        from navig.agent.proactive.user_state import get_user_state_tracker
        return get_user_state_tracker()
    except Exception:
        return None


async def handle_deck_status(request: "web.Request") -> "web.Response":
    """GET /api/deck/status — Avatar state, tasks, errors, mode."""
    tracker = _get_tracker()

    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    tasks_done = 0
    tasks_pending = 0
    task_status = "unavailable"
    if gateway and getattr(gateway, "task_queue", None):
        try:
            q_stats = gateway.task_queue.get_stats()
            tasks_done = int(q_stats.get("status_counts", {}).get("completed", 0))
            tasks_pending = int(q_stats.get("status_counts", {}).get("queued", 0)) + int(
                q_stats.get("status_counts", {}).get("running", 0)
            )
            task_status = "available"
        except Exception:
            task_status = "error"

    if not tracker:
        return web.json_response(
            {"avatar_state": "calm", "state_label": "systems nominal",
             "tasks_done": tasks_done, "tasks_pending": tasks_pending, "errors": 0,
             "current_mode": "work", "uptime": "unknown", "task_queue_status": task_status},
        )

    mode = tracker.get_preference("chat_mode", "work")

    # Map mode → avatar state
    mode_to_state = {
        "work": "focused",
        "deep-focus": "focused",
        "planning": "focused",
        "creative": "calm",
        "relax": "calm",
        "sleep": "sleeping",
    }
    avatar_state = mode_to_state.get(mode, "calm")

    # State labels
    state_labels = {
        "calm": "systems nominal",
        "focused": "locked in",
        "busy": "processing multiple threads",
        "alert": "attention needed",
        "learning": "absorbing new data",
        "sleeping": "quiet mode active",
    }

    # Get uptime from daemon
    uptime = "unknown"
    try:
        import subprocess
        result = subprocess.run(
            ["systemctl", "show", "navig-daemon", "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            ts_str = result.stdout.strip().split("=", 1)[-1]
            if ts_str and ts_str != "n/a":
                from datetime import datetime
                started = datetime.strptime(ts_str.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                delta = datetime.now() - started
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                uptime = f"{hours}h {mins}m"
    except Exception:
        pass

    return web.json_response({
        "avatar_state": avatar_state,
        "state_label": state_labels.get(avatar_state, "nominal"),
        "tasks_done": tasks_done,
        "tasks_pending": tasks_pending,
        "errors": 0,
        "current_mode": mode,
        "uptime": uptime,
        "task_queue_status": task_status,
    })


async def handle_deck_settings_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/settings — Current user preferences."""
    tracker = _get_tracker()

    defaults = {
        "chat_mode": "work",
        "verbosity": "normal",
        "autonomy_level": "low-risk-auto",
        "quiet_hours_start": 23,
        "quiet_hours_end": 7,
        "quiet_hours_enabled": True,
        "notifications_enabled": True,
    }

    if tracker:
        for key in defaults:
            defaults[key] = tracker.get_preference(key, defaults[key])

    return web.json_response(defaults)


async def handle_deck_settings_post(request: "web.Request") -> "web.Response":
    """POST /api/deck/settings — Update user preferences."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    tracker = _get_tracker()
    if not tracker:
        return web.json_response({"error": "state tracker unavailable"}, status=500)

    allowed_keys = {
        "chat_mode", "verbosity", "autonomy_level",
        "quiet_hours_start", "quiet_hours_end",
        "quiet_hours_enabled", "notifications_enabled",
    }

    for key, value in body.items():
        if key in allowed_keys:
            tracker.set_preference(key, value)

    # Return updated settings
    result = {}
    for key in allowed_keys:
        result[key] = tracker.get_preference(key, None)
    return web.json_response(result)


async def handle_deck_mode(request: "web.Request") -> "web.Response":
    """POST /api/deck/mode — Change focus mode."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = body.get("mode")
    valid_modes = ("work", "deep-focus", "planning", "creative", "relax", "sleep")
    if mode not in valid_modes:
        return web.json_response(
            {"error": f"invalid mode. valid: {', '.join(valid_modes)}"}, status=400
        )

    tracker = _get_tracker()
    if tracker:
        tracker.set_preference("chat_mode", mode)

    return web.json_response({"mode": mode})


async def handle_deck_models(request: "web.Request") -> "web.Response":
    """GET /api/deck/models — Active model routing info + user override + LLM mode state."""
    user_id = request.get("deck_user_id", 0)

    # Get user's current tier override from the Telegram channel
    user_tier_override = ""
    try:
        gateway = request.app.get("gateway")
        if gateway and hasattr(gateway, 'channels'):
            tg = gateway.channels.get('telegram')
            if tg and hasattr(tg, '_user_model_prefs'):
                user_tier_override = tg._user_model_prefs.get(user_id, "")
    except Exception:
        pass

    # LLM Mode Router state (mode-based routing)
    llm_mode_routing = False
    llm_mode_summary = {}
    try:
        from navig.llm_router import get_llm_router
        llm_router = get_llm_router()
        if llm_router:
            llm_mode_routing = True
            modes_cfg = llm_router.modes
            for mode_name in ("small_talk", "big_tasks", "coding", "summarize", "research"):
                mc = modes_cfg.get_mode(mode_name)
                if mc:
                    llm_mode_summary[mode_name] = {
                        "provider": mc.provider,
                        "model": mc.model,
                    }
    except Exception:
        pass

    try:
        from navig.agent.ai_client import get_ai_client
        client = get_ai_client()
        router = client.model_router
        if router and router.is_active:
            cfg = router.cfg
            models = []
            for tier_name, slot in [("small", cfg.small), ("big", cfg.big), ("coder_big", cfg.coder_big)]:
                models.append({
                    "tier": tier_name,
                    "model": slot.model,
                    "provider": slot.provider,
                    "max_tokens": slot.max_tokens,
                    "temperature": slot.temperature,
                })
            return web.json_response({
                "routing_active": True,
                "mode": cfg.mode,
                "fallback_enabled": cfg.fallback_enabled,
                "models": models,
                "user_tier_override": user_tier_override,
                "llm_mode_routing": llm_mode_routing,
                "llm_modes": llm_mode_summary,
            })
    except Exception as e:
        logger.debug("Models endpoint error: %s", e)

    return web.json_response({
        "routing_active": False,
        "mode": "single",
        "models": [],
        "user_tier_override": user_tier_override,
    })


async def handle_deck_models_set(request: "web.Request") -> "web.Response":
    """POST /api/deck/models — Set user's default tier override."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    tier = body.get("tier", "")
    valid_tiers = ("", "small", "big", "coder_big")
    if tier not in valid_tiers:
        return web.json_response(
            {"error": f"invalid tier. valid: {', '.join(valid_tiers)}"}, status=400
        )

    user_id = request.get("deck_user_id", 0)

    # Apply the tier override to the Telegram channel's per-user prefs
    applied = False
    try:
        gateway = request.app.get("gateway")
        if gateway and hasattr(gateway, 'channels'):
            tg = gateway.channels.get('telegram')
            if tg and hasattr(tg, '_user_model_prefs'):
                if tier:
                    tg._user_model_prefs[user_id] = tier
                else:
                    tg._user_model_prefs.pop(user_id, None)
                applied = True
    except Exception as e:
        logger.debug("Failed to apply tier override: %s", e)

    # Resolve the model name for the selected tier
    model_info = {"tier": tier or "auto", "model": "", "provider": ""}
    try:
        from navig.agent.ai_client import get_ai_client
        client = get_ai_client()
        router = client.model_router
        if router and router.is_active and tier:
            slot = router.cfg.slot_for_tier(tier)
            model_info["model"] = slot.model
            model_info["provider"] = slot.provider
    except Exception:
        pass

    return web.json_response({
        "ok": applied,
        "tier": tier or "auto",
        "model": model_info.get("model", ""),
        "provider": model_info.get("provider", ""),
    })


async def handle_deck_models_available(request: "web.Request") -> "web.Response":
    """GET /api/deck/models/available — List models from all configured providers."""
    available = []

    # Query Ollama for local models
    try:
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession() as session:
            async with session.get("http://localhost:11434/api/tags", timeout=_aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for m in data.get("models", []):
                        size_mb = m.get("size", 0) // 1024 // 1024
                        available.append({
                            "provider": "ollama",
                            "model": m.get("name", ""),
                            "size_mb": size_mb,
                            "family": m.get("details", {}).get("family", ""),
                        })
    except Exception as e:
        logger.debug("Ollama query failed: %s", e)

    # Check if OpenRouter key is available
    has_openrouter = False
    try:
        from navig.config import get_config_manager
        cfg = get_config_manager()
        key = cfg.global_config.get("openrouter_api_key", "")
        if key and len(key) > 10:
            has_openrouter = True
    except Exception:
        pass

    return web.json_response({
        "available": available,
        "providers": {
            "ollama": len(available) > 0,
            "openrouter": has_openrouter,
        },
    })


# ────────────────────────────────────────────────────────────────
# LLM Modes API Handlers
# ────────────────────────────────────────────────────────────────

def _get_llm_router():
    """Lazily import and return the LLM router singleton."""
    try:
        from navig.llm_router import get_llm_router
        return get_llm_router()
    except Exception as e:
        logger.debug("LLM router not available: %s", e)
        return None


def _get_provider_key_status() -> dict:
    """Check which providers have API keys available (env or vault)."""
    from navig.llm_router import PROVIDER_ENV_KEYS, SUPPORTED_PROVIDERS
    status = {}
    vault = _get_vault()
    for provider in sorted(SUPPORTED_PROVIDERS):
        has_key = False
        # Check env vars
        env_keys = PROVIDER_ENV_KEYS.get(provider, [])
        for ek in env_keys:
            if os.environ.get(ek):
                has_key = True
                break
        # Check vault
        if not has_key and vault:
            try:
                creds = vault.list_credentials()
                for c in creds:
                    if c.get("provider") == provider and c.get("enabled", True):
                        has_key = True
                        break
            except Exception:
                pass
        # Ollama is always "available" (local)
        if provider == "ollama":
            has_key = True
        status[provider] = has_key
    return status


async def handle_deck_llm_modes_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/llm-modes — Return all LLM mode configs + provider key status."""
    router = _get_llm_router()
    if router is None:
        return web.json_response({"error": "LLM router not available"}, status=500)

    try:
        modes = router.get_all_modes()
        provider_status = _get_provider_key_status()
        uncensored = router.list_uncensored_models()

        return web.json_response({
            "modes": modes,
            "providers": provider_status,
            "uncensored": uncensored,
        })
    except Exception as e:
        logger.error("Failed to get LLM modes: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_llm_modes_update(request: "web.Request") -> "web.Response":
    """POST /api/deck/llm-modes — Update a mode's configuration."""
    router = _get_llm_router()
    if router is None:
        return web.json_response({"error": "LLM router not available"}, status=500)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = body.get("mode", "").strip()
    if not mode:
        return web.json_response({"error": "mode is required"}, status=400)

    from navig.llm_router import CANONICAL_MODES, LLMModeRouter
    canonical = LLMModeRouter.resolve_mode(mode)
    if canonical not in CANONICAL_MODES:
        return web.json_response({"error": f"invalid mode: {mode}"}, status=400)

    # Extract update fields
    provider = body.get("provider")
    model = body.get("model")
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens")
    use_uncensored = body.get("use_uncensored")
    fallback_model = body.get("fallback_model")

    # Validate types
    if temperature is not None:
        try:
            temperature = float(temperature)
            if not (0.0 <= temperature <= 2.0):
                return web.json_response({"error": "temperature must be 0.0-2.0"}, status=400)
        except (ValueError, TypeError):
            return web.json_response({"error": "invalid temperature"}, status=400)

    if max_tokens is not None:
        try:
            max_tokens = int(max_tokens)
            if max_tokens < 1 or max_tokens > 131072:
                return web.json_response({"error": "max_tokens must be 1-131072"}, status=400)
        except (ValueError, TypeError):
            return web.json_response({"error": "invalid max_tokens"}, status=400)

    # Apply update
    ok = router.update_mode(
        canonical,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        use_uncensored=use_uncensored,
    )

    if not ok:
        return web.json_response({"error": "failed to update mode"}, status=500)

    # Also update fallback_model directly if provided
    if fallback_model is not None:
        cfg_obj = router.modes.get_mode(canonical)
        if cfg_obj:
            cfg_obj.fallback_model = str(fallback_model)

    # Persist to config.yaml
    try:
        from navig.config import get_config_manager
        cm = get_config_manager()
        raw = cm.global_config or {}
        llm_router_section = raw.get("llm_router", {})
        llm_modes_section = llm_router_section.get("llm_modes", {})

        # Get updated mode config
        updated = router.get_all_modes().get(canonical, {})
        llm_modes_section[canonical] = updated
        llm_router_section["llm_modes"] = llm_modes_section
        raw["llm_router"] = llm_router_section
        cm.update_global(raw)
        logger.info("Persisted LLM mode update: %s", canonical)
    except Exception as e:
        logger.warning("Could not persist LLM mode to config: %s", e)

    return web.json_response({
        "ok": True,
        "mode": canonical,
        "config": router.get_all_modes().get(canonical, {}),
    })


async def handle_deck_llm_modes_detect(request: "web.Request") -> "web.Response":
    """POST /api/deck/llm-modes/detect — Test mode detection on text."""
    router = _get_llm_router()
    if router is None:
        return web.json_response({"error": "LLM router not available"}, status=500)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    detected = router.detect_mode(text)
    resolved = router.get_config(detected)

    return web.json_response({
        "ok": True,
        "mode": detected,
        "provider": resolved.provider,
        "model": resolved.model,
        "is_uncensored": resolved.is_uncensored,
        "reason": resolved.resolution_reason,
    })


# ────────────────────────────────────────────────────────────────
# Vault API Handlers
# ────────────────────────────────────────────────────────────────

# Well-known providers that can be added via the Deck UI
_VAULT_ALLOWED_PROVIDERS = {
    "openai", "openrouter", "anthropic", "groq", "google",
    "github", "gitlab", "huggingface", "replicate", "together",
    "mistral", "cohere", "deepseek", "perplexity",
    "ollama", "siliconflow", "grok", "xai",
}


def _mask_key(key: str) -> str:
    """Mask an API key for safe display: show first 6 and last 4 chars."""
    if not key or len(key) < 12:
        return "••••••••"
    return f"{key[:6]}••••{key[-4:]}"


def _get_vault():
    """Lazily import and return the vault singleton."""
    try:
        from navig.vault import get_vault
        return get_vault()
    except Exception as e:
        logger.debug("Vault not available: %s", e)
        return None


async def handle_deck_vault_list(request: "web.Request") -> "web.Response":
    """GET /api/deck/vault — List all credentials (masked, no secrets)."""
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    try:
        creds = vault.list()
        items = []
        for c in creds:
            items.append({
                "id": c.id,
                "provider": c.provider,
                "profile_id": c.profile_id,
                "credential_type": c.credential_type.value if hasattr(c.credential_type, 'value') else str(c.credential_type),
                "label": c.label,
                "enabled": c.enabled,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "metadata": c.metadata or {},
            })

            # Add masked key preview (read full cred just for masking)
            try:
                full_cred = vault.get_by_id(c.id, caller="deck_vault_list")
                if full_cred and full_cred.data:
                    key_val = full_cred.data.get("api_key", "") or full_cred.data.get("token", "")
                    items[-1]["key_preview"] = _mask_key(key_val)
                else:
                    items[-1]["key_preview"] = "••••••••"
            except Exception:
                items[-1]["key_preview"] = "••••••••"

        return web.json_response({
            "credentials": items,
            "allowed_providers": sorted(_VAULT_ALLOWED_PROVIDERS),
        })
    except Exception as e:
        logger.error("Vault list error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_add(request: "web.Request") -> "web.Response":
    """POST /api/deck/vault — Add a new credential."""
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    provider = body.get("provider", "").strip().lower()
    api_key = body.get("api_key", "").strip()
    label = body.get("label", "").strip()

    if not provider:
        return web.json_response({"error": "provider is required"}, status=400)
    if provider not in _VAULT_ALLOWED_PROVIDERS:
        return web.json_response(
            {"error": f"provider not allowed. valid: {', '.join(sorted(_VAULT_ALLOWED_PROVIDERS))}"},
            status=400,
        )
    if not api_key:
        return web.json_response({"error": "api_key is required"}, status=400)
    if len(api_key) < 10:
        return web.json_response({"error": "api_key too short"}, status=400)

    try:
        cred_id = vault.add(
            provider=provider,
            credential_type="api_key",
            data={"api_key": api_key},
            profile_id="default",
            label=label or f"{provider.title()} Key",
        )
        return web.json_response({
            "ok": True,
            "id": cred_id,
            "provider": provider,
            "key_preview": _mask_key(api_key),
        })
    except Exception as e:
        logger.error("Vault add error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_delete(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/vault/{id} — Delete a credential."""
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        deleted = vault.delete(cred_id)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error("Vault delete error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_toggle(request: "web.Request") -> "web.Response":
    """POST /api/deck/vault/{id}/toggle — Enable or disable a credential."""
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    enabled = body.get("enabled", True)

    try:
        if enabled:
            result = vault.enable(cred_id)
        else:
            result = vault.disable(cred_id)
        return web.json_response({"ok": result, "enabled": enabled})
    except Exception as e:
        logger.error("Vault toggle error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_test(request: "web.Request") -> "web.Response":
    """POST /api/deck/vault/{id}/test — Test a credential with provider validation."""
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        result = vault.test(cred_id)
        return web.json_response({
            "ok": result.success,
            "message": result.message,
            "details": result.details,
        })
    except Exception as e:
        logger.error("Vault test error: %s", e)
        return web.json_response({"error": str(e)}, status=500)# ────────────────────────────────────────────────────────────────
# Static File Serving
# ────────────────────────────────────────────────────────────────

def _find_deck_static_dir(override: Optional[str] = None) -> Optional[Path]:
    """Locate the Deck static build directory."""
    # Config override takes priority
    if override:
        p = Path(override).expanduser()
        if p.is_dir() and (p / "index.html").exists():
            return p
        logger.warning("Deck static_dir override not found: %s", override)

    candidates = [
        Path(__file__).parent.parent.parent / "deck-static",       # navig-core/deck-static/
        Path.home() / "navig-core" / "deck-static",                 # ~/navig-core/deck-static/
        Path(__file__).parent.parent.parent.parent / "navig-deck" / "dist",  # monorepo/navig-deck/dist/
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").exists():
            return p
    return None


async def handle_deck_index(request: "web.Request") -> "web.Response":
    """Serve index.html for the Deck SPA (catch-all)."""
    static_dir = _find_deck_static_dir()
    if not static_dir:
        return web.Response(text="Deck not built. Run: cd navig-deck && npm run build", status=404)
    return web.FileResponse(static_dir / "index.html")


# ────────────────────────────────────────────────────────────────
# Route Registration
# ────────────────────────────────────────────────────────────────

def register_deck_routes(
    app: "web.Application",
    bot_token: str = "",
    allowed_users: Optional[List[int]] = None,
    require_auth: bool = True,
    deck_cfg: Optional[Dict[str, Any]] = None,
):
    """
    Register all Deck API and static file routes on the gateway app.
    
    Args:
        app: aiohttp Application to mount routes on
        bot_token: Telegram bot token for initData HMAC validation
        allowed_users: List of Telegram user IDs allowed to access Deck
        require_auth: Whether to enforce user allowlist
        deck_cfg: Deck config dict (enabled, port, bind, static_dir, dev_mode, auth_max_age)
    """
    deck_cfg = deck_cfg or {}

    # Configure module-level auth
    configure_deck_auth(
        bot_token=bot_token,
        allowed_users=allowed_users or [],
        require_auth=require_auth,
        dev_mode=deck_cfg.get("dev_mode", False),
        auth_max_age=deck_cfg.get("auth_max_age", 3600),
    )

    # Add auth middleware to the app
    # We insert at position 0 so it runs before CORS middleware
    app.middlewares.insert(0, deck_auth_middleware)

    # API routes
    app.router.add_get("/api/deck/status", handle_deck_status)
    app.router.add_get("/api/deck/settings", handle_deck_settings_get)
    app.router.add_post("/api/deck/settings", handle_deck_settings_post)
    app.router.add_post("/api/deck/mode", handle_deck_mode)
    app.router.add_get("/api/deck/models", handle_deck_models)
    app.router.add_post("/api/deck/models", handle_deck_models_set)
    app.router.add_get("/api/deck/models/available", handle_deck_models_available)

    # LLM Modes routes
    app.router.add_get("/api/deck/llm-modes", handle_deck_llm_modes_get)
    app.router.add_post("/api/deck/llm-modes", handle_deck_llm_modes_update)
    app.router.add_post("/api/deck/llm-modes/detect", handle_deck_llm_modes_detect)

    # Vault routes
    app.router.add_get("/api/deck/vault", handle_deck_vault_list)
    app.router.add_post("/api/deck/vault", handle_deck_vault_add)
    app.router.add_delete("/api/deck/vault/{cred_id}", handle_deck_vault_delete)
    app.router.add_post("/api/deck/vault/{cred_id}/toggle", handle_deck_vault_toggle)
    app.router.add_post("/api/deck/vault/{cred_id}/test", handle_deck_vault_test)

    # Static file serving for Deck SPA
    static_dir = _find_deck_static_dir(deck_cfg.get("static_dir"))
    if static_dir:
        # Serve assets (JS, CSS, etc.)
        app.router.add_static("/deck/assets", static_dir / "assets", show_index=False)
        # Serve other static files
        for f in static_dir.iterdir():
            if f.is_file() and f.name != "index.html":
                app.router.add_get(f"/deck/{f.name}", lambda req, fp=f: web.FileResponse(fp))
        # SPA catch-all — serve index.html for all /deck/* routes
        app.router.add_get("/deck/{path:.*}", handle_deck_index)
        app.router.add_get("/deck", handle_deck_index)
        app.router.add_get("/deck/", handle_deck_index)

        logger.info("Deck static files registered from %s", static_dir)
    else:
        # Still register the catch-all so it shows a helpful error
        app.router.add_get("/deck/{path:.*}", handle_deck_index)
        app.router.add_get("/deck", handle_deck_index)
        logger.warning("Deck static dir not found — API only, no SPA")

    logger.info("Deck API routes registered at /api/deck/ (auth enabled)")
