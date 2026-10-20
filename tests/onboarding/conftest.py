"""Onboarding tests run against a FRESH machine — they must not see the operator's keys.

The onboarding steps deliberately scan the environment for provider keys (the
``importedFromEnv`` fast path: a developer with ``ANTHROPIC_API_KEY`` set gets it
picked up without a prompt — correct product behaviour). The tests simulate a
fresh install, so an ambient key on the machine running pytest flipped their
assertions: ``assert 'completed' == 'skipped'`` and phantom
``_retroactiveUpdates`` entries, failing only on developer boxes and never in a
keyless CI (measured 2026-10-14: 3 failures with keys set, 8/8 pass with them
cleared).

The strip list is derived from the product's own sources — ``PROVIDER_ENV_KEYS``
and ``list_all_providers()`` — so a new provider is covered the day it ships,
never by a second copy of the list here. A test that WANTS a key sets it with
``monkeypatch.setenv`` after this fixture runs, which is the existing pattern.
"""

from __future__ import annotations

import os

import pytest


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
    # The scanner's other ambient inputs: which provider to prefer, and the
    # web-search key gate that marks a step configured "from environment".
    keys.update({"NAVIG_LLM_PROVIDER", "NAVIG_WEB_SEARCH_PROVIDER", "NAVIG_WEB_SEARCH_API_KEY"})
    return keys


@pytest.fixture(autouse=True)
def no_ambient_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every scanner-visible credential before each test in this directory."""
    for key in _ambient_provider_env_keys():
        monkeypatch.delenv(key, raising=False)


def test_the_strip_list_covers_the_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a scanner-visible key must be one the fixture strips.

    Fails on the old behaviour by construction — the list is derived from the
    same modules the onboarding steps read, so it cannot drift away from them.
    """
    from navig.providers.source_scan import PROVIDER_ENV_KEYS, provider_env_vars

    keys = _ambient_provider_env_keys()
    assert keys, "derived an empty strip list — the guard would be vacuous"
    # every var of every provider the fallback map knows must be covered
    for names in PROVIDER_ENV_KEYS.values():
        for name in names:
            assert name in keys, f"{name} is in PROVIDER_ENV_KEYS but not stripped"
    # and the registry path too: ask the scanner for a known provider
    sample = provider_env_vars(next(iter(PROVIDER_ENV_KEYS)))
    assert sample and set(sample) <= keys
    # the fixture itself removes an injected ambient key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-canary")
    for key in _ambient_provider_env_keys():
        monkeypatch.delenv(key, raising=False)
    assert "ANTHROPIC_API_KEY" not in os.environ
