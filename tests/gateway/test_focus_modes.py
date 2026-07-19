"""The ``/mode`` focus-mode catalog must speak the one vocabulary its consumer validates.

``UserPreferences.set_preference("chat_mode", v)`` returns ``False`` for any ``v`` not in
``UserPreferences._VALID_MODES`` — so a focus mode the store rejects is a silent no-op. The
``/mode`` command shipped exactly that bug: it offered mood ids (``balance`` …) that were not
valid ``chat_mode`` values, so setting a mode never took effect (and it also crashed on a
removed ``MOOD_REGISTRY`` import). These tests pin the single source of truth
(``navig.gateway.channels.focus_modes``) to the consumer's vocabulary so they cannot drift.
"""

from __future__ import annotations

from navig.agent.proactive.user_state import UserPreferences
from navig.gateway.channels import focus_modes as fm


def test_catalog_keys_exactly_match_the_valid_chat_modes() -> None:
    """Every offered mode is a value `set_preference("chat_mode", ...)` will accept, and no
    valid mode is missing from the catalog."""
    assert set(fm.FOCUS_MODES) == set(UserPreferences._VALID_MODES), (
        "focus_modes.FOCUS_MODES has drifted from UserPreferences._VALID_MODES — /mode would "
        "offer a mode the preference store rejects, or omit a real one."
    )


def test_no_valid_mode_is_missing_from_the_catalog() -> None:
    for mode in UserPreferences._VALID_MODES:
        assert mode in fm.FOCUS_MODES


def test_every_alias_resolves_to_a_real_mode() -> None:
    for alias, canonical in fm._ALIASES.items():
        assert canonical in fm.FOCUS_MODES, f"alias {alias!r} → {canonical!r}, not a real mode"


def test_default_mode_is_valid() -> None:
    assert fm.DEFAULT_MODE in fm.FOCUS_MODES


def test_normalize_accepts_canonical_ids() -> None:
    for mode in fm.FOCUS_MODES:
        assert fm.normalize(mode) == mode
        assert fm.normalize(f"  {mode.upper()} ") == mode  # case/space-insensitive


def test_normalize_resets_and_legacy_words() -> None:
    # auto/reset/default clear to the default; legacy "balance" maps to the nearest live mode.
    assert fm.normalize("auto") == fm.DEFAULT_MODE
    assert fm.normalize("reset") == fm.DEFAULT_MODE
    assert fm.normalize("balance") == "work"
    assert fm.normalize("deep focus") == "deep-focus"
    assert fm.normalize("") is None
    assert fm.normalize("does-not-exist") is None


def test_the_modes_that_gate_do_not_disturb_exist() -> None:
    """is_do_not_disturb() keys off chat_mode in ("deep-focus", "sleep") — both must be real
    modes the user can actually select, or DND can never be reached from /mode."""
    for mode in ("deep-focus", "sleep"):
        assert mode in fm.FOCUS_MODES


def test_every_catalog_mode_is_accepted_by_set_preference(tmp_path) -> None:
    """End-to-end: each offered mode is accepted by the same validation `/mode` writes
    through, and a mode outside the catalog is rejected."""
    from navig.agent.proactive.user_state import UserStateTracker

    tracker = UserStateTracker(state_dir=tmp_path)  # isolated; never the real user state
    for mode in fm.FOCUS_MODES:
        assert tracker.set_preference("chat_mode", mode) is True
        assert tracker.get_preference("chat_mode") == mode
    assert tracker.set_preference("chat_mode", "balance") is False  # legacy mood id, rejected
