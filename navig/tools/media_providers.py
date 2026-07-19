"""
NAVIG media-generation provider catalog — single source of truth.

One place that describes every image / video / audio generation provider NAVIG
can drive: its models, which vault credential + env var supplies its key, whether
it has a real free tier, and where to get a key. Both the generators
(`image_generation` / `video_generation` / `audio_generation`) and the Deck
catalog endpoint (`/api/deck/media/providers`) read from here so the picker the
user sees and the providers the engine can actually run never drift apart.

Nothing here makes network calls; it is pure metadata + a key resolver.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = [
    "MEDIA_CATALOG",
    "resolve_media_key",
    "provider_entry",
    "key_status",
    "catalog_payload",
]


# ── Key resolution ───────────────────────────────────────────────────────────
#
# Resolution order for a provider's key:
#   1. any explicit env var name we pass (e.g. GEMINI_API_KEY, GOOGLE_API_KEY)
#   2. the NAVIG vault (active profile) via the canonical get_api_key(), which
#      itself also falls back to {PROVIDER}_API_KEY.
# This lets a user drop a raw env var OR store a key in the Deck vault UI, and
# supports providers whose conventional env name differs from {PROVIDER}_API_KEY.


def resolve_media_key(vault_provider: str, *env_names: str) -> str | None:
    """Return the API key for *vault_provider*, or None if not configured.

    Args:
        vault_provider: the vault credential provider id (e.g. "google").
        *env_names: alternate environment variable names to try first
            (e.g. "GEMINI_API_KEY", "GOOGLE_API_KEY").
    """
    for name in env_names:
        val = os.environ.get(name)
        if val:
            return val.strip()
    try:
        from navig.vault.core import get_vault

        key = get_vault().get_api_key(vault_provider, caller="media_gen")
        if key:
            return str(key).strip()
    except Exception:  # noqa: BLE001 — vault may be unavailable outside the daemon
        pass
    return None


# ── The catalog ──────────────────────────────────────────────────────────────
#
# Each provider entry:
#   id            generation-routing id (matches the *Provider enums)
#   label         human label for the picker
#   modality      "image" | "video" | "audio"
#   models        [{id, label, note?}]  — selectable model variants
#   vault_provider  vault credential provider id used for the key
#   env           [env var names] the key can also come from
#   free          "yes" | "trial" | "no"  — is there a usable free tier?
#   price         short human price signal
#   get_key       URL where the user obtains a key
#   note          one-line positioning / caveat

MEDIA_CATALOG: dict[str, list[dict[str, Any]]] = {
    "image": [
        {
            "id": "recraft",
            "label": "Recraft",
            "modality": "image",
            "models": [
                {"id": "recraftv3", "label": "Recraft V3 — raster"},
                {"id": "recraftv3_vector", "label": "Recraft V3 — vector (SVG)"},
            ],
            "vault_provider": "recraft",
            "env": ["RECRAFT_API_KEY"],
            "free": "trial",
            "price": "~$0.035–0.30 / image",
            "get_key": "https://www.recraft.ai/  → profile → API",
            "note": "Best all-rounder for game assets, icons, banners; raster + true vector output.",
        },
        {
            "id": "openai_gpt_image",
            "label": "OpenAI gpt-image",
            "modality": "image",
            "models": [
                {"id": "gpt-image-1", "label": "gpt-image (edit + generate)"},
            ],
            "vault_provider": "openai",
            "env": ["OPENAI_API_KEY"],
            "free": "no",
            "price": "~$0.01–0.21 / image",
            "get_key": "https://platform.openai.com/api-keys",
            "note": "Best for iterative art direction + multi-turn edits. No vector output.",
        },
        {
            "id": "openai_dalle3",
            "label": "OpenAI DALL·E 3",
            "modality": "image",
            "models": [{"id": "dall-e-3", "label": "DALL·E 3"}],
            "vault_provider": "openai",
            "env": ["OPENAI_API_KEY"],
            "free": "no",
            "price": "~$0.04–0.12 / image",
            "get_key": "https://platform.openai.com/api-keys",
            "note": "Classic OpenAI image model (existing).",
        },
        {
            "id": "gemini_flash",
            "label": "Gemini 2.5 Flash Image",
            "modality": "image",
            "models": [{"id": "gemini-2.5-flash-image", "label": "Gemini 2.5 Flash Image"}],
            "vault_provider": "google",
            "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "free": "yes",
            "price": "~$0.039 / image (free tier available)",
            "get_key": "https://aistudio.google.com/apikey",
            "note": "Cheapest/fastest general image gen; free tier via Google AI Studio.",
        },
        {
            "id": "gemini_pro",
            "label": "Gemini 3 Pro Image",
            "modality": "image",
            "models": [{"id": "gemini-3-pro-image", "label": "Gemini 3 Pro Image"}],
            "vault_provider": "google",
            "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "free": "yes",
            "price": "~$0.134 / image (free tier available)",
            "get_key": "https://aistudio.google.com/apikey",
            "note": "Higher-end Google image tier; same free key as Flash.",
        },
        {
            "id": "stability",
            "label": "Stability AI",
            "modality": "image",
            "models": [{"id": "stable-diffusion-xl-1024-v1-0", "label": "SDXL 1.0"}],
            "vault_provider": "stability",
            "env": ["STABILITY_API_KEY"],
            "free": "no",
            "price": "credits",
            "get_key": "https://platform.stability.ai/account/keys",
            "note": "Stable Diffusion XL (existing).",
        },
        {
            "id": "local",
            "label": "Local (A1111 / ComfyUI)",
            "modality": "image",
            "models": [{"id": "local", "label": "Local model"}],
            "vault_provider": "",
            "env": ["LOCAL_IMAGE_API_URL"],
            "free": "yes",
            "price": "free (your GPU)",
            "get_key": "runs against http://localhost:7860",
            "note": "No key — drives a local Automatic1111 / ComfyUI endpoint (existing).",
        },
    ],
    "video": [
        {
            "id": "gemini_veo",
            "label": "Google Veo (Gemini)",
            "modality": "video",
            "models": [
                {"id": "veo-3.0-generate-preview", "label": "Veo 3"},
                {"id": "veo-3.0-fast-generate-preview", "label": "Veo 3 Fast"},
            ],
            "vault_provider": "google",
            "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "free": "yes",
            "price": "free tier (limited) via AI Studio",
            "get_key": "https://aistudio.google.com/apikey",
            "note": "Same free key as the Gemini images — one key, image + video.",
        },
        {
            "id": "replicate",
            "label": "Replicate",
            "modality": "video",
            "models": [
                {"id": "kwaivgi/kling-v2.1", "label": "Kling v2.1"},
                {"id": "luma/ray", "label": "Luma Ray"},
                {"id": "wan-video/wan-2.2-t2v-fast", "label": "Wan 2.2 (fast)"},
            ],
            "vault_provider": "replicate",
            "env": ["REPLICATE_API_TOKEN"],
            "free": "trial",
            "price": "pay-as-you-go",
            "get_key": "https://replicate.com/account/api-tokens",
            "note": "One token → a broad hosted catalog (Kling, Luma, Wan, …).",
        },
        {
            "id": "runway",
            "label": "Runway",
            "modality": "video",
            "models": [{"id": "gen4_turbo", "label": "Gen-4 Turbo"}],
            "vault_provider": "runway",
            "env": ["RUNWAYML_API_SECRET", "RUNWAY_API_KEY"],
            "free": "no",
            "price": "credits",
            "get_key": "https://dev.runwayml.com/  → API keys",
            "note": "Cinematic image→video / text→video. Paid.",
        },
        {
            "id": "luma",
            "label": "Luma Dream Machine",
            "modality": "video",
            "models": [{"id": "ray-2", "label": "Ray 2"}],
            "vault_provider": "luma",
            "env": ["LUMAAI_API_KEY", "LUMA_API_KEY"],
            "free": "no",
            "price": "credits",
            "get_key": "https://lumalabs.ai/dream-machine/api",
            "note": "Dreamy motion; direct Luma API. Paid.",
        },
    ],
    "audio": [
        {
            "id": "elevenlabs",
            "label": "ElevenLabs",
            "modality": "audio",
            "models": [
                {"id": "music", "label": "Music (compose)"},
                {"id": "sfx", "label": "Sound effects"},
                {"id": "tts", "label": "Text-to-speech"},
            ],
            "vault_provider": "elevenlabs",
            "env": ["ELEVENLABS_API_KEY", "ELEVEN_API_KEY"],
            "free": "yes",
            "price": "free tier (credits/mo)",
            "get_key": "https://elevenlabs.io/app/settings/api-keys",
            "note": "Official free tier for music, SFX and TTS.",
        },
    ],
}


def provider_entry(modality: str, provider_id: str) -> dict[str, Any] | None:
    """Return the catalog entry for a modality+provider id, or None."""
    for entry in MEDIA_CATALOG.get(modality, []):
        if entry["id"] == provider_id:
            return entry
    return None


def key_status(entry: dict[str, Any]) -> bool:
    """True when the provider has a resolvable key (vault or env), else False.

    Local providers with no `vault_provider` are treated as always available.
    """
    if not entry.get("vault_provider") and entry["id"] == "local":
        return True
    key = resolve_media_key(entry.get("vault_provider", ""), *entry.get("env", []))
    return bool(key)


def catalog_payload() -> dict[str, Any]:
    """Build the Deck-facing catalog: providers by modality + configured flag.

    Secrets are never included — only whether a key resolves (`configured`).
    """
    out: dict[str, Any] = {"modalities": {}}
    for modality, entries in MEDIA_CATALOG.items():
        out["modalities"][modality] = [
            {
                "id": e["id"],
                "label": e["label"],
                "models": e["models"],
                "vault_provider": e.get("vault_provider", ""),
                "env": e.get("env", []),
                "free": e.get("free", "no"),
                "price": e.get("price", ""),
                "get_key": e.get("get_key", ""),
                "note": e.get("note", ""),
                "configured": key_status(e),
            }
            for e in entries
        ]
    return out
