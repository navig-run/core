"""The official Xiaomi MiMo provider — manifest, config, and endpoint shape.

Audited live 2026-10-13 through api.xiaomimimo.com (Anthropic-shape
/anthropic/v1/messages) on the operator's vault key. OpenRouter's endpoint
pricing shows Xiaomi's native route at the SAME price as OpenRouter charges
(flash $0.14/$0.28, pro $0.435/$0.87 per M), so this provider buys latency
stability — one fleet instead of six load-balanced routes — not discount.
Measured same-day: flash official 5.9/4.1/5.4s vs OpenRouter 8.2/4.5/12.1s.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

MIMO_IDS = ("mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed")


def test_manifest_exists_with_audited_models():
    from navig.providers.registry import get_provider

    man = get_provider("mimo")
    assert man is not None, "mimo manifest missing"
    for mid in MIMO_IDS:
        assert mid in man.models, f"{mid} missing from manifest"
    # models[0] is the credential probe AND the substitution head.
    assert man.models[0] == "mimo-v2.6-flash"
    assert man.tier == "cloud"
    assert "MIMO_API_KEY" in man.env_vars


def test_builtin_config_is_anthropic_messages_on_the_official_base_url():
    from navig.providers.clients import get_builtin_provider
    from navig.providers.types import ModelApi

    cfg = get_builtin_provider("mimo")
    assert cfg is not None, "mimo builtin config missing"
    assert cfg.base_url == "https://api.xiaomimimo.com/anthropic"
    # The official path is Anthropic-shape (/anthropic/v1/messages) — verified
    # working 2026-10-13 with the vault key; mistyping this enum would send
    # OpenAI-shape bodies to an Anthropic endpoint.
    assert cfg.api == ModelApi.ANTHROPIC_MESSAGES


def test_builtin_models_carry_real_pricing_and_1m_context():
    from navig.providers.clients import get_builtin_provider

    cfg = get_builtin_provider("mimo")
    by_id = {m.id: m for m in cfg.models}
    assert set(MIMO_IDS) <= set(by_id)
    flash = by_id["mimo-v2.6-flash"]
    assert flash.context_window == 1_050_000
    assert flash.cost and flash.cost.input == 0.14 and flash.cost.output == 0.28
    pro = by_id["mimo-v2.6-pro"]
    assert pro.cost and pro.cost.input == 0.435 and pro.cost.output == 0.87


def test_the_official_ids_carry_no_openrouter_prefix():
    """The official endpoint takes bare ids (mimo-v2.6-pro); the openrouter
    prefix (xiaomi/mimo-v2.6-pro) is OpenRouter's namespace. Crossing them
    404s both ways."""
    from navig.providers.registry import get_provider

    man = get_provider("mimo")
    assert man is not None
    assert not any(mid.startswith("xiaomi/") for mid in man.models)