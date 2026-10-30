"""The env vars the onboarding steps treat as ambient credentials (see ``conftest.py``)."""

from __future__ import annotations


def _ambient_provider_env_keys() -> set[str]:
    """Every env var the onboarding scanner treats as a provider credential."""
    keys: set[str] = set()
    try:
        from navig.providers.source_scan import PROVIDER_ENV_KEYS

        for names in PROVIDER_ENV_KEYS.values():
            keys.update(str(n) for n in names)
    except Exception:  # noqa: BLE001 — a broken registry must not break the guard
        pass
    try:
        from navig.providers.registry import list_all_providers

        for manifest in list_all_providers():
            keys.update(str(v).strip() for v in (manifest.env_vars or []) if str(v).strip())
    except Exception:  # noqa: BLE001
        pass
    # The non-LLM onboarding steps read their own catalogs straight from the
    # environment: web search, voice, and the runtime-secrets import table.
    try:
        from navig.onboarding import steps

        for _pid, _label, names in steps._WEB_SEARCH_PROVIDER_CATALOG:
            keys.update(names)
        for _pid, _label, _kind, names in steps._VOICE_PROVIDER_CATALOG:
            keys.update(names)
        for name, _vault_key, _label in (*steps._ENV_KEY_IMPORTS, *steps._AI_RUNTIME_KEY_IMPORTS):
            keys.add(name)
    except Exception:  # noqa: BLE001
        pass
    # The scanner's other ambient inputs: which provider to prefer, and the
    # web-search key gate that marks a step configured "from environment".
    keys.update({"NAVIG_LLM_PROVIDER", "NAVIG_WEB_SEARCH_PROVIDER", "NAVIG_WEB_SEARCH_API_KEY"})
    return keys
