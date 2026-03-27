"""LLM Modes selection endpoints for the Deck API."""

import logging
import os

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _get_llm_router():
    try:
        from navig.llm_router import get_llm_router

        return get_llm_router()
    except Exception as e:
        logger.debug("LLM router not available: %s", e)
        return None


def _get_vault():
    try:
        from navig.vault import get_vault

        return get_vault()
    except Exception as e:
        logger.debug("Vault not available: %s", e)
        return None


def _get_provider_key_status() -> dict:
    from navig.llm_router import PROVIDER_ENV_KEYS, SUPPORTED_PROVIDERS

    status = {}
    vault = _get_vault()
    for provider in sorted(SUPPORTED_PROVIDERS):
        has_key = False
        env_keys = PROVIDER_ENV_KEYS.get(provider, [])
        for ek in env_keys:
            if os.environ.get(ek):
                has_key = True
                break
        if not has_key and vault:
            try:
                creds = vault.list_credentials()
                for c in creds:
                    if c.get("provider") == provider and c.get("enabled", True):
                        has_key = True
                        break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if provider == "ollama":
            has_key = True
        status[provider] = has_key
    return status


async def handle_deck_llm_modes_get(request: "web.Request") -> "web.Response":
    router = _get_llm_router()
    if router is None:
        return web.json_response({"error": "LLM router not available"}, status=500)

    try:
        modes = router.get_all_modes()
        provider_status = _get_provider_key_status()
        uncensored = router.list_uncensored_models()

        return web.json_response(
            {
                "modes": modes,
                "providers": provider_status,
                "uncensored": uncensored,
            }
        )
    except Exception as e:
        logger.error("Failed to get LLM modes: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_llm_modes_update(request: "web.Request") -> "web.Response":
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

    provider = body.get("provider")
    model = body.get("model")
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens")
    use_uncensored = body.get("use_uncensored")
    fallback_model = body.get("fallback_model")

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

    if fallback_model is not None:
        cfg_obj = router.modes.get_mode(canonical)
        if cfg_obj:
            cfg_obj.fallback_model = str(fallback_model)

    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.global_config or {}
        llm_router_section = raw.get("llm_router", {})
        llm_modes_section = llm_router_section.get("llm_modes", {})

        updated = router.get_all_modes().get(canonical, {})
        llm_modes_section[canonical] = updated
        llm_router_section["llm_modes"] = llm_modes_section
        raw["llm_router"] = llm_router_section
        cm.update_global_config(raw)
        logger.info("Persisted LLM mode update: %s", canonical)
    except Exception as e:
        logger.warning("Could not persist LLM mode to config: %s", e)

    return web.json_response(
        {
            "ok": True,
            "mode": canonical,
            "config": router.get_all_modes().get(canonical, {}),
        }
    )


async def handle_deck_llm_modes_detect(request: "web.Request") -> "web.Response":
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

    return web.json_response(
        {
            "ok": True,
            "mode": detected,
            "provider": resolved.provider,
            "model": resolved.model,
            "is_uncensored": resolved.is_uncensored,
            "reason": resolved.resolution_reason,
        }
    )
