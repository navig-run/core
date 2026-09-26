"""`navig.plugins.host.wire_state` — the canonical per-plugin state, and its consumers.

Two copies of this ladder existed and they DID disagree. `navig plugin list` derived the
word from `error` OR `health`; the hub/store aggregator tested only
`health.state == "failed"`. The legacy branch of `list_installed` sets `error` and leaves
`health` as None — so a legacy plugin that FAILED TO LOAD was advertised as usable in the
store while `navig plugin list` correctly showed it as failed.

The precedence now lives in one place and both surfaces read it. These tests pin the
precedence, that regression, and — structurally — that every word the function can return
is handled by every consumer, so the next state cannot be added to one and forgotten in
the other.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from navig.plugins.host import InstalledPlugin, wire_state


def _p(*, plugin_id="navig-demo", enabled=True, error="", health_state=None):
    health = SimpleNamespace(state=SimpleNamespace(value=health_state)) if health_state else None
    return SimpleNamespace(id=plugin_id, enabled=enabled, error=error, health=health)


# ── precedence ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("plugin", "expected"),
    [
        (_p(enabled=False, error="boom"), "disabled"),
        (_p(error="import failed"), "failed"),
        (_p(health_state="failed"), "failed"),
        (_p(health_state="degraded"), "degraded"),
        (_p(), "wired"),
        (_p(health_state="healthy"), "wired"),
    ],
)
def test_precedence(plugin, expected):
    assert wire_state(plugin) == expected


def test_shadowed_ranks_below_health_but_above_wired():
    shadow = {"navig-demo"}
    assert wire_state(_p(), shadow) == "shadowed"
    assert wire_state(_p(health_state="degraded"), shadow) == "degraded"
    assert wire_state(_p(enabled=False), shadow) == "disabled"


# ── the regression ───────────────────────────────────────────────────────────


def test_a_load_error_without_a_health_object_is_failed():
    """The legacy branch sets `error` and never passes `health`.

    The hub's old copy read `p.health.state.value if p.health is not None else "healthy"`,
    so this exact plugin evaluated to "healthy" and was advertised as usable.
    """
    broken = InstalledPlugin(
        id="legacy-broken", format="legacy", path=None, enabled=True,
        error="ImportError: no module named x",
    )
    assert broken.health is None, "the premise of the regression"
    assert wire_state(broken) == "failed"


def test_the_store_reports_that_plugin_as_broken_not_wired():
    from unittest.mock import MagicMock, patch

    from navig.hub.aggregator import _plugins

    broken = InstalledPlugin(
        id="legacy-broken", format="legacy", path=None, enabled=True,
        error="ImportError: no module named x",
    )
    host = MagicMock()
    host.list_installed.return_value = [broken]
    with patch("navig.plugins.host.get_plugin_host", return_value=host):
        item = _plugins()[0]
    assert item.state == "broken", "a plugin that failed to load must not read as usable"
    assert item.degraded is False


# ── every consumer must handle every word ────────────────────────────────────


def _vocabulary() -> set[str]:
    """The state words `wire_state` can actually return, read off its own source.

    Derived rather than hardcoded: a list written here would be the third copy of the
    thing this module exists to stop.
    """
    tree = ast.parse(inspect.getsource(wire_state))
    return {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def test_the_vocabulary_is_what_we_think_it_is():
    """A scan that silently matched nothing would make both tests below vacuous."""
    assert _vocabulary() == {"disabled", "failed", "degraded", "shadowed", "wired"}


def test_the_store_maps_every_word():
    """An unmapped word is a KeyError at render time — or worse, a default that quietly
    advertises a broken plugin as usable, which is the bug this module documents."""
    from navig.hub.aggregator import _WIRE_STATE_TO_STORE

    missing = _vocabulary() - set(_WIRE_STATE_TO_STORE)
    assert not missing, f"hub/aggregator does not map: {sorted(missing)}"
    stale = set(_WIRE_STATE_TO_STORE) - _vocabulary()
    assert not stale, f"hub/aggregator maps words wire_state never returns: {sorted(stale)}"


def test_the_cli_renders_every_word():
    from navig.commands.plugin import _PLUGIN_COLOR, _PLUGIN_MARK

    for state in _vocabulary():
        assert state in _PLUGIN_MARK, f"no glyph for {state}"
        assert state in _PLUGIN_COLOR, f"no colour for {state}"
