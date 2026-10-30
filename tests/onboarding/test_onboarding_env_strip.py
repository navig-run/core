"""The onboarding strip list covers every credential the steps read (see ``conftest.py``)."""

from __future__ import annotations

import os

import pytest

from tests.onboarding._env_strip import _ambient_provider_env_keys


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
    # every credential the non-LLM steps read from the environment
    from navig.onboarding import steps

    catalog_vars = {n for _p, _l, names in steps._WEB_SEARCH_PROVIDER_CATALOG for n in names}
    catalog_vars |= {n for _p, _l, _k, names in steps._VOICE_PROVIDER_CATALOG for n in names}
    catalog_vars |= {n for n, _v, _l in (*steps._ENV_KEY_IMPORTS, *steps._AI_RUNTIME_KEY_IMPORTS)}
    assert {"DEEPSEEK_SEARCH_API_KEY", "PERPLEXITY_API_KEY", "SERPAPI_KEY", "DEEPGRAM_API_KEY"} <= catalog_vars
    missing = sorted(catalog_vars - keys)
    assert not missing, f"onboarding catalog vars not stripped: {missing}"
    # and the registry path too: ask the scanner for a known provider
    sample = provider_env_vars(next(iter(PROVIDER_ENV_KEYS)))
    assert sample and set(sample) <= keys
    # the fixture itself removes an injected ambient key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-canary")
    for key in _ambient_provider_env_keys():
        monkeypatch.delenv(key, raising=False)
    assert "ANTHROPIC_API_KEY" not in os.environ
