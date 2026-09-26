"""`navig plugin show` must report the SAME state word as `navig plugin list`.

`show` used to re-derive the word from `enabled`/`error`/`health` by hand. Measured across
all seven reachable combinations it agreed with `wire_state` exactly — but nothing bound
the two, so a state added to the canonical resolver would have reached the table, the
summary banner and the JSON payload, and silently skipped this view. A plugin reported
`wired` here while `list` says `shadowed` is precisely the disagreement the state exists
to prevent.

The word now comes from `_plugin_state`; only the per-state DETAIL lines are local.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.plugin import plugin_app
from navig.plugins.host import wire_state
from navig.plugins.sources import PluginSourceAudit

runner = CliRunner()


def _plugin(*, enabled=True, error="", health_state=None, health_error=""):
    health = None
    if health_state:
        health = SimpleNamespace(
            state=SimpleNamespace(value=health_state),
            error=health_error,
            degraded_components=lambda: [],
        )
    return SimpleNamespace(
        id="navig-demo", version="1.0.0", format="pip", path=None, source="pip",
        enabled=enabled, error=error, health=health, description="demo",
        commands=[], missing_deps=[],
    )


def _show(plugin, audit=None):
    host = MagicMock()
    host.list_installed.return_value = [plugin]
    host.get.return_value = plugin
    with (
        patch("navig.commands.plugin._host", return_value=host),
        patch("navig.plugins.sources.audit_plugin_sources", return_value=audit),
    ):
        return runner.invoke(plugin_app, ["show", plugin.id])


def _vocabulary() -> set[str]:
    """Read the state words off `wire_state`'s own source, never a restated list."""
    tree = ast.parse(inspect.getsource(wire_state))
    return {
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    }


_SHADOW = PluginSourceAudit(
    declared=1, verified=(), shadowed=("navig-demo",),
    unresolved=(), fix_paths=("plugins/navig-demo",),
)

# One reachable plugin shape per state word.
_CASES = {
    "disabled": (_plugin(enabled=False), None),
    "failed": (_plugin(error="ImportError: no module named x"), None),
    "degraded": (_plugin(health_state="degraded", health_error="one part failed"), None),
    "shadowed": (_plugin(), _SHADOW),
    "wired": (_plugin(), None),
}


def test_every_state_word_has_a_case_here():
    """Derived from the resolver: a new state fails HERE until this view handles it.

    That is the binding. Without it, a word added to `wire_state` reaches the table, the
    banner and `--json`, and silently skips `show`.
    """
    missing = _vocabulary() - set(_CASES)
    assert not missing, (
        f"`wire_state` can return {sorted(missing)}, which `navig plugin show` has no case "
        "for — add the case and the rendering, do not delete this assertion."
    )


@pytest.mark.parametrize("word", sorted(_CASES))
def test_show_reports_the_canonical_word(word: str):
    plugin, audit = _CASES[word]
    assert wire_state(plugin, {"navig-demo"} if audit else ()) == word, (
        "the fixture no longer produces the state it is named for"
    )
    result = _show(plugin, audit)
    assert result.exit_code == 0, result.output
    status = [ln for ln in result.output.splitlines() if "Status:" in ln]
    assert status, f"no status line at all for {word}: {result.output!r}"
    assert word in status[0].lower(), f"expected {word!r} in {status[0]!r}"


def test_shadowed_carries_its_remedy():
    result = _show(*_CASES["shadowed"])
    assert "pip install -e plugins/navig-demo" in result.output


def test_failed_still_shows_the_load_error():
    """The legacy-format load error is the most useful line on the screen."""
    result = _show(*_CASES["failed"])
    assert "ImportError: no module named x" in result.output


def test_degraded_still_shows_the_health_detail():
    result = _show(*_CASES["degraded"])
    assert "one part failed" in result.output
