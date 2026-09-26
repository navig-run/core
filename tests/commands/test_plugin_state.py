"""`_plugin_state` — the single source both `navig plugin list`'s table and its
summary banner read for a plugin's wire state. Locks the precedence
(disabled > failed > degraded > shadowed > wired).

``shadowed`` means the plugin's import resolves to an INSTALLED copy rather than to the
source tree ``[tool.uv.sources]`` declares. It ranks below the health states on purpose: a
shadowed plugin loads perfectly, it is simply not your code — which is exactly why a green
tick over it was the lie this state exists to stop (`navig mobile ui` answered "No such
command" for a group registered unconditionally in the source, while `navig plugin list`
printed `✓ wired`).
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.commands.plugin import _plugin_state


def _p(*, plugin_id="navig-demo", enabled=True, error=None, health_state=None):
    health = SimpleNamespace(state=SimpleNamespace(value=health_state)) if health_state else None
    return SimpleNamespace(id=plugin_id, enabled=enabled, error=error, health=health)


def test_disabled_wins_even_over_error():
    assert _plugin_state(_p(enabled=False, error="boom")) == "disabled"


def test_error_is_failed():
    assert _plugin_state(_p(error="import failed")) == "failed"


def test_health_failed_is_failed():
    assert _plugin_state(_p(health_state="failed")) == "failed"


def test_health_degraded_is_degraded():
    assert _plugin_state(_p(health_state="degraded")) == "degraded"


def test_enabled_and_healthy_is_wired():
    assert _plugin_state(_p()) == "wired"
    assert _plugin_state(_p(health_state="healthy")) == "wired"


# ── shadowed ─────────────────────────────────────────────────────────────────


def test_shadowed_when_the_id_is_in_the_audit():
    assert _plugin_state(_p(), {"navig-demo"}) == "shadowed"


def test_not_shadowed_when_the_audit_names_another_plugin():
    assert _plugin_state(_p(), {"navig-other"}) == "wired"


def test_default_argument_means_no_shadow_information():
    """Callers that never audited must not accidentally report every plugin as shadowed."""
    assert _plugin_state(_p()) == "wired"


def test_health_states_outrank_shadowed():
    """A health problem is more specific than an environment one — name it first."""
    shadow = {"navig-demo"}
    assert _plugin_state(_p(enabled=False), shadow) == "disabled"
    assert _plugin_state(_p(error="import failed"), shadow) == "failed"
    assert _plugin_state(_p(health_state="failed"), shadow) == "failed"
    assert _plugin_state(_p(health_state="degraded"), shadow) == "degraded"


def test_every_state_has_a_glyph_and_a_colour():
    """The table indexes both dicts by state, so a state missing from either is a KeyError
    at render time — on the row that most needed to be shown."""
    from navig.commands.plugin import _PLUGIN_COLOR, _PLUGIN_MARK

    for state in ("wired", "shadowed", "degraded", "disabled", "failed"):
        assert state in _PLUGIN_MARK, state
        assert state in _PLUGIN_COLOR, state
