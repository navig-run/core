"""navig could not call ANY OpenAI reasoning model (o-series, gpt-5).

They reject two parameters the rest of the API requires, and both are hard 400s.
Measured 2026-09-08 against the live API:

    max_tokens=1                          -> "'max_tokens' is not supported with
                                              this model. Use
                                              'max_completion_tokens' instead."
    max_completion_tokens=64, temp=0.7    -> "'temperature' does not support 0.7
                                              with this model."
    max_completion_tokens=64, no temp     -> LIVE

⚠ Fixing only the first gets halfway and still 400s.

Four o-series ids ship in the openai manifest, so they were selectable and
unusable, and `navig mode doctor` reported them as "error" while they were live.
"""

from __future__ import annotations

import pytest

from navig.llm.liveness import hit_output_limit, probe_model
from navig.providers.clients import _sanitize_openai_body

REASONING = ["o1", "o3", "o3-mini", "o4-mini", "gpt-5", "gpt-5-mini"]
CLASSIC = ["gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4.1-nano"]


def _body(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0.7,
        "max_tokens": 16,
        "stream": False,
    }


@pytest.mark.parametrize("model", REASONING)
def test_reasoning_models_get_the_parameters_they_accept(model):
    out = _sanitize_openai_body(_body(model), provider_name="openai")

    assert "max_tokens" not in out, "max_tokens is a hard 400 on this model"
    assert out["max_completion_tokens"] == 16, "the budget must survive the rename"
    assert "temperature" not in out, "any explicit temperature is a hard 400"


@pytest.mark.parametrize("model", CLASSIC)
def test_classic_models_are_left_alone(model):
    """The rename is per-MODEL, not per-provider — both shapes exist on openai."""
    out = _sanitize_openai_body(_body(model), provider_name="openai")

    assert out["max_tokens"] == 16
    assert "max_completion_tokens" not in out
    assert out["temperature"] == 0.7


@pytest.mark.parametrize("provider", ["openrouter", "nvidia", "groq"])
def test_other_providers_are_untouched(provider):
    """OpenRouter serves `openai/gpt-5` and accepts the ordinary shape."""
    out = _sanitize_openai_body(_body("openai/gpt-5"), provider_name=provider)

    assert "max_tokens" in out, f"{provider} body was rewritten"
    assert "max_completion_tokens" not in out


# ── the probe ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Could not finish the message because max_tokens or model output limit was reached.",
        "output limit was reached. Please try again with higher max_tokens.",
    ],
)
def test_hitting_the_output_cap_is_proof_of_life(message):
    assert hit_output_limit(Exception(message))


@pytest.mark.parametrize(
    "message",
    [
        "Client error '401 Unauthorized'",
        "model reached its end of life",
        "Server error '503 Service Unavailable'",
        "connection refused",
    ],
)
def test_a_real_failure_is_not_mistaken_for_a_budget_cap(message):
    """Anti-over-suppression floor — this must not turn failures into 'live'."""
    assert not hit_output_limit(Exception(message))


def test_probe_reports_live_when_only_the_budget_was_too_small(monkeypatch):
    """A reasoning model spends tokens thinking, so a 1-token probe ALWAYS caps.

    Raising the probe budget would fix today's models and rot for tomorrow's;
    reading the provider's own "you hit the cap" as success cannot.
    """
    import navig.llm.generate as gen

    def boom(*_a, **_kw):
        raise RuntimeError(
            "[openai] Could not finish the message because max_tokens or model "
            "output limit was reached. Please try again with higher max_tokens. (status=400)"
        )

    monkeypatch.setattr(gen, "llm_generate", boom)

    status, detail = probe_model("openai", "o3")

    assert status == "live", f"a live reasoning model reported as {status}"
    assert "budget" in detail
