"""
Provider Discovery Layer

Public API that decouples Telegram UX from router internals.  Provides
enriched provider/model listings with capability metadata, connection
status, and vision-model resolution.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from navig.providers.capabilities import (
    Capability,
    capabilities_label,
    get_model_capabilities,
    list_vision_models,
)
from navig.providers.registry import (
    ProviderManifest,
    get_provider,
    list_enabled_providers,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ModelInfo:
    """Enriched model metadata for UI display."""

    name: str
    provider_id: str
    capabilities: list[Capability]
    capability_source: str  # "verified" | "inferred"
    is_current: bool = False  # Currently assigned to a tier
    tier: str = ""  # Which tier it's assigned to, if any
    capability_label: str = ""  # Emoji summary e.g. "👁 💻 🧠"


@dataclass
class ProviderInfo:
    """Enriched provider metadata for UI display."""

    id: str
    display_name: str
    emoji: str
    tier: str  # "cloud" | "proxy" | "local"
    connected: bool  # Key detected or local probe OK
    active: bool  # Currently the active routing provider
    models: list[ModelInfo] = field(default_factory=list)
    vision_models: list[ModelInfo] = field(default_factory=list)
    health: str = "unknown"  # "ready" | "key_missing" | "offline" | "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Connection probes
# ─────────────────────────────────────────────────────────────────────────────


def _check_env_keys(manifest: ProviderManifest) -> bool:
    """Return True if any of the provider's env vars are set."""
    for var in manifest.env_vars:
        if os.environ.get(var):
            return True
    return False


def _check_vault_keys(manifest: ProviderManifest) -> bool:
    """Return True if any vault key is available (best-effort)."""
    try:
        from navig.vault import get_vault

        vault = get_vault()
        if vault:
            for vk in manifest.vault_keys:
                if vault.get(vk):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _probe_connection(manifest: ProviderManifest) -> tuple[bool, str]:
    """Probe whether a provider is reachable.

    Returns ``(connected, health_status)`` where health_status is one of
    ``"ready"``, ``"key_missing"``, ``"offline"``, ``"unknown"``.
    """
    if manifest.tier == "local" and manifest.local_probe:
        # Quick TCP connect check
        try:
            import socket

            host, port_str = manifest.local_probe.rsplit(":", 1)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((host, int(port_str)))
            sock.close()
            if result == 0:
                return True, "ready"
            return False, "offline"
        except Exception:  # noqa: BLE001
            return False, "offline"

    if not manifest.requires_key:
        return True, "ready"

    # Cloud / proxy — check env + vault
    if _check_env_keys(manifest) or _check_vault_keys(manifest):
        return True, "ready"

    return False, "key_missing"


# ─────────────────────────────────────────────────────────────────────────────
# Active provider detection
# ─────────────────────────────────────────────────────────────────────────────


def _get_active_provider() -> str:
    """Return the currently active provider id from the LLM mode router."""
    try:
        from navig.llm_router import get_llm_router

        lr = get_llm_router()
        if lr:
            m = lr.modes.get_mode("big_tasks")
            if m and getattr(m, "provider", None):
                return m.provider
    except Exception:  # noqa: BLE001
        pass
    return ""


def _get_current_tier_assignments() -> dict[str, tuple[str, str]]:
    """Return ``{tier: (provider_id, model_name)}`` for active assignments.

    Tiers: ``"small"``, ``"big"``, ``"coder_big"``.
    """
    assignments: dict[str, tuple[str, str]] = {}
    try:
        from navig.llm_router import get_llm_router

        lr = get_llm_router()
        if not lr:
            return assignments
        tier_modes = {"small": "small_talk", "big": "big_tasks", "coder_big": "coding"}
        for tier, mode in tier_modes.items():
            mc = lr.modes.get_mode(mode)
            if mc:
                prov = getattr(mc, "provider", "") or ""
                model = getattr(mc, "model", "") or ""
                assignments[tier] = (prov, model)
    except Exception:  # noqa: BLE001
        pass
    return assignments


# ─────────────────────────────────────────────────────────────────────────────
# Public API: provider listing
# ─────────────────────────────────────────────────────────────────────────────


def list_connected_providers() -> list[ProviderInfo]:
    """Return enriched info for all enabled providers.

    Each entry includes connection status, capability-enriched model list,
    and whether it's the currently active routing provider.
    """
    active_prov = _get_active_provider()
    tier_assignments = _get_current_tier_assignments()
    providers = list_enabled_providers()

    result: list[ProviderInfo] = []
    for manifest in providers:
        connected, health = _probe_connection(manifest)
        is_active = manifest.id == active_prov

        # Build enriched model list
        models: list[ModelInfo] = []
        vision: list[ModelInfo] = []
        for model_name in manifest.models:
            caps, src = get_model_capabilities(model_name)
            # Check if this model is currently assigned to a tier
            current_tier = ""
            is_current = False
            for tier, (t_prov, t_model) in tier_assignments.items():
                if t_prov == manifest.id and t_model == model_name:
                    current_tier = tier
                    is_current = True
                    break

            info = ModelInfo(
                name=model_name,
                provider_id=manifest.id,
                capabilities=caps,
                capability_source=src,
                is_current=is_current,
                tier=current_tier,
                capability_label=capabilities_label(model_name),
            )
            models.append(info)
            if Capability.VISION in caps:
                vision.append(info)

        result.append(
            ProviderInfo(
                id=manifest.id,
                display_name=manifest.display_name,
                emoji=manifest.emoji,
                tier=manifest.tier,
                connected=connected,
                active=is_active,
                models=models,
                vision_models=vision,
                health=health,
            )
        )

    return result


def list_available_models(
    capability: Capability | None = None,
    connected_only: bool = True,
) -> list[ModelInfo]:
    """Return a flat list of models across all providers, optionally filtered.

    Parameters
    ----------
    capability:
        If given, only return models that have this capability.
    connected_only:
        If True (default), skip providers that aren't connected.
    """
    providers = list_connected_providers()
    result: list[ModelInfo] = []
    for prov in providers:
        if connected_only and not prov.connected:
            continue
        for model in prov.models:
            if capability is None or capability in model.capabilities:
                result.append(model)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Vision model resolution
# ─────────────────────────────────────────────────────────────────────────────

# Priority order for vision model resolution:
# 1. Session override  (so:vision_model)
# 2. Global config     (media_engine.vision_model + media_engine.vision_provider)
# 3. Active provider   (first vision-capable model from active provider)
# 4. Any connected     (first vision-capable model from any connected provider)


def resolve_vision_model(
    session_overrides: dict[str, Any] | None = None,
) -> tuple[str, str, str] | None:
    """Resolve the best vision model/provider pair.

    Parameters
    ----------
    session_overrides:
        Session metadata dict (``so:`` prefixed keys already stripped).
        May contain ``vision_provider`` and ``vision_model``.

    Returns
    -------
    ``(provider_id, model_name, resolution_reason)`` or ``None`` if no
    vision model is available.
    """
    # 1. Session override
    if session_overrides:
        so_provider = session_overrides.get("vision_provider", "")
        so_model = session_overrides.get("vision_model", "")
        if so_provider and so_model:
            return so_provider, so_model, "session_override"

    # 2. Global config
    try:
        from navig.config import get_config_manager

        gcfg = get_config_manager().global_config or {}
        me_cfg = gcfg.get("media_engine") or {}
        cfg_model = me_cfg.get("vision_model", "")
        cfg_provider = me_cfg.get("vision_provider", "")
        if cfg_model:
            # If provider not specified, try to infer from model name
            if not cfg_provider:
                cfg_provider = _infer_provider_from_model(cfg_model)
            if cfg_provider:
                return cfg_provider, cfg_model, "global_config"
    except Exception:  # noqa: BLE001
        pass

    # 3. Active provider's first vision model
    active_prov = _get_active_provider()
    if active_prov:
        manifest = get_provider(active_prov)
        if manifest:
            vision = list_vision_models(manifest.models)
            if vision:
                return active_prov, vision[0][0], "active_provider"

    # 4. Any connected provider's first vision model
    for prov in list_connected_providers():
        if prov.connected and prov.vision_models:
            first = prov.vision_models[0]
            return first.provider_id, first.name, "fallback"

    return None


def _infer_provider_from_model(model_name: str) -> str:
    """Best-effort: guess provider id from model name."""
    lower = model_name.lower()
    if "gpt" in lower or lower.startswith("o1") or lower.startswith("o3") or lower.startswith("o4"):
        return "openai"
    if "claude" in lower:
        return "anthropic"
    if "gemini" in lower:
        return "google"
    if "grok" in lower:
        return "xai"
    if "llama" in lower or "nemotron" in lower:
        return "groq"  # default fast Llama host
    if "mistral" in lower or "pixtral" in lower:
        return "mistral"
    if "deepseek" in lower:
        return "openrouter"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Vision API format helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_vision_api_format(provider_id: str) -> str:
    """Return the API format needed for vision calls to *provider_id*.

    Returns one of: ``"openai"``, ``"anthropic"``, ``"google"``.
    """
    if provider_id == "anthropic":
        return "anthropic"
    if provider_id in ("google", "gemini"):
        return "google"
    # OpenAI-compatible: openai, groq, nvidia, openrouter, github_models, xai, etc.
    return "openai"
