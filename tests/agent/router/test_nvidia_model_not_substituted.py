"""An explicitly-configured NVIDIA model must reach the provider unchanged.

``RoutingConfig.from_dict`` substitutes a slot's model when it is absent from the
provider manifest, to rescue hallucinated ids written by auto-onboarding. That is
only safe when the manifest is an ALLOWLIST. For NVIDIA NIM it is not: the
catalog rotates and the manifest rots.

Measured 2026-09-08 on the operator's install — NIM served 81 models while the
manifest listed 20, and a 1-token call to each of those 20 failed. So a slot the
operator had just pointed at a live model was "corrected" to the manifest's first
entry, which was itself 410 GONE. ``navig mode route set`` printed "✓ Updated"
and the value never took effect.
"""

from __future__ import annotations

from navig.agent.model_router import RoutingConfig
from navig.providers.registry import get_provider

# Deliberately not in the manifest — that is the whole point.
UNLISTED = "nvidia/some-model-shipped-after-this-list-was-written"


def _routing(provider: str, model: str) -> dict:
    return {
        "enabled": True,
        "mode": "rules_then_fallback",
        "models": {
            "small": {"provider": provider, "model": model},
            "big": {"provider": provider, "model": model},
            "coder_big": {"provider": provider, "model": model},
        },
    }


def test_unlisted_nvidia_model_survives_config_load():
    assert UNLISTED not in list(get_provider("nvidia").models), "fixture must be unlisted"

    cfg = RoutingConfig.from_dict(_routing("nvidia", UNLISTED))

    for tier, slot in (("small", cfg.small), ("big", cfg.big), ("coder_big", cfg.coder_big)):
        assert slot.model == UNLISTED, f"{tier} was silently substituted"


def test_every_shipped_nvidia_model_is_a_plausible_id():
    """A vacuity floor plus a guard against re-listing a known-dead family.

    The four ``meta/llama-3.1-*`` ids reached end-of-life on 2026-08-26 and were
    the ones that broke this install; the list must not regrow them.
    """
    models = list(get_provider("nvidia").models)
    assert len(models) >= 3, "manifest emptied — the substitution default comes from here"
    assert all("/" in m for m in models), "NIM ids are namespaced"
    assert not [m for m in models if m.startswith("meta/llama-3.1-")], "EOL 2026-08-26"


def test_substitution_still_applies_to_a_fixed_catalog_provider():
    """The rescue must keep working where the manifest really is an allowlist.

    Without this, exempting nvidia could have disabled the guard everywhere and
    nothing would have said so. groq is a genuine fixed catalog.
    """
    listed = list(get_provider("groq").models)
    assert listed, "fixture provider must list models, or this proves nothing"

    bogus = "hallucinated/not-a-real-groq-model"
    cfg = RoutingConfig.from_dict(_routing("groq", bogus))

    assert cfg.small.model != bogus, "substitution stopped working for fixed catalogs"
    assert cfg.small.model in listed


# ── the SECOND router had the same defect ────────────────────────────────
#
# `navig/llm/routing/router.py` applies `llm_modes` at ROUTING time and used to
# clear any configured model absent from the manifest — with no open-catalog
# exemption. So an operator who set a live nvidia model not in the 7-entry
# manifest had it silently replaced at dial time, while `navig mode doctor`
# probed the CONFIGURED model and reported it live. A divergence between what is
# probed and what is used, invisible to the tool built to catch exactly that.


def test_an_unlisted_open_catalog_model_is_not_rejected():
    from navig.agent.model_router import manifest_rejects

    assert UNLISTED not in list(get_provider("nvidia").models), "fixture must be unlisted"
    assert manifest_rejects("nvidia", UNLISTED) is False, "operator's choice was rejected"


def test_a_stale_fixed_catalog_id_is_still_rejected():
    """The guard exists for a reason — a pre-update id 400s through every fallback."""
    from navig.agent.model_router import manifest_rejects

    assert manifest_rejects("groq", "hallucinated/not-a-real-groq-model") is True


def test_both_routers_make_the_decision_in_one_place():
    """Two copies of the DECISION would drift the way the two taxonomies did
    (#1439). The remediations legitimately differ — one substitutes a default,
    one clears and lets `_execute` choose — so only the decision is shared."""
    import inspect

    from navig.llm.routing import router as routing_router

    src = inspect.getsource(routing_router)
    assert "from navig.agent.model_router import manifest_rejects" in src, (
        "the routing router must IMPORT the decision, not carry its own copy"
    )
    assert "def _manifest_rejects" not in src, "a local copy of the decision came back"
