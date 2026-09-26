"""`navig plugin list --json` — the machine half of the audience.

The `shadowed` state tells you the CLI is running an installed copy of a plugin rather
than your source. It was added to the human table, but `--plain` carries five fixed
tab-separated columns (a scripting contract that must not change), so every agent, CI step
and script was blind to it — the audience least able to notice the symptom by eye.

House rule: "any status/list command that an agent or script might parse should also offer
`--json` printing the raw structured data. Humans get the table; scripts get JSON."
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.commands.plugin import plugin_app
from navig.plugins.sources import PluginSourceAudit

runner = CliRunner()


def _plugin(pid: str, *, enabled=True, error="", version="1.0.0", fmt="pip"):
    return SimpleNamespace(
        id=pid, version=version, format=fmt, source="pip", enabled=enabled,
        error=error, health=None, description=f"{pid} description",
    )


def _run(args, plugins, audit=None):
    host = MagicMock()
    host.list_installed.return_value = plugins
    with (
        patch("navig.commands.plugin._host", return_value=host),
        patch("navig.plugins.sources.audit_plugin_sources", return_value=audit),
    ):
        return runner.invoke(plugin_app, args)


def _payload(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_emits_parseable_json_with_the_documented_shape():
    res = _run(["list", "--json"], [_plugin("navig-alpha")])
    body = _payload(res)
    assert set(body) >= {"plugins", "counts"}
    row = body["plugins"][0]
    assert row["id"] == "navig-alpha"
    assert row["state"] == "wired"
    assert set(row) >= {"id", "state", "version", "format", "source", "enabled"}


def test_a_shadowed_plugin_is_visible_to_machines():
    """The whole point: the state a script could not see before."""
    audit = PluginSourceAudit(
        declared=2, verified=("navig-beta",), shadowed=("navig-alpha",),
        unresolved=(), fix_paths=("plugins/navig-alpha",),
    )
    body = _payload(_run(["list", "--json"], [_plugin("navig-alpha")], audit))

    assert body["plugins"][0]["state"] == "shadowed"
    assert body["counts"]["shadowed"] == 1
    assert body["sources"]["shadowed"] == ["navig-alpha"]
    assert body["sources"]["fix_paths"] == ["plugins/navig-alpha"]


def test_no_plugins_emits_an_empty_list_not_prose():
    """A script asked for JSON; "No plugins installed" is not parseable.

    The human branch returns before `--plain`, so `--json` has to be handled ahead of it.
    """
    body = _payload(_run(["list", "--json"], []))
    assert body["plugins"] == []
    assert body["counts"] == {}


def test_sources_is_omitted_outside_a_development_checkout():
    """Absent must not read as "nothing is shadowed" — it means "not checked"."""
    body = _payload(_run(["list", "--json"], [_plugin("navig-alpha")], None))
    assert "sources" not in body


def test_unresolved_entries_carry_their_reason():
    audit = PluginSourceAudit(
        declared=1, verified=(), shadowed=(),
        unresolved=(("navig-gamma", "not installed"),), fix_paths=(),
    )
    body = _payload(_run(["list", "--json"], [_plugin("navig-alpha")], audit))
    assert body["sources"]["unresolved"] == [{"id": "navig-gamma", "reason": "not installed"}]


def test_disabled_plugins_are_excluded_unless_all_is_passed():
    plugins = [_plugin("navig-alpha"), _plugin("navig-off", enabled=False)]
    assert [r["id"] for r in _payload(_run(["list", "--json"], plugins))["plugins"]] == [
        "navig-alpha"
    ]
    with_all = _payload(_run(["list", "--json", "--all"], plugins))
    assert {r["id"] for r in with_all["plugins"]} == {"navig-alpha", "navig-off"}
    assert with_all["counts"]["disabled"] == 1


def test_plain_output_is_unchanged():
    """`--plain`'s five tab-separated columns are a contract — JSON must not disturb it."""
    res = _run(["list", "--plain"], [_plugin("navig-alpha")])
    assert res.exit_code == 0
    line = next(ln for ln in res.output.splitlines() if ln.startswith("navig-alpha"))
    assert line.split("\t") == ["navig-alpha", "1.0.0", "pip", "pip", "on"]
