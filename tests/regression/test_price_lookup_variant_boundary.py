"""A price belongs to the model it names — and to that model's variants only.

Both cost trackers matched a model to a price with a bare ``startswith``. Since
``"gpt-4.1".startswith("gpt-4")`` is True, the whole GPT-4.1 family — including
``gpt-4.1`` itself, OpenAI's catalog head (the credential probe and the routing
substitution default), and ``gpt-4.1-nano``, the cheapest OpenAI model — was
billed at classic GPT-4's $30/$60 per M: roughly 15x too high on every turn.

The per-turn tracker also took the FIRST prefix in dict order (its table carried
a comment that exact entries "must precede" a shorter prefix — a rule written
down because nothing enforced it) and matched in REVERSE (``"gpt-4"`` priced as
``"gpt-4o"``).
"""

from __future__ import annotations

import pytest

from navig.agent.usage_tracker import PRICE_TABLE, _lookup_price, model_price_key
from navig.cost_tracker import SessionCostTracker


@pytest.mark.parametrize(
    "model,expected_in,expected_out",
    [("gpt-4.1", 2.00, 8.00), ("gpt-4.1-mini", 0.40, 1.60), ("gpt-4.1-nano", 0.10, 0.40)],
)
def test_the_gpt_4_1_family_is_not_priced_as_classic_gpt_4(model, expected_in, expected_out):
    inp, out, _, _ = _lookup_price(model)
    assert (inp, out) == (expected_in, expected_out)
    assert (inp, out) != PRICE_TABLE["gpt-4"][:2]


@pytest.mark.parametrize(
    "model,key",
    [
        ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20241022"),  # exact
        ("gemini-2.5-pro-preview-05-06", "gemini-2.5-pro"),             # preview suffix
        ("grok-3-mini-fast", "grok-3-mini"),                            # longest, not grok-3
        ("gpt-4-turbo-preview", "gpt-4-turbo"),                         # longest, not gpt-4
        ("claude-sonnet-4@20250514", "claude-sonnet-4"),                # vertex version
        ("gpt-4o-2024-11-20", "gpt-4o"),                                # dated snapshot
    ],
)
def test_a_variant_inherits_its_base_price(model, key):
    assert model_price_key(model, PRICE_TABLE) == key


@pytest.mark.parametrize("model", ["gpt-4.5-preview", "gpt-4", "gemini-2.55", "o"])
def test_a_version_continuation_or_a_shorter_name_is_not_a_variant(model):
    keys = {"gpt-4", "gemini-2.5", "o1"} - {model}
    assert model_price_key(model, keys) is None


def test_the_longest_key_wins_whatever_the_declaration_order():
    for keys in (["claude-opus-4", "claude-opus-4-8"], ["claude-opus-4-8", "claude-opus-4"]):
        assert model_price_key("claude-opus-4-8-20260101", keys) == "claude-opus-4-8"


def test_navig_cost_uses_the_same_rule():
    """`navig cost` prices from an operator-written table. An operator's "gpt-4"
    entry must not bill their gpt-4.1 usage at GPT-4 rates either."""
    tracker = SessionCostTracker(
        session_id="t",
        config={
            "enabled": True, "persist": False, "history_keep": 1,
            "model_pricing": {
                "gpt-4": {"input": 30.0, "output": 60.0},
                "gpt-4.1": {"input": 2.0, "output": 8.0},
                "default": {"input": 0.0, "output": 0.0},
            },
        },
    )
    assert tracker._get_pricing("gpt-4.1-2025-04-14")["input"] == 2.0
    # A SIZE suffix names a different, cheaper model: with only "gpt-4.1" in the
    # table, gpt-4.1-nano must fall to the default rather than borrow 4.1's price.
    # (This line used to assert the opposite — it encoded the over-billing.)
    assert tracker._get_pricing("gpt-4.1-nano")["input"] == 0.0
    assert tracker._get_pricing("gpt-4.5")["input"] == 0.0  # not a variant of gpt-4: default
    assert tracker._get_pricing("gpt-4-0613")["input"] == 30.0


@pytest.mark.parametrize("model", ["gpt-4.5-preview", "claude-opus", "grok"])
def test_the_per_turn_tracker_does_not_borrow_another_models_price(model):
    """Through `_lookup_price` itself, so the MATCHER is under test, not the
    table: `gpt-4.5-preview` used to inherit classic gpt-4 ($30/$60), and a bare
    alias like `claude-opus` REVERSE-matched whichever claude-opus key came
    first. An unknown model reports $0 and says so at debug level."""
    assert _lookup_price(model) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "model", ["gpt-5-nano", "gemini-2.5-flash-lite", "claude-opus-4-8-mini", "grok-3-tiny"]
)
def test_a_size_suffix_is_a_different_model_not_a_variant(model):
    """`gemini-2.5-flash-lite` is $0.10/$0.40 against Flash's $0.30/$2.50;
    inheriting the bigger model's price over-bills it several times over."""
    assert model_price_key(model, PRICE_TABLE) is None


@pytest.mark.parametrize(
    "model,key",
    [("gpt-4o-mini-2024-07-18", "gpt-4o-mini"), ("grok-3-mini-fast", "grok-3-mini"),
     ("gpt-5-2025-08-07", "gpt-5")],
)
def test_a_size_word_INSIDE_the_key_still_takes_variants(model, key):
    assert model_price_key(model, PRICE_TABLE) == key
