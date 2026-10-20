"""API keys for the media engine — found wherever the operator actually stored them.

Both engines looked keys up by ENVIRONMENT-VARIABLE name (``OPENAI_API_KEY``),
through the env and the vault's env-name resolver. A key added the normal way —
``navig vault add openai`` — is stored under the PROVIDER id ``openai`` and was
never found, so GPT-4o vision and Whisper silently returned nothing on an install
whose ``navig doctor`` reported "openai: key configured (vault:openai)".

Measured 2026-09-29: a business-chat role replying to a photo found no
description (vision never ran) and no text (the photo had none), gave up, and
the operator saw the trigger vanish and nothing arrive. Same class as #642, where
provider discovery made the identical miss; the vault's own API is keyed by
provider id (``Vault.get_api_key(provider)``), and that is the lookup added here.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def provider_key(provider: str, *env_names: str) -> str | None:
    """The API key for *provider*: env / env-named secret first, then the vault by id.

    The env-name path runs first so an explicit ``OPENAI_API_KEY`` still wins, as it
    always did. Never raises — a missing key disables one media stage, nothing more.
    """
    try:
        from navig.vault.resolver import resolve_secret  # noqa: PLC0415

        found = resolve_secret(list(env_names))
    except Exception:  # noqa: BLE001
        found = next((os.environ[k] for k in env_names if os.environ.get(k)), None)
    if found:
        return found
    try:
        from navig.vault import get_vault  # noqa: PLC0415

        key = get_vault().get_api_key(provider, caller="media_engine")
    except Exception:  # noqa: BLE001 — a locked or absent vault is not an error here
        logger.debug("media engine: vault lookup for %s failed", provider, exc_info=True)
        return None
    key = (str(key) if key is not None else "").strip()
    return key or None
