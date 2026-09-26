"""GitHub Models is a real provider in every surface, not only in the dispatcher.

``navig.providers.registry`` has listed ``github_models`` as an enabled cloud
provider (15 models, priority #2 in ``ai_client``'s auto-detection) while
``BUILTIN_PROVIDERS`` — the table behind ``navig ai models`` / ``navig ai
providers`` / ``--test`` and the fallback manager's client factory — had no
entry. The verifier flagged it on every Telegram ``/providers`` screen and every
onboarding run, and the docstring's "run ``verify_all_providers()`` and confirm
zero failures" was enforced by nothing: the only test asserted ``hasattr(r, "ok")``.

The recorded reason for leaving it was "needs real context-window metadata".
Measured: ``ModelDefinition.context_window`` / ``max_tokens`` are read by exactly
one consumer outside their definition — the ``navig ai models`` display table.
"""

from __future__ import annotations

import logging

import pytest

from navig.providers.registry import ALL_PROVIDERS, get_provider
from navig.providers.types import BUILTIN_PROVIDERS
from navig.providers.verifier import _is_soft_issue, verify_provider


def _structural_issues(manifest) -> list[str]:
    logging.disable(logging.CRITICAL)
    try:
        return [i for i in verify_provider(manifest).issues if not _is_soft_issue(i)]
    finally:
        logging.disable(logging.NOTSET)


def test_every_enabled_provider_passes_structural_verification():
    """Factory + ProviderConfig presence are properties of the code, not of the
    machine — a red here is a provider half-registered, on every install."""
    enabled = [m for m in ALL_PROVIDERS if m.enabled]
    assert len(enabled) >= 5, "registry floor: the scan saw almost nothing"
    broken = {m.id: _structural_issues(m) for m in enabled}
    broken = {k: v for k, v in broken.items() if v}
    assert broken == {}, f"half-registered providers: {broken}"


def test_github_models_config_offers_exactly_the_manifest_models():
    manifest = get_provider("github_models")
    assert manifest is not None and manifest.enabled and manifest.tier == "cloud"
    cfg = BUILTIN_PROVIDERS["github_models"]
    assert [m.id for m in cfg.models] == list(manifest.models)
    assert cfg.base_url == "https://models.inference.ai.azure.com"


def test_github_models_is_last_so_shared_ids_resolve_to_their_native_provider():
    """``fallback.py`` infers a provider from a bare model id by scanning
    ``BUILTIN_PROVIDERS`` in dict order; ``gpt-4o`` must still mean OpenAI."""
    keys = list(BUILTIN_PROVIDERS)
    assert keys[-1] == "github_models"
    from navig.providers.fallback import FallbackManager

    fm = FallbackManager.__new__(FallbackManager)
    fm.providers = BUILTIN_PROVIDERS.copy()
    fm._cooldowns = {}
    fm._clients = {}
    chain = fm.resolve_candidates("phi-4")
    assert chain and chain[0].provider_name == "github_models"
    chain = fm.resolve_candidates("gpt-4o")
    assert chain and chain[0].provider_name == "openai"


@pytest.mark.parametrize("model_id", ["phi-4", "deepseek-r1", "meta-llama-3.1-405b-instruct"])
def test_github_only_model_ids_now_resolve_to_a_provider(model_id):
    from navig.providers.fallback import FallbackManager

    fm = FallbackManager.__new__(FallbackManager)
    fm.providers = BUILTIN_PROVIDERS.copy()
    fm._cooldowns = {}
    fm._clients = {}
    chain = fm.resolve_candidates(model_id)
    assert [c.provider_name for c in chain] == ["github_models"]
