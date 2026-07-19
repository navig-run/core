"""`_plugin_state` — the single source both `navig plugin list`'s table and its
summary banner read for a plugin's wire state. Locks the precedence
(disabled > failed > degraded > wired).
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.commands.plugin import _plugin_state


def _p(*, enabled=True, error=None, health_state=None):
    health = SimpleNamespace(state=SimpleNamespace(value=health_state)) if health_state else None
    return SimpleNamespace(enabled=enabled, error=error, health=health)


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
