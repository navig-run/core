from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_FALLBACK_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openrouter": ("OPENROUTER_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "nvidia": ("NVIDIA_API_KEY", "NIM_API_KEY"),
    "xai": ("XAI_API_KEY", "GROK_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
    "github_models": ("GITHUB_TOKEN", "GH_TOKEN"),
}

_FALLBACK_CFG_KEYS: dict[str, tuple[str, ...]] = {
    "openrouter": ("openrouter_api_key",),
    "openai": ("openai_api_key",),
    "anthropic": ("anthropic_api_key",),
    "groq": ("groq_api_key",),
    "google": ("google_api_key", "gemini_api_key"),
    "gemini": ("google_api_key", "gemini_api_key"),
    "nvidia": ("nvidia_api_key", "nim_api_key"),
    "xai": ("xai_api_key", "grok_key"),
    "mistral": ("mistral_api_key",),
    "github_models": ("github_token", "gh_token"),
}

_FALLBACK_PROVIDER_IDS: tuple[str, ...] = (
    "openrouter",
    "openai",
    "anthropic",
    "groq",
    "gemini",
    "nvidia",
    "xai",
    "mistral",
    "github_models",
)


def _load_config(navig_dir: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import]

        cfg_path = navig_dir / "config.yaml"
        if not cfg_path.exists():
            return {}
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def provider_env_vars(provider_id: str) -> tuple[str, ...]:
    env_vars: list[str] = []
    try:
        from navig.providers.registry import get_provider

        manifest = get_provider(provider_id)
        if manifest is not None:
            env_vars.extend([str(v).strip() for v in (manifest.env_vars or []) if str(v).strip()])
    except Exception:  # noqa: BLE001
        pass

    for fallback in _FALLBACK_ENV_VARS.get(provider_id, ()):
        if fallback not in env_vars:
            env_vars.append(fallback)
    return tuple(env_vars)


def provider_env_key(provider_id: str) -> str:
    for env_name in provider_env_vars(provider_id):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def provider_has_config_key(provider_id: str, *, navig_dir: Path, cfg: dict[str, Any] | None = None) -> bool:
    data = cfg if cfg is not None else _load_config(navig_dir)
    for key in _FALLBACK_CFG_KEYS.get(provider_id, ()):
        if str(data.get(key) or "").strip():
            return True
    return False


def provider_has_vault_key(provider_id: str) -> bool:
    try:
        from navig.vault.core import get_vault

        vault = get_vault()
        if vault is None:
            return False

        labels: list[str] = [f"{provider_id}/api_key"]
        try:
            from navig.vault.resolver import vault_labels_for_env

            for env_name in provider_env_vars(provider_id):
                labels.extend(vault_labels_for_env(env_name))
        except Exception:  # noqa: BLE001
            pass

        for label in labels:
            try:
                secret = (vault.get_secret(label) or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if secret:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def detect_provider_sources(provider_id: str, *, navig_dir: Path, cfg: dict[str, Any] | None = None) -> list[str]:
    sources: list[str] = []
    if provider_env_key(provider_id):
        sources.append("env")
    if provider_has_vault_key(provider_id):
        sources.append("vault")
    if provider_has_config_key(provider_id, navig_dir=navig_dir, cfg=cfg):
        sources.append("config")
    return sources


def scan_enabled_provider_sources(*, navig_dir: Path, cfg: dict[str, Any] | None = None) -> dict[str, list[str]]:
    try:
        from navig.providers.registry import list_enabled_providers

        provider_ids = [
            str(provider.id)
            for provider in list_enabled_providers()
            if str(getattr(provider, "id", "")).strip()
        ]
    except Exception:  # noqa: BLE001
        provider_ids = list(_FALLBACK_PROVIDER_IDS)

    deduped = sorted(set(provider_ids))
    config = cfg if cfg is not None else _load_config(navig_dir)

    detected: dict[str, list[str]] = {}
    for provider_id in deduped:
        sources = detect_provider_sources(provider_id, navig_dir=navig_dir, cfg=config)
        if sources:
            detected[provider_id] = sources
    return detected
