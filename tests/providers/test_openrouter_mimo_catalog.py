"""Xiaomi MiMo V2.6 in the OpenRouter catalog — pinned where it was audited.

Audited live 2026-10-13: all three ids answered a 1-token call on the
operator's key (flash 3.9s, pro 2.1s, ultraspeed 11.3s). The manifest feeds
substitution and credential probes; the BUILTIN table feeds `navig ai
models`, pricing and context. Both must keep the entries, or the picker goes
quiet on models the audit proved live.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

MIMO_IDS = (
    "xiaomi/mimo-v2.6-flash",
    "xiaomi/mimo-v2.6-pro",
    "xiaomi/mimo-v2.6-pro-ultraspeed",
)


def test_openrouter_manifest_lists_the_audited_mimo_ids():
    from navig.providers.registry import get_provider

    man = get_provider("openrouter")
    assert man is not None
    for mid in MIMO_IDS:
        assert mid in man.models, f"{mid} missing from the openrouter manifest"


def test_openrouter_builtin_table_describes_each_mimo_model():
    from navig.providers.types import BUILTIN_PROVIDERS

    models = {m.id: m for m in BUILTIN_PROVIDERS["openrouter"].models}
    for mid in MIMO_IDS:
        assert mid in models, f"{mid} missing from the builtin table"
        assert models[mid].context_window >= 1_000_000, f"{mid} lost its 1M context"
        assert (models[mid].cost or None) is not None, f"{mid} has no pricing"
        assert models[mid].cost.input > 0 and models[mid].cost.output > 0


def test_the_mimo_models_are_not_accidentally_the_substitution_head():
    """`models[0]` is the credential probe AND the substitution default — it
    must stay the operator's proven head, not shift because an audit appended
    rows."""
    from navig.providers.registry import get_provider

    man = get_provider("openrouter")
    assert man is not None
    assert man.models[0] not in MIMO_IDS