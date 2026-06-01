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
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.auth import configure_deck_auth, deck_auth_middleware
from navig.gateway.deck.routes.admin import (
    handle_deck_admin_agents,
    handle_deck_admin_connectors,
    handle_deck_admin_document_sets,
    handle_deck_admin_image_providers,
    handle_deck_admin_llm_providers,
    handle_deck_admin_mcp_servers,
    handle_deck_admin_search_providers,
    handle_deck_admin_service_accounts,
    handle_deck_admin_settings,
    handle_deck_admin_settings_update,
    handle_deck_admin_voice_providers,
)
from navig.gateway.deck.routes.core import (
    handle_deck_mode,
    handle_deck_settings_get,
    handle_deck_settings_post,
    handle_deck_status,
)
from navig.gateway.deck.routes.ops import (
    handle_deck_ops,
    handle_deck_ops_quick,
    handle_deck_ops_session,
    handle_deck_ops_toggle,
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
from navig.gateway.deck.routes.social import (
    handle_deck_social_adapter_get,
    handle_deck_social_adapter_post,
    handle_deck_social_matrix_bridges_deploy,
    handle_deck_social_matrix_bridges_get,
    handle_deck_social_matrix_get,
    handle_deck_social_matrix_update,
    handle_deck_social_status,
    handle_deck_social_telegram_commands,
    handle_deck_social_telegram_get,
    handle_deck_social_telegram_post,
)
from navig.gateway.deck.routes.audio import (
    handle_deck_audio_get,
    handle_deck_audio_post,
)
from navig.gateway.deck.routes.persona import (
    handle_deck_persona_get,
    handle_deck_persona_post,
)
from navig.gateway.deck.routes.vision import (
    handle_deck_vision_get,
    handle_deck_vision_post,
)
from navig.gateway.deck.routes.runtime import (
    handle_runtime_nodes,
    handle_runtime_missions,
    handle_runtime_receipts,
    handle_runtime_mission_advance,
)
from navig.gateway.deck.routes.apps import (
    handle_deck_apps_life,
    handle_deck_apps_health,
    handle_deck_apps_tasks_get,
    handle_deck_apps_tasks_add,
    handle_deck_apps_tasks_toggle,
    handle_deck_apps_habits_toggle,
    handle_deck_apps_reminders_get,
    handle_deck_apps_reminders_add,
    handle_deck_apps_reminders_delete,
    handle_deck_apps_plans,
    handle_deck_apps_finance,
    handle_deck_apps_goals,
    handle_deck_apps_goals_milestone,
    handle_deck_apps_calendar,
    handle_deck_apps_passport,
    handle_deck_apps_wallet,
    handle_deck_apps_wallet_send,
    handle_deck_apps_knowledge_get,
    handle_deck_apps_knowledge_add,
    handle_deck_apps_devops,
)
from navig.gateway.deck.routes.vault import (
    handle_deck_vault_add,
    handle_deck_vault_delete,
    handle_deck_vault_list,
    handle_deck_vault_test,
    handle_deck_vault_toggle,
    handle_deck_whisper_install,
)
from navig.gateway.deck.routes.monitor import (
    handle_deck_monitor_all,
    handle_deck_monitor_cpu,
    handle_deck_monitor_disk,
    handle_deck_monitor_memory,
    handle_deck_monitor_ports,
    handle_deck_monitor_services,
    handle_deck_monitor_uptime,
)
from navig.gateway.deck.routes.hosts import handle_deck_hosts
from navig.gateway.deck.routes.bizops import (
    handle_bizops_overview,
    handle_bizops_accounts_list,
    handle_bizops_accounts_create,
    handle_bizops_accounts_update,
    handle_bizops_accounts_reconcile,
    handle_bizops_transactions_list,
    handle_bizops_transactions_create,
    handle_bizops_transactions_quick,
    handle_bizops_transactions_import,
    handle_bizops_transactions_delete,
    handle_bizops_projects_list,
    handle_bizops_projects_create,
    handle_bizops_projects_update,
    handle_bizops_projects_summary,
    handle_bizops_categories_list,
    handle_bizops_categories_create,
    handle_bizops_invoices_list,
    handle_bizops_invoices_create,
    handle_bizops_invoices_mark_paid,
    handle_bizops_invoices_remind,
    handle_bizops_subs_list,
    handle_bizops_subs_create,
    handle_bizops_tax_get,
    handle_bizops_tax_set_rate,
    handle_bizops_decisions_list,
    handle_bizops_decisions_ack,
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

    # Auto-generate api_key if missing — printed prominently so the user can
    # copy it into the browser login.  Persist it with:
    #   navig config set deck.api_key <key>
    import secrets as _secrets
    api_key = str(deck_cfg.get("api_key") or "").strip()
    if not api_key:
        api_key = "navig_" + _secrets.token_urlsafe(32)
        logger.info(
            "Deck API key (auto-generated — not persisted):\n"
            "    %s\n"
            "  Paste this into the browser login screen.\n"
            "  To persist: navig config set deck.api_key %s",
            api_key,
            api_key,
        )

    # Configure module-level auth
    configure_deck_auth(
        bot_token=bot_token,
        allowed_users=allowed_users or [],
        require_auth=require_auth,
        dev_mode=deck_cfg.get("dev_mode", False),
        auth_max_age=deck_cfg.get("auth_max_age", 3600),
        api_key=api_key,
    )

    # Add auth middleware to the app.
    # Must be appended (not inserted at 0) so that the CORS middleware (already
    # at index 1) runs as the outermost wrapper.  CORS then post-processes the
    # 401/403 responses returned by deck_auth_middleware and adds the required
    # Access-Control-Allow-Origin headers before they reach the browser.
    if web:
        app.middlewares.append(deck_auth_middleware)

    # API routes
    if web:
        app.router.add_get("/api/deck/status", handle_deck_status)
        app.router.add_get("/api/deck/settings", handle_deck_settings_get)
        app.router.add_post("/api/deck/settings", handle_deck_settings_post)
        app.router.add_post("/api/deck/mode", handle_deck_mode)
        app.router.add_get("/api/deck/models", handle_deck_models)
        app.router.add_post("/api/deck/models", handle_deck_models_set)
        app.router.add_get("/api/deck/models/available", handle_deck_models_available)

        # Ops / Remote Control
        app.router.add_get("/api/deck/ops", handle_deck_ops)
        app.router.add_post("/api/deck/ops/toggle", handle_deck_ops_toggle)
        app.router.add_post("/api/deck/ops/session", handle_deck_ops_session)
        app.router.add_post("/api/deck/ops/quick", handle_deck_ops_quick)

        # LLM Modes routes
        app.router.add_get("/api/deck/llm-modes", handle_deck_llm_modes_get)
        app.router.add_post("/api/deck/llm-modes", handle_deck_llm_modes_update)
        app.router.add_post("/api/deck/llm-modes/detect", handle_deck_llm_modes_detect)

        # Admin (Onyx-ported provider registries)
        app.router.add_get("/api/deck/admin/llm-providers", handle_deck_admin_llm_providers)
        app.router.add_get("/api/deck/admin/search-providers", handle_deck_admin_search_providers)
        app.router.add_get("/api/deck/admin/image-providers", handle_deck_admin_image_providers)
        app.router.add_get("/api/deck/admin/voice-providers", handle_deck_admin_voice_providers)
        app.router.add_get("/api/deck/admin/mcp-servers", handle_deck_admin_mcp_servers)
        app.router.add_get("/api/deck/admin/settings", handle_deck_admin_settings)
        app.router.add_get("/api/deck/admin/agents", handle_deck_admin_agents)
        app.router.add_get("/api/deck/admin/connectors", handle_deck_admin_connectors)
        app.router.add_get("/api/deck/admin/document-sets", handle_deck_admin_document_sets)
        app.router.add_get("/api/deck/admin/service-accounts", handle_deck_admin_service_accounts)
        app.router.add_post("/api/deck/admin/settings", handle_deck_admin_settings_update)

        # Social Networks routes
        app.router.add_get("/api/deck/social/status", handle_deck_social_status)
        app.router.add_get("/api/deck/social/telegram", handle_deck_social_telegram_get)
        app.router.add_post("/api/deck/social/telegram", handle_deck_social_telegram_post)
        app.router.add_get("/api/deck/social/telegram/commands", handle_deck_social_telegram_commands)
        app.router.add_get("/api/deck/social/adapter/{network}", handle_deck_social_adapter_get)
        app.router.add_post("/api/deck/social/adapter/{network}", handle_deck_social_adapter_post)
        app.router.add_get("/api/deck/social/matrix", handle_deck_social_matrix_get)
        app.router.add_post("/api/deck/social/matrix", handle_deck_social_matrix_update)
        app.router.add_get("/api/deck/social/matrix/bridges", handle_deck_social_matrix_bridges_get)

        # Voice & Audio (per-user TTS config) — replaces in-chat /voice /audio menus
        app.router.add_get("/api/deck/audio", handle_deck_audio_get)
        app.router.add_post("/api/deck/audio", handle_deck_audio_post)

        # Persona switcher — replaces in-chat /persona /personas
        app.router.add_get("/api/deck/persona", handle_deck_persona_get)
        app.router.add_post("/api/deck/persona", handle_deck_persona_post)

        # Vision provider/model — replaces in-chat /provider_vision
        app.router.add_get("/api/deck/vision", handle_deck_vision_get)
        app.router.add_post("/api/deck/vision", handle_deck_vision_post)
        app.router.add_post("/api/deck/social/matrix/bridges/deploy", handle_deck_social_matrix_bridges_deploy)

        # Mesh / Flux peer discovery — registered by
        # navig.gateway.routes.mesh.register() (registry-backed, computes
        # health from heartbeat last_seen).

        # Runtime (Nodes / Missions / Receipts)
        app.router.add_get("/runtime/nodes", handle_runtime_nodes)
        app.router.add_get("/runtime/missions", handle_runtime_missions)
        app.router.add_get("/runtime/receipts", handle_runtime_receipts)
        app.router.add_post("/runtime/missions/{mission_id}/advance", handle_runtime_mission_advance)

        # LifeOps routes
        app.router.add_get("/api/deck/apps/life", handle_deck_apps_life)
        app.router.add_get("/api/deck/apps/health", handle_deck_apps_health)
        app.router.add_get("/api/deck/apps/tasks", handle_deck_apps_tasks_get)
        app.router.add_post("/api/deck/apps/tasks/add", handle_deck_apps_tasks_add)
        app.router.add_post("/api/deck/apps/tasks/toggle", handle_deck_apps_tasks_toggle)
        app.router.add_post("/api/deck/apps/habits/toggle", handle_deck_apps_habits_toggle)
        app.router.add_get("/api/deck/apps/reminders", handle_deck_apps_reminders_get)
        app.router.add_post("/api/deck/apps/reminders/add", handle_deck_apps_reminders_add)
        app.router.add_delete("/api/deck/apps/reminders/{rid}", handle_deck_apps_reminders_delete)
        app.router.add_get("/api/deck/apps/plans", handle_deck_apps_plans)
        app.router.add_get("/api/deck/apps/finance", handle_deck_apps_finance)
        app.router.add_get("/api/deck/apps/goals", handle_deck_apps_goals)
        app.router.add_post("/api/deck/apps/goals/update-milestone", handle_deck_apps_goals_milestone)
        app.router.add_get("/api/deck/apps/calendar", handle_deck_apps_calendar)
        app.router.add_get("/api/deck/apps/passport", handle_deck_apps_passport)
        app.router.add_get("/api/deck/apps/wallet", handle_deck_apps_wallet)
        app.router.add_post("/api/deck/apps/wallet/send", handle_deck_apps_wallet_send)
        app.router.add_get("/api/deck/apps/knowledge", handle_deck_apps_knowledge_get)
        app.router.add_post("/api/deck/apps/knowledge/add", handle_deck_apps_knowledge_add)
        app.router.add_get("/api/deck/apps/devops", handle_deck_apps_devops)

        # Vault routes
        app.router.add_get("/api/deck/vault", handle_deck_vault_list)
        app.router.add_post("/api/deck/vault", handle_deck_vault_add)
        app.router.add_delete("/api/deck/vault/{cred_id}", handle_deck_vault_delete)
        app.router.add_post("/api/deck/vault/{cred_id}/toggle", handle_deck_vault_toggle)
        app.router.add_post("/api/deck/vault/{cred_id}/test", handle_deck_vault_test)
        app.router.add_post("/api/deck/whisper/install", handle_deck_whisper_install)

        # Hosts / Fleet endpoint
        app.router.add_get("/api/deck/hosts", handle_deck_hosts)

        # Monitor routes — system resources (CPU, RAM, disk, uptime, services, ports)
        app.router.add_get("/api/deck/monitor", handle_deck_monitor_all)
        app.router.add_get("/api/deck/monitor/disk", handle_deck_monitor_disk)
        app.router.add_get("/api/deck/monitor/memory", handle_deck_monitor_memory)
        app.router.add_get("/api/deck/monitor/cpu", handle_deck_monitor_cpu)
        app.router.add_get("/api/deck/monitor/uptime", handle_deck_monitor_uptime)
        app.router.add_get("/api/deck/monitor/services", handle_deck_monitor_services)
        app.router.add_get("/api/deck/monitor/ports", handle_deck_monitor_ports)

        # Static file serving for Deck SPA
        static_dir = _find_deck_static_dir(deck_cfg.get("static_dir"))
        if static_dir:
            # Serve assets (JS, CSS, etc.) — only if the subdirectory actually exists.
            _assets_dir = static_dir / "assets"
            if _assets_dir.is_dir():
                app.router.add_static("/deck/assets", _assets_dir, show_index=False)
            # Serve other static files
            for f in static_dir.iterdir():
                # Plain filenames only — no path separators, no hidden files, not index.html.
                # Reject symlinks entirely: we serve only real files within the static dir.
                # f comes from iterdir() over a trusted config path, not from user input —
                # no resolve()/relative_to() needed; symlink exclusion prevents traversal.
                if (
                    f.is_file()
                    and not f.is_symlink()
                    and f.name != "index.html"
                    and "/" not in f.name
                    and "\\" not in f.name
                    and not f.name.startswith(".")
                ):

                    async def _serve_static_file(request, fp=f):
                        return web.FileResponse(fp)

                    app.router.add_get(f"/deck/{f.name}", _serve_static_file)
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
