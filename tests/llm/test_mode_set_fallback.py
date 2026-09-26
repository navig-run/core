"""A mode's FALLBACK is settable from the CLI.

It is what answers when the primary cannot, and it had no CLI at all — repointing
one meant hand-editing `llm_router.llm_modes.<mode>.fallback_*` in config.yaml.
That mattered once `navig mode doctor` started probing fallbacks: it could show
you a dead one and give you no supported way to fix it. Measured on a real
install, three fallbacks pointed at an ollama that was not running.
"""

from __future__ import annotations

import inspect

import pytest

from navig.commands.mode import mode_set
from navig.llm.router import CANONICAL_MODES, LLMModeRouter


@pytest.fixture
def router():
    return LLMModeRouter({})


def test_a_fallback_can_be_repointed(router):
    assert router.update_mode("coding", fallback_provider="xai", fallback_model="grok-3-mini")

    cfg = router.modes.get_mode("coding")
    assert cfg.fallback_provider == "xai"
    assert cfg.fallback_model == "grok-3-mini"


def test_the_provider_is_normalised_like_the_primary(router):
    """Raw attribute assignment skips the pydantic validators, and a value that
    fails `model_validate` on the next load silently resets the whole llm_router
    block — so the fallback must be normalised exactly like the primary."""
    router.update_mode("coding", fallback_provider="  XAI  ", fallback_model="  grok-3-mini  ")

    cfg = router.modes.get_mode("coding")
    assert cfg.fallback_provider == "xai"
    assert cfg.fallback_model == "grok-3-mini"


def test_an_empty_provider_is_stored_not_discarded(router):
    """Empty means "same provider as the primary" — a real setting, not a no-op."""
    router.update_mode("coding", fallback_provider="xai", fallback_model="m")
    router.update_mode("coding", fallback_provider="")

    assert router.modes.get_mode("coding").fallback_provider == ""


def test_setting_only_the_fallback_leaves_the_primary_alone(router):
    """The commonest use: the primary is fine, the safety net is dead."""
    before = router.modes.get_mode("coding")
    primary = (before.provider, before.model)

    router.update_mode("coding", fallback_provider="nvidia", fallback_model="some/model")

    after = router.modes.get_mode("coding")
    assert (after.provider, after.model) == primary


def test_an_unknown_name_resolves_to_big_tasks_at_the_ROUTER_level(router):
    """Documents why the CLI has to validate separately.

    `resolve_mode` sends ANY unrecognised hint to `big_tasks` — correct when
    ROUTING a message, destructive when picking a config row to overwrite. So
    `update_mode` happily accepts a typo and writes into big_tasks, and
    `mode_set`'s own "Unknown mode" branch could never fire.
    """
    assert router.resolve_mode("not-a-mode") == "big_tasks"

    router.update_mode(router.resolve_mode("codingg"), fallback_model="TYPO")

    assert router.modes.get_mode("big_tasks").fallback_model == "TYPO"
    assert router.modes.get_mode("coding").fallback_model != "TYPO"


def test_the_cli_rejects_a_typo_before_it_writes():
    """`navig mode set codingg --model X` must not repoint the heaviest mode."""
    src = inspect.getsource(mode_set)

    assert "typed not in CANONICAL_MODES and typed not in MODE_ALIASES" in src, (
        "the write path no longer validates the mode name"
    )
    # The guard must come BEFORE the update, or it guards nothing.
    assert src.index("Unknown mode") < src.index("router.update_mode(")


@pytest.mark.parametrize("mode", sorted(CANONICAL_MODES))
def test_every_canonical_mode_accepts_a_fallback(router, mode):
    assert router.update_mode(mode, fallback_provider="xai", fallback_model="grok-3-mini")


def test_the_cli_exposes_both_options():
    """A parameter that never reaches update_mode is a flag that does nothing."""
    params = inspect.signature(mode_set).parameters

    assert "fallback_provider" in params
    assert "fallback_model" in params


def test_the_cli_forwards_them_to_update_mode():
    """Pin the wiring, not just the flags — the 'declared but never passed' class."""
    src = inspect.getsource(mode_set)

    assert "fallback_provider=fallback_provider" in src
    assert "fallback_model=fallback_model" in src


# ── how the fallback is displayed ────────────────────────────────────────


class _Cfg:
    def __init__(self, provider="", model="", fallback_provider="", fallback_model=""):
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model


def test_a_cross_provider_fallback_shows_its_provider():
    """`grok-3-mini` alone does not say xai — and every fallback here is
    deliberately on a different provider, so the model name is ambiguous."""
    from navig.commands.mode import _fallback_label

    label = _fallback_label(_Cfg("nvidia", "some/model", "xai", "grok-3-mini"))

    assert label == "xai:grok-3-mini"


def test_a_same_provider_fallback_is_not_prefixed():
    """No point repeating the provider it already shares with the primary."""
    from navig.commands.mode import _fallback_label

    assert _fallback_label(_Cfg("openai", "gpt-4.1", "openai", "gpt-4o")) == "gpt-4o"


def test_an_empty_fallback_provider_means_same_as_primary():
    from navig.commands.mode import _fallback_label

    assert _fallback_label(_Cfg("openai", "gpt-4.1", "", "gpt-4o")) == "gpt-4o"


def test_no_fallback_renders_a_dash():
    from navig.commands.mode import _fallback_label

    assert "—" in _fallback_label(_Cfg("openai", "gpt-4.1", "", ""))
