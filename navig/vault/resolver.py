"""NAVIG Vault Resolver — ${VAULT:path} reference substitution.

Replaces ``${VAULT:label}`` (and legacy ``${BLACKBOX:key}`` / ``${CRED:provider}``)
references in any string with decrypted values pulled from the vault.

Usage
-----
from navig.vault.resolver import resolve_refs

config_text = "api_key=${VAULT:openai/api_key}"
resolved    = resolve_refs(config_text)
# → "api_key=sk-..."

Raises
------
RuntimeError
    If the vault is locked (no active session and machine-fingerprint fails).
KeyError
    If a referenced label cannot be found in the vault.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

__all__ = [
    "ENV_VAULT_LABELS",
    "resolve_refs",
    "has_refs",
    "list_refs",
    "vault_labels_for_env",
    "resolve_secret",
    "resolve_json_str",
]


ENV_VAULT_LABELS: dict[str, list[str]] = {
    "OPENAI_API_KEY": ["openai/api_key", "openai/api-key", "openai_api_key"],
    "OPENROUTER_API_KEY": [
        "openrouter/api_key",
        "openrouter/api-key",
        "openrouter_api_key",
    ],
    "ANTHROPIC_API_KEY": [
        "anthropic/api_key",
        "anthropic/api-key",
        "anthropic_api_key",
    ],
    "CLAUDE_API_KEY": ["anthropic/api_key", "anthropic/api-key", "anthropic_api_key"],
    "GROQ_API_KEY": ["groq/api_key", "groq/api-key", "groq_api_key"],
    "GEMINI_API_KEY": ["google/api_key", "google/api-key", "google_api_key"],
    "GOOGLE_API_KEY": ["google/api_key", "google/api-key", "google_api_key"],
    "NVIDIA_API_KEY": ["nvidia/api_key", "nvidia/api-key", "nvidia_api_key"],
    "NIM_API_KEY": ["nvidia/api_key", "nvidia/api-key", "nvidia_api_key"],
    "XAI_API_KEY": ["xai/api_key", "xai/api-key", "xai_api_key"],
    "GROK_KEY": ["xai/api_key", "xai/api-key", "xai_api_key"],
    "MISTRAL_API_KEY": ["mistral/api_key", "mistral/api-key", "mistral_api_key"],
    "GITHUB_TOKEN": ["github/token", "github_models/token", "github_models", "github"],
    "NAVIG_BRIDGE_LLM_TOKEN": [
        "bridge/llm_token",
        "bridge/llm-token",
        "navig_bridge/llm_token",
    ],
    "DEEPGRAM_KEY": ["deepgram/api_key", "deepgram/api-key", "deepgram_api_key"],
    "DEEPGRAM_API_KEY": ["deepgram/api_key", "deepgram/api-key", "deepgram_api_key"],
    "ELEVENLABS_API_KEY": [
        "elevenlabs/api_key",
        "elevenlabs/api-key",
        "elevenlabs_api_key",
    ],
    "XI_API_KEY": ["elevenlabs/api_key", "elevenlabs/api-key", "elevenlabs_api_key"],
    "GOOGLE_CLOUD_API_KEY": ["google/api_key", "google/api-key", "google_api_key"],
    "GOOGLE_TTS_API_KEY": ["google/api_key", "google/api-key", "google_api_key"],
    "AUDD_API_KEY": ["audd/api_key", "audd/api-key", "audd_api_key"],
    "SPOTIFY_CLIENT_ID": [
        "spotify/client_id",
        "spotify/client-id",
        "spotify_client_id",
    ],
    "SPOTIFY_CLIENT_SECRET": [
        "spotify/client_secret",
        "spotify/client-secret",
        "spotify_client_secret",
    ],
    "LASTFM_API_KEY": ["lastfm/api_key", "lastfm/api-key", "lastfm_api_key"],
    "SERPAPI_KEY": ["serpapi/api_key", "serpapi/api-key", "serpapi_api_key"],
    "SERPAPI_API_KEY": ["serpapi/api_key", "serpapi/api-key", "serpapi_api_key"],
    "GOOGLE_APPLICATION_CREDENTIALS": [
        "google/vision-service-account",
        "google/tts-service-account",
    ],
    "GOOGLE_TTS_SERVICE_ACCOUNT": [
        "google/tts-service-account",
        "google/vision-service-account",
    ],
    "DISCORD_BOT_TOKEN": ["discord/bot_token", "discord/token"],
    "TELEGRAM_BOT_TOKEN": [
        "telegram/bot_token",
        "telegram/token",
        "telegram/bot-token",
        "telegram_bot_token",
    ],
    "WHATSAPP_BRIDGE_API_KEY": ["whatsapp/bridge_api_key", "whatsapp/api_key"],
}

# Matches ${VAULT:any/path}, ${BLACKBOX:key}, ${CRED:provider}
_PATTERN = re.compile(r"\$\{(VAULT|BLACKBOX|CRED):([^}]+)\}")


def has_refs(text: str) -> bool:
    """Return True if *text* contains any vault reference tokens."""
    return bool(_PATTERN.search(text))


def list_refs(text: str) -> list[tuple[str, str]]:
    """Return all (namespace, label) pairs found in *text*."""
    return [(m.group(1), m.group(2)) for m in _PATTERN.finditer(text)]


def resolve_refs(text: str, strict: bool = True) -> str:
    """Replace all vault reference tokens in *text* with decrypted values.

    Parameters
    ----------
    text   : String that may contain ``${VAULT:...}`` tokens.
    strict : If True (default), raise on missing items/locked vault.
             If False, leave unresolved tokens in place.

    Returns
    -------
    str
        Text with all resolvable tokens replaced.
    """

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        namespace = match.group(1)  # VAULT | BLACKBOX | CRED
        label = match.group(2)

        # Normalize legacy namespaces to VAULT
        if namespace in ("BLACKBOX", "CRED"):
            label = label  # pass-through — same label lookup in vault

        try:
            value = _fetch_secret(label)
        except Exception:
            if strict:
                raise
            return match.group(0)  # leave token as-is
        return value

    return _PATTERN.sub(_replace, text)


def _fetch_secret(label: str) -> str:
    """Resolve a label to its plaintext secret value.

    Tries active session first; falls back to machine-fingerprint unlock.
    Provider shorthand is supported: "openai" → looks for VaultItemKind.PROVIDER
    with label "openai".
    """
    # Lazy imports to avoid circular dependency at module load
    from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

    vcore = get_vault_v2()
    return vcore.get_secret(label)


def vault_labels_for_env(env_var: str) -> list[str]:
    """Return candidate vault labels for an environment variable name."""
    return list(ENV_VAULT_LABELS.get(env_var, []))


def _normalize_names(names: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if names is None:
        return []
    if isinstance(names, str):
        return [names]
    return [name for name in names if name]


def resolve_secret(
    env_vars: str | list[str] | tuple[str, ...],
    vault_labels: str | list[str] | tuple[str, ...] | None = None,
) -> Optional[str]:
    """Resolve a runtime secret from env first, then vault.

    If *vault_labels* is omitted, candidate labels are inferred from the first
    matching env var name via ``ENV_VAULT_LABELS``.
    """
    env_names = _normalize_names(env_vars)
    for env_var in env_names:
        value = os.environ.get(env_var, "").strip()
        if value:
            return value

    labels = _normalize_names(vault_labels)
    if not labels:
        for env_var in env_names:
            labels.extend(vault_labels_for_env(env_var))

    if not labels:
        return None

    from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

    vault = get_vault_v2()
    for label in labels:
        try:
            value = vault.get_secret(label)
        except Exception:
            continue
        if value:
            return value
    return None


def resolve_json_str(
    env_vars: str | list[str] | tuple[str, ...],
    vault_labels: str | list[str] | tuple[str, ...] | None = None,
) -> Optional[str]:
    """Resolve a JSON secret blob from env first, then vault.

    Environment values may be either raw JSON strings or filesystem paths to a
    JSON file. Vault values are resolved with ``get_json_str()`` first and then
    ``get_secret()`` as a compatibility fallback.
    """
    env_names = _normalize_names(env_vars)
    for env_var in env_names:
        value = os.environ.get(env_var, "").strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                continue
        return value

    labels = _normalize_names(vault_labels)
    if not labels:
        for env_var in env_names:
            labels.extend(vault_labels_for_env(env_var))

    if not labels:
        return None

    from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

    vault = get_vault_v2()
    for label in labels:
        try:
            value = vault.get_json_str(label)
        except Exception:
            try:
                value = vault.get_secret(label)
            except Exception:
                continue
        if value:
            return value
    return None


# ── Convenience: resolve a dict recursively ──────────────────────────────────


def resolve_dict(obj: object, strict: bool = True) -> object:
    """Recursively resolve vault refs in dicts, lists, and strings."""
    if isinstance(obj, str):
        return resolve_refs(obj, strict=strict)
    if isinstance(obj, dict):
        return {k: resolve_dict(v, strict=strict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_dict(item, strict=strict) for item in obj]
    return obj
