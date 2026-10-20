"""The `Plugins` step of `neng update` must not claim work it does not do.

`neng update` runs real steps — `_step_git` pulls source, `_step_pypi` runs
`pip/uv install --upgrade navig`, `_step_reload_daemon` restarts the daemon. The
Plugins step sat between them titled "Plugins", printing "N user plugin(s)", while
**nothing in the command upgrades a plugin**: `_step_pypi` names only `navig`.
In a list of steps that genuinely update things, that reads as though the plugins
had been updated too.

Actually upgrading them is not a drop-in fix: first-party plugins in a dev install
are editable local checkouts, and `--upgrade` would replace an operator's working
copy with whatever PyPI holds. So the note states what happened instead.

The failure path was worse than the note. An exception returned
`ok=True, note="up to date"` — unable to list plugins, and reporting the strongest
success available. It now reports failure with the reason.

Neither change moves `neng update`'s exit code: `_run_update` gates that on `r1`
(the git/pypi step) alone, so `ok` here only changes the icon this step draws.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.commands import update as update_mod


def _mgr(plugins: dict):
    return SimpleNamespace(list_plugins=lambda: plugins)


def test_plugins_that_exist_are_not_reported_as_updated(monkeypatch):
    monkeypatch.setattr(
        "navig.plugins.get_plugin_manager",
        lambda: _mgr({"my-plugin": SimpleNamespace(source="user")}),
    )
    r = update_mod._step_plugins()

    assert r.ok
    assert "1 installed" in r.note
    assert "not upgraded by this step" in r.note, "the step must not imply it updated anything"
    assert "user plugin(s)" not in r.note


def test_no_plugins_says_so_instead_of_claiming_up_to_date(monkeypatch):
    monkeypatch.setattr("navig.plugins.get_plugin_manager", lambda: _mgr({}))
    r = update_mod._step_plugins()

    assert r.ok
    assert r.note == "no user plugins"


def test_a_failure_to_list_is_not_reported_as_up_to_date(monkeypatch):
    """The original bug: `except Exception: return ok=True, "up to date"`.

    Unable to check, reported the best possible result — the shape that makes a
    broken install look healthy precisely when someone runs the updater.
    """
    def boom():
        raise RuntimeError("plugin manager exploded")

    monkeypatch.setattr("navig.plugins.get_plugin_manager", boom)
    r = update_mod._step_plugins()

    assert not r.ok, "could not list must not read as success"
    assert "up to date" not in r.note
    assert "could not list plugins" in r.note
    assert "RuntimeError" in r.note


def test_a_plugin_that_is_not_marked_user_is_not_counted(monkeypatch):
    """`source == "user"` is the filter; a builtin must not inflate the number."""
    monkeypatch.setattr(
        "navig.plugins.get_plugin_manager",
        lambda: _mgr(
            {
                "builtin-thing": SimpleNamespace(source="builtin"),
                "mine": SimpleNamespace(source="user"),
            }
        ),
    )
    r = update_mod._step_plugins()

    assert r.ok
    assert "1 installed" in r.note
