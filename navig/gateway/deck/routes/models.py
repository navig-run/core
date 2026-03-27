"""Model selection endpoints for the Deck API."""

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


async def handle_deck_models(request: "web.Request") -> "web.Response":
    user_id = request.get("deck_user_id", 0)

    user_tier_override = ""
    try:
        gateway = request.app.get("gateway")
        if gateway and hasattr(gateway, "channels"):
            tg = gateway.channels.get("telegram")
            if tg and hasattr(tg, "_user_model_prefs"):
                user_tier_override = tg._user_model_prefs.get(user_id, "")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    llm_mode_routing = False
    llm_mode_summary = {}
    try:
        from navig.llm_router import get_llm_router

        llm_router = get_llm_router()
        if llm_router:
            llm_mode_routing = True
            modes_cfg = llm_router.modes
            for mode_name in (
                "small_talk",
                "big_tasks",
                "coding",
                "summarize",
                "research",
            ):
                mc = modes_cfg.get_mode(mode_name)
                if mc:
                    llm_mode_summary[mode_name] = {
                        "provider": mc.provider,
                        "model": mc.model,
                    }
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    try:
        from navig.agent.ai_client import get_ai_client

        client = get_ai_client()
        router = client.model_router
        if router and router.is_active:
            cfg = router.cfg
            models = []
            for tier_name, slot in [
                ("small", cfg.small),
                ("big", cfg.big),
                ("coder_big", cfg.coder_big),
            ]:
                models.append(
                    {
                        "tier": tier_name,
                        "model": slot.model,
                        "provider": slot.provider,
                        "max_tokens": slot.max_tokens,
                        "temperature": slot.temperature,
                    }
                )
            return web.json_response(
                {
                    "routing_active": True,
                    "mode": cfg.mode,
                    "fallback_enabled": cfg.fallback_enabled,
                    "models": models,
                    "user_tier_override": user_tier_override,
                    "llm_mode_routing": llm_mode_routing,
                    "llm_modes": llm_mode_summary,
                }
            )
    except Exception as e:
        logger.debug("Models endpoint error: %s", e)

    return web.json_response(
        {
            "routing_active": False,
            "mode": "single",
            "models": [],
            "user_tier_override": user_tier_override,
        }
    )


async def handle_deck_models_set(request: "web.Request") -> "web.Response":
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
    applied = False
    try:
        gateway = request.app.get("gateway")
        if gateway and hasattr(gateway, "channels"):
            tg = gateway.channels.get("telegram")
            if tg and hasattr(tg, "_user_model_prefs"):
                if tier:
                    tg._user_model_prefs[user_id] = tier
                else:
                    tg._user_model_prefs.pop(user_id, None)
                applied = True
    except Exception as e:
        logger.debug("Failed to apply tier override: %s", e)

    model_info = {"tier": tier or "auto", "model": "", "provider": ""}
    try:
        from navig.agent.ai_client import get_ai_client

        client = get_ai_client()
        router = client.model_router
        if router and router.is_active and tier:
            slot = router.cfg.slot_for_tier(tier)
            model_info["model"] = slot.model
            model_info["provider"] = slot.provider
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return web.json_response(
        {
            "ok": applied,
            "tier": tier or "auto",
            "model": model_info.get("model", ""),
            "provider": model_info.get("provider", ""),
        }
    )


async def handle_deck_models_available(request: "web.Request") -> "web.Response":
    available = []
    try:
        import aiohttp as _aiohttp

        async with (
            _aiohttp.ClientSession() as session,
            session.get(
                "http://localhost:11434/api/tags",
                timeout=_aiohttp.ClientTimeout(total=5),
            ) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                for m in data.get("models", []):
                    size_mb = m.get("size", 0) // 1024 // 1024
                    available.append(
                        {
                            "provider": "ollama",
                            "model": m.get("name", ""),
                            "size_mb": size_mb,
                            "family": m.get("details", {}).get("family", ""),
                        }
                    )
    except Exception as e:
        logger.debug("Ollama query failed: %s", e)

    has_openrouter = False
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        key = cfg.global_config.get("openrouter_api_key", "")
        if key and len(key) > 10:
            has_openrouter = True
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return web.json_response(
        {
            "available": available,
            "providers": {
                "ollama": len(available) > 0,
                "openrouter": has_openrouter,
            },
        }
    )
