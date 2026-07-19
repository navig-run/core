"""
Regression: LLMModeRouter.update_mode must REJECT out-of-range temperature /
max_tokens instead of assigning them raw.

Raw attribute assignment bypasses LLMModeConfig's pydantic Field(ge/le)
constraints (no validate_assignment). A persisted out-of-range value then fails
model_validate on the NEXT load, which resets the ENTIRE llm_router block to
defaults — silently wiping every mode's config. update_mode is the choke point,
so it validates ranges and normalizes the provider.
"""

from __future__ import annotations

import pytest

from navig.llm.router import LLMModeRouter


def _router():
    return LLMModeRouter(config={})  # code defaults, no user config


@pytest.mark.parametrize("kwargs", [
    {"max_tokens": 200000},   # over the 131072 ceiling
    {"max_tokens": 0},        # under 1
    {"temperature": 3.0},     # over 2.0
    {"temperature": -0.5},    # under 0.0
])
def test_update_mode_rejects_out_of_range(kwargs):
    r = _router()
    before = r.modes.get_mode("big_tasks").model_dump()
    with pytest.raises(ValueError):
        r.update_mode("big_tasks", **kwargs)
    # the mode is left untouched (no partial mutation persisted)
    assert r.modes.get_mode("big_tasks").model_dump() == before


def test_update_mode_survives_reload_after_valid_edit():
    r = _router()
    assert r.update_mode("big_tasks", provider="Anthropic",
                         model="claude-opus-4-8", max_tokens=8000, temperature=0.3)
    # provider normalized to lowercase (raw assign skips validate_provider)
    assert r.modes.get_mode("big_tasks").provider == "anthropic"

    # persist → reload must NOT wipe the block (model_validate must pass)
    raw = {"llm_router": {"llm_modes": r.get_all_modes()}}
    r2 = LLMModeRouter(config=raw)
    bt = r2.modes.get_mode("big_tasks")
    assert (bt.provider, bt.model, bt.max_tokens) == ("anthropic", "claude-opus-4-8", 8000)


def test_update_mode_boundaries_accepted():
    r = _router()
    assert r.update_mode("coding", temperature=0.0, max_tokens=1)
    assert r.update_mode("coding", temperature=2.0, max_tokens=131072)
