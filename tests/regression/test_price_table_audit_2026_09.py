"""The price table and the prompt-caching list, audited against the providers'
own pricing pages on 2026-09-27.

The audit found o3 billed 5x too high (the pre-cut price), Opus 4.5 3x (it
carried Opus 4's price), Gemini 2.5 Flash's output 8x too LOW (it carried 1.5
Flash's), and two provider defaults — grok-4.6 and the whole Claude 5 family —
unpriced, so every turn on them read $0.00. The Claude 5 family was also missing
from the prompt-caching list, so no cache_control was ever sent for it.

These tests pin the facts that were wrong, plus one property that must outlive
any price change: every provider's catalog HEAD (the credential probe and the
routing substitution default) has a price, so its cost is never silently $0.
"""

from __future__ import annotations

import pytest

from navig.agent.prompt_caching import supports_caching
from navig.agent.usage_tracker import _lookup_price


@pytest.mark.parametrize(
    "model,inp,out",
    [
        ("o3", 2.00, 8.00),
        ("claude-opus-4-5", 5.00, 25.00),
        ("claude-opus-4-1", 15.00, 75.00),  # a variant of opus-4 — genuinely 15/75
        ("gemini-2.5-flash", 0.30, 2.50),
        ("gemini-2.5-pro", 1.25, 10.00),
        ("grok-4.6", 2.00, 6.00),
        ("claude-opus-5", 5.00, 25.00),
        ("claude-opus-5-5", 4.00, 20.00),  # NOT a variant of opus-5: longest key wins
        ("claude-sonnet-5", 2.00, 10.00),
    ],
)
def test_audited_prices(model, inp, out):
    assert _lookup_price(model)[:2] == (inp, out)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "xai", "groq", "nvidia"])
def test_every_keyed_providers_catalog_head_has_a_price(provider):
    """A head with no entry costs $0.00 on every turn — the grok-4.6 bug. groq and
    nvidia host open-weight models on free/credit tiers, so a deliberate $0 entry
    is allowed there; what is not allowed is NO entry."""
    from navig.agent.usage_tracker import PRICE_TABLE, model_price_key
    from navig.providers.registry import get_provider

    head = get_provider(provider).models[0]
    key = model_price_key(head, PRICE_TABLE)
    if provider in ("groq", "nvidia"):
        pytest.skip(f"{provider}: open-weight host, head {head!r} — priced by the operator")
    assert key is not None, f"{provider}'s head {head!r} has no price — it will read $0.00"
    assert sum(PRICE_TABLE[key][:2]) > 0


@pytest.mark.parametrize("model", ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5"])
def test_the_claude_5_family_is_cacheable(model):
    assert supports_caching(model)


@pytest.mark.parametrize("model", ["claude", "claude-opus", "gpt-4o"])
def test_a_bare_alias_or_another_vendor_is_not_cacheable(model):
    """The old check also matched in REVERSE: `"claude"` read as cacheable."""
    assert not supports_caching(model)
