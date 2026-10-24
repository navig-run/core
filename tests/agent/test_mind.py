"""Tests for NAVIG MIND — the personal cognitive/language preference layer."""

from __future__ import annotations

from navig.mind import (
    ARTIFACT_LANGUAGE_FIREWALL,
    DigitalGhostConfig,
    LexiconConfig,
    MindConfig,
    get_effective_mind,
    mind_prompt_context,
    resolve_mind,
)


def test_defaults_match_the_spec() -> None:
    m = MindConfig()
    assert m.enabled is True
    assert m.conversation_language == "auto"
    assert m.creative_language == "ru"
    assert m.engineering_language == "en"
    assert m.commentary_language == "ru"
    assert m.lexicon.enabled is True and m.lexicon.density == "balanced"
    assert m.digitalghost.enabled is True and m.digitalghost.frequency == "balanced"


def test_artifact_firewall_is_never_disableable() -> None:
    # Even a hostile config that names the firewall must not turn it off.
    m = MindConfig.from_dict({"artifact_language_firewall": False, "enabled": True})
    assert m.to_dict()["artifact_language_firewall"] is True
    assert ARTIFACT_LANGUAGE_FIREWALL is True


def test_resolve_hierarchy_session_over_project_over_global() -> None:
    m = resolve_mind(
        global_mind={"creative_language": "ru", "lexicon": {"density": "minimal"}},
        project_mind={"commentary_language": "fr"},
        session_override={"lexicon": {"density": "intensive"}},
    )
    assert m.creative_language == "ru"  # global survives
    assert m.commentary_language == "fr"  # project wins over default
    assert m.lexicon.density == "intensive"  # session wins over global


def test_resolve_normalizes_invalid_values() -> None:
    m = resolve_mind(
        global_mind={
            "creative_language": "xx",
            "lexicon": {"density": "nonsense"},
            "digitalghost": {"frequency": "nonsense"},
        }
    )
    assert m.creative_language == "ru"  # falls back to default
    assert m.lexicon.density == "balanced"
    assert m.digitalghost.frequency == "balanced"


def test_prompt_block_disabled_returns_empty() -> None:
    assert mind_prompt_context(MindConfig(enabled=False)) == ""


def test_prompt_block_carries_the_contract() -> None:
    block = mind_prompt_context(MindConfig())
    assert "<mind>" in block and "</mind>" in block
    # Language defaults + firewall + DIGITALGHOST + Lexicon are all present.
    assert "creative exploration is in ru" in block
    assert "engineering and technical artifacts are in en" in block
    assert "Artifact-language firewall" in block
    assert "DIGITALGHOST is your independent editorial voice" in block
    assert "Lexicon" in block


def test_prompt_block_respects_sub_toggles() -> None:
    m = MindConfig(
        lexicon=LexiconConfig(enabled=False),
        digitalghost=DigitalGhostConfig(enabled=False),
    )
    block = mind_prompt_context(m)
    assert "Lexicon" not in block
    assert "DIGITALGHOST is your independent editorial voice" not in block
    # Firewall still present even with both subs disabled.
    assert "Artifact-language firewall" in block


def test_get_effective_mind_never_raises() -> None:
    # Must resolve (to defaults) even with no config written at all.
    m = get_effective_mind()
    assert isinstance(m, MindConfig)
    assert m.to_dict()["artifact_language_firewall"] is True


def test_retrospective_mode_adds_recall_grounding() -> None:
    m = MindConfig(digitalghost=DigitalGhostConfig(enabled=True, frequency="retrospective"))
    block = mind_prompt_context(m)
    assert "navig recall" in block
    assert "never an invented progress percentage" in block


def test_balanced_mode_omits_recall_grounding() -> None:
    m = MindConfig(digitalghost=DigitalGhostConfig(enabled=True, frequency="balanced"))
    block = mind_prompt_context(m)
    assert "navig recall" not in block


def test_commentary_distinguishes_interpretation_from_fact() -> None:
    block = mind_prompt_context(MindConfig())
    assert "Distinguish interpretation from verified fact" in block


def test_lore_creative_terminology_section() -> None:
    block = mind_prompt_context(MindConfig())
    assert "Lore & creative terminology" in block
    assert "not automated localization" in block

