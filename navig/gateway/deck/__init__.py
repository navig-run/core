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
from navig.gateway.deck.routes.ask import (
    handle_deck_ask,
)
from navig.gateway.deck.routes.vision import (
    handle_deck_vision_get,
    handle_deck_vision_post,
)
from navig.gateway.deck.routes.cloud import (
    handle_deck_cloud_enabled,
    handle_deck_cloud_restart,
    handle_deck_cloud_status,
)
from navig.gateway.deck.routes.license import (
    handle_deck_license_paste,
    handle_deck_license_raw,
    handle_deck_license_status,
)
from navig.gateway.deck.routes.runtime import (
    handle_runtime_nodes,
    handle_runtime_missions,
    handle_runtime_receipts,
    handle_runtime_mission_create,
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
from navig.gateway.deck.routes.board import (
    handle_board_get,
    handle_board_goal_create,
    handle_board_goal_update,
    handle_board_goal_delete,
    handle_board_goal_generate,
    handle_board_goal_run,
    handle_board_card_create,
    handle_board_card_update,
    handle_board_card_delete,
    handle_board_card_move,
    handle_board_card_run,
    handle_board_card_approve,
    handle_board_card_reject,
    handle_board_dep_add,
    handle_board_dep_remove,
    handle_board_subtask_add,
    handle_board_subtask_update,
    handle_board_subtask_delete,
    handle_board_briefing,
    handle_board_settings_get,
    handle_board_settings_post,
)
from navig.gateway.deck.routes.vault import (
    handle_deck_vault_add,
    handle_deck_vault_delete,
    handle_deck_vault_list,
    handle_deck_vault_test,
    handle_deck_vault_toggle,
    handle_deck_whisper_install,
    handle_deck_whisper_install_status,
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
from navig.gateway.deck.routes.batch import handle_deck_batch
from navig.gateway.deck.routes.context import (
    handle_deck_context,
    handle_deck_context_files,
    handle_deck_spaces,
)
from navig.gateway.deck.routes.catalog import (
    handle_deck_catalog,
    handle_deck_catalog_install,
    handle_deck_space_activate,
    handle_deck_space_disable,
    handle_deck_space_enable,
    handle_deck_space_register,
    handle_deck_spaces_scan,
)
from navig.gateway.deck.routes.requests import (
    handle_deck_requests_list,
    handle_deck_requests_next_action,
    handle_deck_requests_respond,
)
from navig.gateway.deck.routes.inbox import (
    handle_deck_extract_mode_get,
    handle_deck_extract_mode_set,
    handle_deck_inbox_capture,
    handle_deck_inbox_list,
    handle_deck_inbox_process_all,
    handle_deck_inbox_promote,
    handle_deck_inbox_reroute,
    handle_deck_inbox_route,
    handle_deck_inbox_skip,
    handle_deck_inbox_upload,
    handle_deck_plan_context,
)
from navig.gateway.deck.routes.cli import (
    handle_deck_cli_commands,
    handle_deck_cli_exec,
)
from navig.gateway.deck.routes.logs import (
    handle_deck_logs_sources,
    handle_deck_logs_tail,
)
from navig.gateway.deck.routes.briefing import (
    handle_deck_briefing,
    handle_deck_briefing_regenerate,
)
from navig.gateway.deck.routes.nettools import (
    handle_deck_net_server,
    handle_deck_net_dns,
    handle_deck_net_ssl,
    handle_deck_net_whois,
    handle_deck_net_weather,
)
from navig.gateway.deck.routes.skills import (
    handle_deck_skills,
    handle_deck_skill_detail,
)
from navig.gateway.deck.routes.database import (
    handle_deck_db_hosts,
    handle_deck_db_list,
    handle_deck_db_tables,
    handle_deck_db_query,
)
from navig.gateway.deck.routes.schedule import (
    handle_deck_reminders_list,
    handle_deck_reminders_create,
    handle_deck_reminder_cancel,
    handle_deck_crons_list,
    # Aliased: this is the schedule-tab briefing (bound to /api/deck/schedule/briefing).
    # Without the alias it shadowed routes.briefing.handle_deck_briefing and hijacked
    # the canonical GET /api/deck/briefing.
    handle_deck_briefing as handle_deck_schedule_briefing,
)
from navig.gateway.deck.routes.messages import (
    handle_deck_messages_threads_list,
    handle_deck_messages_thread_detail,
    handle_deck_messages_contacts,
    handle_deck_messages_contact_add,
    handle_deck_messages_contact_update,
    handle_deck_messages_contact_delete,
    handle_deck_messages_send,
)
from navig.gateway.deck.routes.remote import (
    handle_deck_remote_hosts,
    handle_deck_remote_host_use,
    handle_deck_remote_host_test,
    handle_deck_remote_files,
    handle_deck_remote_cat,
    handle_deck_remote_run,
    handle_deck_remote_deploy,
    handle_deck_remote_deploy_status,
    handle_deck_remote_docker,
    handle_deck_remote_backup,
)
from navig.gateway.deck.routes.connectors import (
    handle_deck_connectors_list,
    handle_deck_connectors_connect,
    handle_deck_connectors_callback,
    handle_deck_connectors_oauth_callback,
    handle_deck_connectors_disconnect,
    handle_deck_connectors_health,
    handle_deck_connectors_search,
    handle_deck_connectors_fetch,
    handle_deck_connectors_act,
    handle_deck_mcp_list,
    handle_deck_mcp_add,
    handle_deck_mcp_remove,
)
# BizOps + financial-connector routes (the `business_ops` tier) are provided by the
# private `navig-harbor` plugin via the `gateway:register_routes` hook — they are NOT
# imported in public core. (Public core ships only the seam; see register_deck_routes.)

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

    # Make any hook-firing inbox pipeline binary-aware (idempotent, best-effort).
    try:
        from navig.inbox.extract_hook import register_default_extract_hook

        register_default_extract_hook()
    except Exception:  # noqa: BLE001 — never block deck boot on this
        pass

    # Auto-generate api_key if missing AND persist it to ~/.navig/config.yaml
    # immediately. Without persistence, every restart minted a fresh key,
    # invalidating every Telegram/Deck binding the user had set up. The cost
    # of a single file write at startup is trivial compared to that breakage.
    import secrets as _secrets

    def _persist_api_key(key: str, *, reason: str) -> None:
        """Mutate the live deck_cfg + persist the key so restarts reuse it.

        deck_cfg is a reference into config_manager.global_config -- mutating it
        propagates to the rest of the boot path (CloudManager, etc.).
        """
        try:
            deck_cfg["api_key"] = key
        except Exception:  # noqa: BLE001
            pass
        try:
            from navig.core import Config
            _cfg = Config()
            _cfg.set("deck.api_key", key, scope="global")
            _cfg.save(scope="global")
            logger.info(
                "Deck API key %s and persisted to ~/.navig/config.yaml:\n"
                "    %s\n"
                "  Subsequent restarts will reuse this key.",
                reason, key,
            )
        except Exception as _persist_exc:  # noqa: BLE001
            # If we can't persist (read-only FS in a container?), fall back to
            # the old behavior of printing the ephemeral key.
            logger.warning(
                "Could not persist deck.api_key (%s). Using ephemeral key:\n    %s\n"
                "  To persist manually: navig config set deck.api_key %s",
                _persist_exc, key, key,
            )

    # The deck's Bearer api_key is the ONLY auth for a publicly-reachable deck
    # (Lighthouse / Direct / tunnel). It must always be present AND strong, so:
    #   • missing  → mint a 32-byte url-safe key and persist it.
    #   • weak     → a too-short key (< 16 chars) is brute-forceable; upgrade it
    #     to a strong one and WARN loudly (prints the new key to re-paste).
    # 16 chars is the floor; minted keys are ~43 chars after the `navig_` prefix.
    _MIN_API_KEY_LEN = 16
    api_key = str(deck_cfg.get("api_key") or "").strip()
    if not api_key:
        api_key = "navig_" + _secrets.token_urlsafe(32)
        _persist_api_key(api_key, reason="generated")
    elif len(api_key) < _MIN_API_KEY_LEN:
        weak = api_key
        api_key = "navig_" + _secrets.token_urlsafe(32)
        logger.warning(
            "SECURITY: deck.api_key was too weak (%d chars < %d) — a public deck "
            "with a guessable key is a real risk. Upgrading to a strong key.\n"
            "  Old (now invalid): %s\n"
            "  Re-paste the NEW key into any browser/Mini App deck you use.",
            len(weak), _MIN_API_KEY_LEN, weak,
        )
        _persist_api_key(api_key, reason="upgraded (was too weak)")

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

        # Ask NAVIG — one-shot conversational reply for the command palette's
        # "Ask NAVIG" row (backs the navig:ask-ai front-end seam).
        app.router.add_post("/api/deck/ask", handle_deck_ask)

        # Vision provider/model — replaces in-chat /provider_vision
        app.router.add_get("/api/deck/vision", handle_deck_vision_get)
        app.router.add_post("/api/deck/vision", handle_deck_vision_post)

        # Memory curation — propose → approve/reject + portable export/import.
        # Mounted under /api/deck for the SPA panel (deck auth) and mirrored at the
        # gateway level (/api/memory + /memory/review page) in routes/core.py.
        from navig.gateway.deck.routes.memory import (
            handle_memory_approve,
            handle_memory_export,
            handle_memory_import,
            handle_memory_pending,
            handle_memory_reject,
        )

        app.router.add_get("/api/deck/memory/facts/pending", handle_memory_pending)
        app.router.add_post("/api/deck/memory/facts/{fact_id}/approve", handle_memory_approve)
        app.router.add_post("/api/deck/memory/facts/{fact_id}/reject", handle_memory_reject)
        app.router.add_get("/api/deck/memory/facts/export", handle_memory_export)
        app.router.add_post("/api/deck/memory/facts/import", handle_memory_import)

        # Cloud broker/tunnel — exposes CloudManager state to Account → Cloud
        app.router.add_get("/api/deck/cloud/status", handle_deck_cloud_status)

        # License — Phase 3.2. Status + raw token + paste endpoint. The Deck
        # uses these to decide between TRIAL MODE and the full UI.
        app.router.add_get("/api/deck/license/status", handle_deck_license_status)
        app.router.add_get("/api/deck/license/raw", handle_deck_license_raw)
        app.router.add_post("/api/deck/license/paste", handle_deck_license_paste)
        app.router.add_post("/api/deck/cloud/enabled", handle_deck_cloud_enabled)
        app.router.add_post("/api/deck/cloud/restart", handle_deck_cloud_restart)
        app.router.add_post("/api/deck/social/matrix/bridges/deploy", handle_deck_social_matrix_bridges_deploy)

        # Mesh / Flux peer discovery — registered by
        # navig.gateway.routes.mesh.register() (registry-backed, computes
        # health from heartbeat last_seen).

        # Runtime (Nodes / Missions / Receipts)
        app.router.add_get("/runtime/nodes", handle_runtime_nodes)
        app.router.add_get("/runtime/missions", handle_runtime_missions)
        app.router.add_post("/runtime/missions", handle_runtime_mission_create)
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

        # Tasks board (pipeline-chain Kanban)
        app.router.add_get("/api/deck/board", handle_board_get)
        app.router.add_post("/api/deck/board/goals", handle_board_goal_create)
        app.router.add_patch("/api/deck/board/goals/{id}", handle_board_goal_update)
        app.router.add_delete("/api/deck/board/goals/{id}", handle_board_goal_delete)
        app.router.add_post("/api/deck/board/goals/{id}/generate", handle_board_goal_generate)
        app.router.add_post("/api/deck/board/goals/{id}/run", handle_board_goal_run)
        app.router.add_post("/api/deck/board/cards", handle_board_card_create)
        app.router.add_patch("/api/deck/board/cards/{id}", handle_board_card_update)
        app.router.add_delete("/api/deck/board/cards/{id}", handle_board_card_delete)
        app.router.add_post("/api/deck/board/cards/{id}/move", handle_board_card_move)
        app.router.add_post("/api/deck/board/cards/{id}/run", handle_board_card_run)
        app.router.add_post("/api/deck/board/cards/{id}/approve", handle_board_card_approve)
        app.router.add_post("/api/deck/board/cards/{id}/reject", handle_board_card_reject)
        app.router.add_post("/api/deck/board/cards/{id}/deps", handle_board_dep_add)
        app.router.add_delete("/api/deck/board/cards/{id}/deps/{dep}", handle_board_dep_remove)
        app.router.add_post("/api/deck/board/cards/{id}/subtasks", handle_board_subtask_add)
        app.router.add_patch("/api/deck/board/subtasks/{id}", handle_board_subtask_update)
        app.router.add_delete("/api/deck/board/subtasks/{id}", handle_board_subtask_delete)
        app.router.add_get("/api/deck/board/briefing", handle_board_briefing)
        app.router.add_get("/api/deck/board/settings", handle_board_settings_get)
        app.router.add_post("/api/deck/board/settings", handle_board_settings_post)
        app.router.add_get("/api/deck/apps/passport", handle_deck_apps_passport)
        app.router.add_get("/api/deck/apps/wallet", handle_deck_apps_wallet)
        app.router.add_post("/api/deck/apps/wallet/send", handle_deck_apps_wallet_send)
        app.router.add_get("/api/deck/apps/knowledge", handle_deck_apps_knowledge_get)
        app.router.add_post("/api/deck/apps/knowledge/add", handle_deck_apps_knowledge_add)
        # Deploy Ops module gate -- /apps/devops is the Deploy Ops module's
        # public-facing endpoint. Apps/wallet, finance, etc. live in mixed
        # modules and aren't gated at the registration level yet.
        from navig.license import requires_capability as _rc_deploy
        app.router.add_get(
            "/api/deck/apps/devops",
            _rc_deploy("deploy_ops")(handle_deck_apps_devops),
        )

        # Vault routes
        app.router.add_get("/api/deck/vault", handle_deck_vault_list)
        app.router.add_post("/api/deck/vault", handle_deck_vault_add)
        app.router.add_delete("/api/deck/vault/{cred_id}", handle_deck_vault_delete)
        app.router.add_post("/api/deck/vault/{cred_id}/toggle", handle_deck_vault_toggle)
        app.router.add_post("/api/deck/vault/{cred_id}/test", handle_deck_vault_test)
        app.router.add_post("/api/deck/whisper/install", handle_deck_whisper_install)
        app.router.add_get("/api/deck/whisper/install/status", handle_deck_whisper_install_status)

        # Connectors (OAuth integrations + MCP servers)
        app.router.add_get("/api/deck/connectors", handle_deck_connectors_list)
        # OAuth provider redirect target — auth-bypassed (no bearer token on the
        # provider's redirect; PKCE state is the security boundary). MUST be
        # registered before the {connector_id} routes so "oauth" isn't captured.
        app.router.add_get("/api/deck/connectors/oauth/callback", handle_deck_connectors_oauth_callback)
        app.router.add_post("/api/deck/connectors/{connector_id}/connect", handle_deck_connectors_connect)
        app.router.add_post("/api/deck/connectors/{connector_id}/connect/callback", handle_deck_connectors_callback)
        app.router.add_delete("/api/deck/connectors/{connector_id}", handle_deck_connectors_disconnect)
        app.router.add_get("/api/deck/connectors/{connector_id}/health", handle_deck_connectors_health)
        app.router.add_post("/api/deck/connectors/{connector_id}/search", handle_deck_connectors_search)
        app.router.add_post("/api/deck/connectors/{connector_id}/fetch", handle_deck_connectors_fetch)
        app.router.add_post("/api/deck/connectors/{connector_id}/act", handle_deck_connectors_act)
        app.router.add_get("/api/deck/mcp/servers", handle_deck_mcp_list)
        app.router.add_post("/api/deck/mcp/servers", handle_deck_mcp_add)
        app.router.add_delete("/api/deck/mcp/servers/{name}", handle_deck_mcp_remove)

        # Batch — collapse a screen's many GET reads into one round-trip
        app.router.add_post("/api/deck/batch", handle_deck_batch)

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

        # Context Engine — memory bank + indexed files + spaces.
        # NOTE: /api/deck/context returns the MEMORY-BANK OVERVIEW (stats / by-source /
        # recent), consumed by the deck Context app. The full PlanContext snapshot
        # (plans + wiki + docs + inbox, == PlanContext.gather()) is a DIFFERENT shape
        # and lives at /api/deck/context/snapshot (handle_deck_plan_context, bound below).
        # Context-snapshot consumers (forge/echo/spaces) must use /context/snapshot.
        app.router.add_get("/api/deck/context", handle_deck_context)
        app.router.add_get("/api/deck/context/files", handle_deck_context_files)
        app.router.add_get("/api/deck/spaces", handle_deck_spaces)

        # Spaces control panel + catalog/marketplace (registry-backed). Static
        # sub-paths (scan/register) registered before the {id} routes so they
        # are never captured by the dynamic segment.
        app.router.add_get("/api/deck/spaces/scan", handle_deck_spaces_scan)
        app.router.add_post("/api/deck/spaces/register", handle_deck_space_register)
        app.router.add_post("/api/deck/spaces/{id}/enable", handle_deck_space_enable)
        app.router.add_post("/api/deck/spaces/{id}/disable", handle_deck_space_disable)
        app.router.add_post("/api/deck/spaces/{id}/activate", handle_deck_space_activate)
        app.router.add_get("/api/deck/catalog", handle_deck_catalog)
        app.router.add_post("/api/deck/catalog/install", handle_deck_catalog_install)

        # Requests — unified "navig asks me for action" stream (approvals +
        # questions + operator proposals). The space picker reuses /spaces.
        app.router.add_get("/api/deck/requests", handle_deck_requests_list)
        app.router.add_post("/api/deck/requests/next-action", handle_deck_requests_next_action)
        app.router.add_post("/api/deck/requests/{request_id}/respond", handle_deck_requests_respond)
        # Plan-step: refine a chosen step into approach variants, then decompose
        # + run it as a tracked board task plan.
        from navig.gateway.deck.routes.plan_steps import (
            handle_plan_step_refine,
            handle_plan_step_execute,
        )
        app.router.add_post("/api/deck/requests/plan-step/refine", handle_plan_step_refine)
        app.router.add_post("/api/deck/requests/plan-step/execute", handle_plan_step_execute)

        # Inbox — document routing into navig spaces (live scan + history)
        app.router.add_get("/api/deck/inbox", handle_deck_inbox_list)
        app.router.add_post("/api/deck/inbox/upload", handle_deck_inbox_upload)
        app.router.add_post("/api/deck/inbox/capture", handle_deck_inbox_capture)
        app.router.add_get("/api/deck/inbox/extract-mode", handle_deck_extract_mode_get)
        app.router.add_post("/api/deck/inbox/extract-mode", handle_deck_extract_mode_set)
        app.router.add_get("/api/deck/context/snapshot", handle_deck_plan_context)
        app.router.add_post("/api/deck/inbox/process-all", handle_deck_inbox_process_all)
        app.router.add_post("/api/deck/inbox/{event_id}/route", handle_deck_inbox_route)
        app.router.add_post("/api/deck/inbox/{event_id}/skip", handle_deck_inbox_skip)
        app.router.add_post("/api/deck/inbox/{event_id}/reroute", handle_deck_inbox_reroute)
        app.router.add_post("/api/deck/inbox/{event_id}/promote", handle_deck_inbox_promote)

        # Notifications — cross-channel router: deck feed + per-type×channel matrix
        from navig.gateway.deck.routes.notify import (
            handle_notify_feed_list,
            handle_notify_feed_read,
            handle_notify_feed_read_all,
            handle_notify_prefs_get,
            handle_notify_prefs_post,
            handle_notify_test,
            handle_notify_briefing_now,
            handle_notify_sms_webhook,
            handle_notify_signals_get,
            handle_notify_signals_post,
            handle_notify_monitors_get,
            handle_notify_monitors_post,
        )
        app.router.add_get("/api/deck/notify/feed", handle_notify_feed_list)
        app.router.add_post("/api/deck/notify/feed/read-all", handle_notify_feed_read_all)
        app.router.add_post("/api/deck/notify/feed/{id}/read", handle_notify_feed_read)
        app.router.add_get("/api/deck/notify/prefs", handle_notify_prefs_get)
        app.router.add_post("/api/deck/notify/prefs", handle_notify_prefs_post)
        app.router.add_post("/api/deck/notify/test", handle_notify_test)
        app.router.add_post("/api/deck/notify/briefing", handle_notify_briefing_now)
        app.router.add_get("/api/deck/notify/sms/webhook", handle_notify_sms_webhook)
        app.router.add_post("/api/deck/notify/sms/webhook", handle_notify_sms_webhook)
        app.router.add_get("/api/deck/notify/signals", handle_notify_signals_get)
        app.router.add_post("/api/deck/notify/signals", handle_notify_signals_post)
        app.router.add_get("/api/deck/notify/monitors", handle_notify_monitors_get)
        app.router.add_post("/api/deck/notify/monitors", handle_notify_monitors_post)

        # Email-ops — filter rules + AI briefings (deliver via the notify router)
        from navig.gateway.deck.routes.email import (
            handle_email_config_get,
            handle_email_config_save,
            handle_email_brief_run,
            handle_email_status,
        )
        app.router.add_get("/api/deck/email/config", handle_email_config_get)
        app.router.add_post("/api/deck/email/config", handle_email_config_save)
        app.router.add_post("/api/deck/email/brief/run", handle_email_brief_run)
        app.router.add_get("/api/deck/email/status", handle_email_status)

        # Console — navig CLI catalog + streaming exec, and live log tailing
        app.router.add_get("/api/deck/cli/commands", handle_deck_cli_commands)
        app.router.add_post("/api/deck/cli/exec", handle_deck_cli_exec)
        app.router.add_get("/api/deck/logs/sources", handle_deck_logs_sources)
        app.router.add_get("/api/deck/logs", handle_deck_logs_tail)

        # Daily briefing — structured categorized report + regenerate
        app.router.add_get("/api/deck/briefing", handle_deck_briefing)
        app.router.add_post("/api/deck/briefing/regenerate", handle_deck_briefing_regenerate)

        # NetTools — diagnostics
        app.router.add_get("/api/deck/net/server", handle_deck_net_server)
        app.router.add_get("/api/deck/net/dns", handle_deck_net_dns)
        app.router.add_get("/api/deck/net/ssl", handle_deck_net_ssl)
        app.router.add_get("/api/deck/net/whois", handle_deck_net_whois)
        app.router.add_get("/api/deck/net/weather", handle_deck_net_weather)

        # Skills — discovery + detail
        app.router.add_get("/api/deck/skills", handle_deck_skills)
        app.router.add_get("/api/deck/skills/{skill_id}", handle_deck_skill_detail)

        # Database — list / tables / query (SSH-backed)
        app.router.add_get("/api/deck/db/hosts", handle_deck_db_hosts)
        app.router.add_post("/api/deck/db/list", handle_deck_db_list)
        app.router.add_post("/api/deck/db/tables", handle_deck_db_tables)
        app.router.add_post("/api/deck/db/query", handle_deck_db_query)

        # Schedule — reminders + crons + briefing
        app.router.add_get("/api/deck/schedule/reminders", handle_deck_reminders_list)
        app.router.add_post("/api/deck/schedule/reminders", handle_deck_reminders_create)
        app.router.add_delete("/api/deck/schedule/reminders/{reminder_id}", handle_deck_reminder_cancel)
        app.router.add_get("/api/deck/schedule/crons", handle_deck_crons_list)
        app.router.add_get("/api/deck/schedule/briefing", handle_deck_schedule_briefing)

        # Messages — threads + contacts + send across adapters
        app.router.add_get("/api/deck/messages/threads", handle_deck_messages_threads_list)
        app.router.add_get("/api/deck/messages/threads/{thread_id}", handle_deck_messages_thread_detail)
        app.router.add_get("/api/deck/messages/contacts", handle_deck_messages_contacts)
        app.router.add_post("/api/deck/messages/contacts", handle_deck_messages_contact_add)
        app.router.add_patch("/api/deck/messages/contacts/{alias}", handle_deck_messages_contact_update)
        app.router.add_delete("/api/deck/messages/contacts/{alias}", handle_deck_messages_contact_delete)
        app.router.add_post("/api/deck/messages/send", handle_deck_messages_send)

        # Telegram network manager — rooms, catalog, media analysis, post/edit/delete
        from navig.gateway.deck.routes import telegram_manager as _tg_manager
        _tg_manager.register(app)

        # Telegram Manager (MTProto user-client) — login, dialogs/topics, history
        # backfill, search, organize (forward/move/rename/delete/links/dedupe),
        # Business rights matrix. Wraps the navig.telegram Telethon engine.
        from navig.gateway.deck.routes import telegram_mtproto as _tg_mtproto
        _tg_mtproto.register(app)

        # Studio — social composer & scheduler (networks, media, posts, publish, AI)
        from navig.gateway.deck.routes import studio as _studio
        _studio.register(app)

        # Remote — SSH-backed host operations
        app.router.add_get("/api/deck/remote/hosts", handle_deck_remote_hosts)
        app.router.add_post("/api/deck/remote/hosts/use", handle_deck_remote_host_use)
        app.router.add_post("/api/deck/remote/hosts/test", handle_deck_remote_host_test)
        app.router.add_get("/api/deck/remote/files", handle_deck_remote_files)
        app.router.add_get("/api/deck/remote/cat", handle_deck_remote_cat)
        app.router.add_post("/api/deck/remote/run", handle_deck_remote_run)
        app.router.add_post("/api/deck/remote/deploy", handle_deck_remote_deploy)
        app.router.add_get("/api/deck/remote/deploy/status", handle_deck_remote_deploy_status)
        app.router.add_post("/api/deck/remote/docker", handle_deck_remote_docker)
        app.router.add_get("/api/deck/remote/backup", handle_deck_remote_backup)

        # BizOps + financial-connector routes (business_ops tier) live in the private
        # navig-harbor plugin. Fire the gateway route hook so any installed plugin can
        # register its deck routes here — public core has no hard dependency on them.
        from navig.core.hooks import trigger_hook_sync
        trigger_hook_sync(
            "gateway",
            "register_routes",
            {"app": app, "deck_cfg": deck_cfg, "require_auth": require_auth},
        )

        # Static file serving for Deck SPA
        static_dir = _find_deck_static_dir(deck_cfg.get("static_dir"))
        if static_dir:
            # Next.js compiles its JS/CSS/fonts into /_next/static/* with
            # *absolute* URLs (the build emits <script src="/_next/...">
            # baked into the HTML), so they MUST be served from the root
            # path, not under /deck/. Without this mount the browser gets
            # a wall of 404s for _next/static/chunks/*.js and the Deck
            # UI doesn't render. The static dir's _next/ folder exists
            # because Next's build copies it into out/ during npm run build.
            _next_dir = static_dir / "_next"
            if _next_dir.is_dir():
                app.router.add_static("/_next/", _next_dir, show_index=False)
            # Serve assets (JS, CSS, etc.) — only if the subdirectory actually exists.
            # Mounted at the root path (where index.html references them) and kept
            # at /deck/assets for backward compatibility with older links.
            _assets_dir = static_dir / "assets"
            if _assets_dir.is_dir():
                app.router.add_static("/assets", _assets_dir, show_index=False)
                app.router.add_static("/deck/assets", _assets_dir, show_index=False)
            # Serve other top-level static files (favicon, manifest, icons, …).
            # Registered at root — where the SPA references them — and mirrored
            # under /deck/ for back-compat.
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

                    app.router.add_get(f"/{f.name}", _serve_static_file)
                    app.router.add_get(f"/deck/{f.name}", _serve_static_file)
            # SPA index — served at root (primary) and /deck/* for back-compat.
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
