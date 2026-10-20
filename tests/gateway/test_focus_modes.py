"""The ``/mode`` agent-mode catalog must speak the one vocabulary its consumer validates.

``UserPreferences.set_preference("chat_mode", v)`` returns ``False`` for any ``v`` not in
``UserPreferences._VALID_MODES`` — so a mode the store rejects is a silent no-op. The
retired focus-mode vocabulary (work/deep-focus/planning/creative/relax/sleep) must never
leak back into the offer surface, and no retired phrase may ever resolve to the NEW
capability ``creative`` (owner decision 2026-10-20: the old conversational Creative
migrates to Standard; only the literal current word selects Creative).
"""

from __future__ import annotations

from navig.agent.proactive.user_state import UserPreferences
from navig.gateway.channels import focus_modes as fm


def test_catalog_keys_exactly_match_the_valid_chat_modes() -> None:
    """Every offered mode is a value `set_preference("chat_mode", ...)` will accept, and no
    valid mode is missing from the catalog."""
    assert set(fm.AGENT_MODES) == set(UserPreferences._VALID_MODES), (
        "focus_modes.AGENT_MODES has drifted from UserPreferences._VALID_MODES — /mode would "
        "offer a mode the preference store rejects, or omit a real one."
    )
    # Release alias stays bound to the same object until the tree-wide rename.
    assert fm.FOCUS_MODES is fm.AGENT_MODES


def test_the_vocabulary_is_exactly_standard_and_creative() -> None:
    assert set(fm.AGENT_MODES) == {"standard", "creative"}
    assert fm.DEFAULT_MODE == "standard"


def test_no_valid_mode_is_missing_from_the_catalog() -> None:
    for mode in UserPreferences._VALID_MODES:
        assert mode in fm.AGENT_MODES


def test_every_alias_resolves_to_a_real_mode() -> None:
    for alias, canonical in {**fm._ALIASES, **fm._LEGACY_ALIASES}.items():
        assert canonical in fm.AGENT_MODES, f"alias {alias!r} → {canonical!r}, not a real mode"


def test_default_mode_is_valid() -> None:
    assert fm.DEFAULT_MODE in fm.AGENT_MODES


def test_normalize_accepts_canonical_ids() -> None:
    for mode in fm.AGENT_MODES:
        assert fm.normalize(mode) == mode
        assert fm.normalize(f"  {mode.upper()} ") == mode  # case/space-insensitive
        assert fm.resolve(mode) == (mode, False)  # canonical words are not "legacy"


def test_normalize_resets_and_legacy_words_land_on_standard() -> None:
    # auto/reset/default clear to the default without a retired notice…
    for word in ("auto", "reset", "default"):
        assert fm.resolve(word) == (fm.DEFAULT_MODE, False)
    # …while retired focus-mode requests behave as Standard but are reported as
    # legacy so the surface can say the mode retired (without listing the choices).
    for word in ("work", "balance", "deep focus", "sleep", "planning", "relax", "chill"):
        assert fm.resolve(word) == ("standard", True), word
    assert fm.normalize("") is None
    assert fm.normalize("does-not-exist") is None


def test_no_legacy_phrase_can_select_the_new_creative() -> None:
    """The owner's hard rule: the OLD creative (tone) and every other mood phrase must
    never arm capability authoring — only the literal word `creative` may."""
    assert "creative" not in fm._LEGACY_ALIASES.values()
    # Retired phrases and mood words land on Standard or are not recognized —
    # never on Creative ("create"/"creative mode" are deliberately unrecognized:
    # a mood phrase must not arm authoring, and the /mode reply lists the two).
    for phrase in ("create", "creative mode", "balance", "work", "chill", "playful"):
        mode, was_legacy = fm.resolve(phrase)
        assert mode in (None, "standard"), (phrase, mode)
        if mode == "standard":
            assert was_legacy is True
    # …but the literal current word selects Creative, non-legacy.
    assert fm.resolve("creative") == ("creative", False)


def test_retired_modes_are_gone_from_the_offer_surface() -> None:
    for retired in UserPreferences._LEGACY_MODES:
        if retired == "creative":
            continue  # the id itself is reused by the new vocabulary
        assert retired not in fm.AGENT_MODES
        assert fm.normalize(retired) == "standard"


def test_every_catalog_mode_is_accepted_by_set_preference(tmp_path) -> None:
    """End-to-end: each offered mode is accepted by the same validation `/mode` writes
    through, and retired/outside values are rejected."""
    from navig.agent.proactive.user_state import UserStateTracker

    tracker = UserStateTracker(state_dir=tmp_path)  # isolated; never the real user state
    for mode in fm.AGENT_MODES:
        assert tracker.set_preference("chat_mode", mode) is True
        assert tracker.get_preference("chat_mode") == mode
    for retired in ("balance", "work", "deep-focus", "sleep", "planning"):
        assert tracker.set_preference("chat_mode", retired) is False, retired
