"""Onboarding tests run against a FRESH machine — they must not see the operator's keys.

The onboarding steps deliberately scan the environment for provider keys (the
``importedFromEnv`` fast path: a developer with ``ANTHROPIC_API_KEY`` set gets it
picked up without a prompt — correct product behaviour). The tests simulate a
fresh install, so an ambient key on the machine running pytest flipped their
assertions: ``assert 'completed' == 'skipped'`` and phantom
``_retroactiveUpdates`` entries, failing only on developer boxes and never in a
keyless CI (measured 2026-10-14: 3 failures with keys set, 8/8 pass with them
cleared).

The strip list is derived from the product's own sources — ``PROVIDER_ENV_KEYS``,
``list_all_providers()`` and the web-search, voice and runtime-secrets catalogs in
``navig.onboarding.steps`` — so a new provider is covered the day it ships,
never by a second copy of the list here. A test that WANTS a key sets it with
``monkeypatch.setenv`` after this fixture runs, which is the existing pattern.

The derivation lives in ``_env_strip.py`` and its regression check in
``test_onboarding_env_strip.py`` — pytest never collects tests from a conftest.
"""

from __future__ import annotations

import pytest

from tests.onboarding._env_strip import _ambient_provider_env_keys


@pytest.fixture(autouse=True)
def no_ambient_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every scanner-visible credential before each test in this directory."""
    for key in _ambient_provider_env_keys():
        monkeypatch.delenv(key, raising=False)
