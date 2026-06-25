"""Vault credential endpoints for the Deck API."""

import asyncio
import logging
import sys
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.vault.secret_str import mask_secret

logger = logging.getLogger(__name__)

_VAULT_ALLOWED_PROVIDERS = {
    # AI / LLM
    "openai",
    "openrouter",
    "anthropic",
    "groq",
    "google",
    "deepseek",
    "mistral",
    "cohere",
    "perplexity",
    "fireworks",
    "nvidia",
    "together",
    "replicate",
    "huggingface",
    "siliconflow",
    "grok",
    "xai",
    "ollama",
    "cerebras",
    "ai21",
    # Voice / Audio
    "deepgram",
    "elevenlabs",
    # Developer tools
    "github",
    "gitlab",
    "vercel",
    "netlify",
    "linear",
    "notion",
    # Infrastructure / Cloud
    "aws",
    "gcp",
    "azure",
    "hetzner",
    "digitalocean",
    "cloudflare",
    "fly",
    # Data / SaaS
    "stripe",
    "supabase",
    "pinecone",
    "neon",
    "redis",
    # Search / research / enrichment
    "tavily",
    "serpapi",
    "brave",
    "hunter",
    "apollo",
    # Messaging / social adapters
    "telegram",
    "discord",
    "slack",
    "whatsapp",
    "sms",
    "linkedin",
    "linkedin_page",
    "reddit",
    "instagram_meta",
    "instagram",
    "facebook",
    "threads",
    "youtube",
    "vk",
    "medium",
    "devto",
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
            meta = c.metadata or {}
            profile_id = meta.get("profile_id", "")
            raw_ct = meta.get("credential_type", "")
            credential_type = (
                raw_ct.value if hasattr(raw_ct, "value") else str(raw_ct)
            ) if raw_ct else ""
            items.append(
                {
                    "id": c.id,
                    "provider": c.provider,
                    "profile_id": profile_id,
                    "credential_type": credential_type,
                    "label": c.label,
                    "enabled": c.enabled,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "last_used_at": (c.last_used_at.isoformat() if c.last_used_at else None),
                    "metadata": meta,
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

        # Probe whether openai-whisper local package is installed
        whisper_installed = False
        try:
            import whisper as _whisper_pkg  # noqa: F401
            whisper_installed = True
        except ImportError:
            pass

        return web.json_response(
            {
                "credentials": items,
                "allowed_providers": sorted(_VAULT_ALLOWED_PROVIDERS),
                "whisper_installed": whisper_installed,
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
                "key_preview": mask_secret(api_key, show_prefix=6),
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


# ── Whisper install: background runner + status polling ──────────────────────
#
# `pip install openai-whisper` pulls torch (~2 GB) and easily exceeds any
# reasonable HTTP timeout. We spawn the install as a background task and let
# the client poll /whisper/install/status until it finishes.

_WHISPER_INSTALL_STATE: dict[str, Any] = {
    "running": False,
    "ok": None,        # None while running, True/False when done
    "message": "",     # latest line of output / final outcome
    "started_at": 0.0,
    "finished_at": 0.0,
}


def _whisper_already_installed() -> bool:
    """Cheap check — try importing the module without invoking it."""
    try:
        import importlib.util
        return importlib.util.find_spec("whisper") is not None
    except Exception:
        return False


async def _run_whisper_install() -> None:
    """Background coroutine: runs `pip install openai-whisper` to completion."""
    import time as _time
    _WHISPER_INSTALL_STATE.update({
        "running": True,
        "ok": None,
        "message": "Starting pip install openai-whisper…",
        "started_at": _time.time(),
        "finished_at": 0.0,
    })
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "openai-whisper",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge so we see warnings too
        )
        # Read line-by-line so the latest progress line is always visible.
        last_line = ""
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.readline()
            if not chunk:
                break
            line = chunk.decode(errors="replace").rstrip()
            if line:
                last_line = line
                _WHISPER_INSTALL_STATE["message"] = line[:240]
        await proc.wait()
        ok = proc.returncode == 0
        _WHISPER_INSTALL_STATE.update({
            "running": False,
            "ok": ok,
            "message": (last_line or ("Installed successfully" if ok else "Install failed"))[:240],
            "finished_at": _time.time(),
        })
    except Exception as exc:
        logger.exception("Whisper install error")
        _WHISPER_INSTALL_STATE.update({
            "running": False,
            "ok": False,
            "message": f"Install failed: {exc}",
            "finished_at": _time.time(),
        })


async def handle_deck_whisper_install(request: "web.Request") -> "web.Response":
    """Kick off the openai-whisper install in the background.

    Returns immediately with `{ok: true, started: bool, already_installed: bool}`.
    Long-running pip resolution / torch download happens asynchronously.
    Clients should poll `/api/deck/whisper/install/status` to track progress.
    """
    if _whisper_already_installed():
        _WHISPER_INSTALL_STATE.update({
            "running": False, "ok": True, "message": "Already installed",
        })
        return web.json_response({"ok": True, "started": False, "already_installed": True})

    if _WHISPER_INSTALL_STATE["running"]:
        return web.json_response({"ok": True, "started": False, "already_running": True})

    # Fire and forget. The task runs in the same event loop and updates the
    # shared dict above; no need to track the task handle.
    asyncio.create_task(_run_whisper_install())
    return web.json_response({"ok": True, "started": True})


async def handle_deck_whisper_install_status(request: "web.Request") -> "web.Response":
    """Poll endpoint — returns the current state of the install."""
    state = dict(_WHISPER_INSTALL_STATE)
    state["already_installed"] = _whisper_already_installed()
    return web.json_response(state)
