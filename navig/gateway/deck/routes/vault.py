"""Vault credential endpoints for the Deck API."""

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.vault.secret_str import mask_secret

logger = logging.getLogger(__name__)

_VAULT_ALLOWED_PROVIDERS = {
    "openai",
    "openrouter",
    "anthropic",
    "groq",
    "google",
    "github",
    "gitlab",
    "huggingface",
    "replicate",
    "together",
    "mistral",
    "cohere",
    "deepseek",
    "perplexity",
    "ollama",
    "siliconflow",
    "grok",
    "xai",
}


from navig.gateway.deck.routes._utils import _get_vault


async def handle_deck_vault_list(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    try:
        creds = vault.list()
        items = []
        for c in creds:
            items.append(
                {
                    "id": c.id,
                    "provider": c.provider,
                    "profile_id": c.profile_id,
                    "credential_type": (
                        c.credential_type.value
                        if hasattr(c.credential_type, "value")
                        else str(c.credential_type)
                    ),
                    "label": c.label,
                    "enabled": c.enabled,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "last_used_at": (c.last_used_at.isoformat() if c.last_used_at else None),
                    "metadata": c.metadata or {},
                }
            )

            try:
                full_cred = vault.get_by_id(c.id, caller="deck_vault_list")
                if full_cred and full_cred.data:
                    key_val = full_cred.data.get("api_key", "") or full_cred.data.get("token", "")
                    items[-1]["key_preview"] = mask_secret(key_val, show_prefix=6)
                else:
                    items[-1]["key_preview"] = "••••••••"
            except Exception:
                items[-1]["key_preview"] = "••••••••"

        return web.json_response(
            {
                "credentials": items,
                "allowed_providers": sorted(_VAULT_ALLOWED_PROVIDERS),
            }
        )
    except Exception as e:
        logger.error("Vault list error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_add(request: "web.Request") -> "web.Response":
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
            {
                "error": f"provider not allowed. valid: {', '.join(sorted(_VAULT_ALLOWED_PROVIDERS))}"
            },
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
        return web.json_response(
            {
                "ok": True,
                "id": cred_id,
                "provider": provider,
                "key_preview": _mask_key(api_key),
            }
        )
    except Exception as e:
        logger.error("Vault add error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_delete(request: "web.Request") -> "web.Response":
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
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        result = vault.test(cred_id)
        return web.json_response(
            {
                "ok": result.success,
                "message": result.message,
                "details": result.details,
            }
        )
    except Exception as e:
        logger.error("Vault test error: %s", e)
        return web.json_response({"error": str(e)}, status=500)
