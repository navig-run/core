"""
NAVIG Deck API — REST endpoints for the Telegram Mini App.

Splits logic into:
- auth: Middleware and initData validation
- routes.core: Status, settings, user mode
- routes.models: Model overrides and limits
- routes.llm_modes: Advanced LLM mode configuration
- routes.vault: Key vault endpoints
- routes.static_assets: Serving the local Deck SPA
"""

import logging
from typing import Any, Dict, List, Optional  # noqa: F401

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.auth import configure_deck_auth, deck_auth_middleware
from navig.gateway.deck.routes.core import (
    handle_deck_mode,
    handle_deck_settings_get,
    handle_deck_settings_post,
    handle_deck_status,
)
from navig.gateway.deck.routes.llm_modes import (
    handle_deck_llm_modes_detect,
    handle_deck_llm_modes_get,
    handle_deck_llm_modes_update,
)
from navig.gateway.deck.routes.models import (
    handle_deck_models,
    handle_deck_models_available,
    handle_deck_models_set,
)
from navig.gateway.deck.routes.static_assets import (
    _find_deck_static_dir,
    handle_deck_index,
)
from navig.gateway.deck.routes.vault import (
    handle_deck_vault_add,
    handle_deck_vault_delete,
    handle_deck_vault_list,
    handle_deck_vault_test,
    handle_deck_vault_toggle,
)

logger = logging.getLogger(__name__)


def register_deck_routes(
    app: "web.Application",
    bot_token: str = "",
    allowed_users: list[int] | None = None,
    require_auth: bool = True,
    deck_cfg: dict[str, Any] | None = None,
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
    if web:
        app.middlewares.insert(0, deck_auth_middleware)

    # API routes
    if web:
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


__all__ = ["register_deck_routes"]
