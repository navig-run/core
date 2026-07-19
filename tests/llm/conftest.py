"""Shared fixtures for LLM-layer tests.

The runtime fallback policy keeps a **process-global** per-model cooldown map
(``navig.llm.fallback_policy``). Left populated, it leaks between tests — a
cooldown set by a fallback test would make an unrelated dispatch test skip its
primary. Reset it around every test in this package.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_fallback_cooldowns():
    from navig.llm import fallback_policy

    fallback_policy.reset_cooldowns()
    yield
    fallback_policy.reset_cooldowns()
