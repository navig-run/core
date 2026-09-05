"""`navig telegram sessions` and `navig template` reported failure and exited 0.

Two stand out beyond the exit codes:

  * `sessions delete <unknown-key>` printed "✓ Session deleted". The manager's
    `delete_session` returned None and no-opped silently on an unknown key, and
    the CLI announced success unconditionally — it did not merely misreport a
    failure, it claimed a deletion that never happened.
  * `template validate` printed "✗ N template(s) failed validation" and exited 0,
    which makes it unusable in a pipeline: the exit code is the only thing CI
    reads.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

pytestmark = pytest.mark.integration


# ── telegram sessions ──────────────────────────────────────────────────


def _session(key="telegram:user:1", count=3):
    s = MagicMock()
    s.session_key = key
    s.message_count = count
    return s


def _manager(sessions=(), delete_result=True):
    m = MagicMock()
    m.list_sessions.return_value = list(sessions)
    m.delete_session.return_value = delete_result
    return m


def _patch_manager(monkeypatch, manager):
    import navig.commands.telegram as t_mod

    monkeypatch.setattr(t_mod, "_session_manager", lambda **_k: manager)
    return t_mod


def test_deleting_an_unknown_session_does_not_claim_success(monkeypatch, capsys):
    """It printed "✓ Session deleted" for a key that never existed."""
    t_mod = _patch_manager(monkeypatch, _manager(delete_result=False))

    with pytest.raises(typer.Exit) as exc:
        t_mod.delete_session("telegram:user:nope", force=True)

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "not found" in out
    assert "Session deleted" not in out, out


def test_deleting_a_real_session_still_exits_zero(monkeypatch, capsys):
    """Anti-vacuity — otherwise raising unconditionally would pass the test above."""
    t_mod = _patch_manager(monkeypatch, _manager(delete_result=True))
    t_mod.delete_session("telegram:user:1", force=True)
    assert "Session deleted" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["show_session", "clear_session"])
def test_an_unknown_session_exits_non_zero(monkeypatch, cmd):
    t_mod = _patch_manager(monkeypatch, _manager(sessions=[_session()]))
    fn = getattr(t_mod, cmd)

    with pytest.raises(typer.Exit) as exc:
        fn("telegram:user:nope") if cmd == "show_session" else fn("telegram:user:nope", force=True)
    assert exc.value.exit_code == 1


def test_a_missing_session_module_exits_non_zero(monkeypatch):
    """Five commands carried this import guard, and all five exited 0.

    A script could not tell "no sessions" from "the module that manages them is
    missing".
    """
    import builtins

    import navig.commands.telegram as t_mod

    real_import = builtins.__import__

    def _no_module(name, *a, **kw):
        if "telegram_sessions" in name:
            raise ImportError(name)
        return real_import(name, *a, **kw)

    with patch.object(builtins, "__import__", _no_module), pytest.raises(typer.Exit) as exc:
        t_mod._session_manager()
    assert exc.value.exit_code == 1


def test_the_session_manager_delete_reports_whether_anything_was_deleted(tmp_path):
    """The manager itself, not the CLI: it returned None for both outcomes.

    `commands/memory.py` and `gateway/routes/memory.py` already do
    `if store.delete_session(...)`, so bool was the codebase's convention and this
    manager was the outlier.
    """
    from navig.gateway.channels.telegram_sessions import SessionManager

    mgr = SessionManager(storage_dir=tmp_path) if _accepts(SessionManager, "storage_dir") else None
    if mgr is None:  # pragma: no cover - constructor shape differs
        pytest.skip("SessionManager does not accept a storage dir in this build")

    assert mgr.delete_session("telegram:user:never-existed") is False


def _accepts(cls, kwarg: str) -> bool:
    import inspect

    return kwarg in inspect.signature(cls.__init__).parameters


# ── template ───────────────────────────────────────────────────────────


def _templates(monkeypatch, mapping):
    import navig.commands.template as tpl_mod

    mgr = MagicMock()
    mgr.discover_templates.return_value = None
    mgr.get_template.side_effect = lambda n: mapping.get(n)
    monkeypatch.setattr(tpl_mod, "TemplateManager", lambda *a, **k: mgr)
    return tpl_mod, mgr


def test_an_unknown_template_exits_non_zero(monkeypatch):
    tpl_mod, _ = _templates(monkeypatch, {})
    with pytest.raises(typer.Exit) as exc:
        tpl_mod.show_template_cmd("nope", {})
    assert exc.value.exit_code == 1


@pytest.mark.parametrize(
    "verb, cmd",
    [
        ("navig flow template add", "enable_template_cmd"),
        ("navig flow template remove", "disable_template_cmd"),
        ("(interactive menu) toggle", "toggle_template_cmd"),
    ],
)
def test_enable_disable_toggle_do_not_exit_zero_on_an_unknown_template(
    monkeypatch, verb, cmd
):
    """Three verbs this file's own subject missed.

    `show` and `validate` were fixed above; `enable`/`disable`/`toggle` were left
    reporting failure at exit 0. The manager prints "x Template 'X' not found" and returns
    False, and each command discarded it. Measured on the real CLI before the fix:

        navig flow template remove nosuchtemplate
        -> "x Template 'nosuchtemplate' not found"   exit 0

    so `navig flow template add X && <deploy it>` proceeded against a template that does
    not exist. `navig template` is not a command group — the wired surface is
    `navig flow template …`, which is why a check on the group name would have missed it.

    Exit 2, not 1: "not found" is the usage class, the same split `navig mcp` uses.
    """
    tpl_mod, mgr = _templates(monkeypatch, {})
    for attr in ("enable_template", "disable_template", "toggle_template"):
        setattr(mgr, attr, MagicMock(return_value=False))

    with pytest.raises(typer.Exit) as exc:
        getattr(tpl_mod, cmd)("nosuchtemplate", {})

    assert exc.value.exit_code == 2, f"{verb} on an unknown template must not exit 0"


def test_a_successful_enable_still_exits_zero(monkeypatch):
    """The half that keeps the fix honest: a real enable must not become an error."""
    template = MagicMock()
    tpl_mod, mgr = _templates(monkeypatch, {"caddy": template})
    mgr.enable_template = MagicMock(return_value=True)

    tpl_mod.enable_template_cmd("caddy", {})  # must not raise


def test_an_operation_failure_is_exit_1_not_2(monkeypatch):
    """The two failures are different classes and must stay distinguishable.

    A template that EXISTS but whose enable failed is an operation failure (1); only an
    absent one is the usage class (2). A single `raise typer.Exit(1)` would have collapsed
    both, which is what made `navig mcp`'s version wrong the first time.
    """
    template = MagicMock()
    tpl_mod, mgr = _templates(monkeypatch, {"caddy": template})
    mgr.enable_template = MagicMock(return_value=False)

    with pytest.raises(typer.Exit) as exc:
        tpl_mod.enable_template_cmd("caddy", {})

    assert exc.value.exit_code == 1


def test_validate_exits_non_zero_when_a_template_fails(monkeypatch, capsys):
    """The one that matters for CI — and the table must still be printed."""
    tpl_mod, mgr = _templates(monkeypatch, {})
    mgr.validate_all_templates.return_value = {"good": True, "broken": False}
    mgr.templates = {"good": MagicMock(), "broken": MagicMock()}

    with pytest.raises(typer.Exit) as exc:
        tpl_mod.validate_templates_cmd({})

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "failed validation" in out
    # The summary table is printed BEFORE the exit, so the operator sees which.
    assert "broken" in out


def test_validate_exits_zero_when_every_template_passes(monkeypatch):
    """Anti-vacuity for the validate path."""
    tpl_mod, mgr = _templates(monkeypatch, {})
    mgr.validate_all_templates.return_value = {"good": True}
    mgr.templates = {"good": MagicMock()}

    tpl_mod.validate_templates_cmd({})  # must not raise


def test_a_missing_editor_exits_non_zero(monkeypatch, tmp_path):
    """`template edit` with no usable $EDITOR reported the problem and exited 0."""
    tpl_mod, mgr = _templates(monkeypatch, {"web": MagicMock()})
    monkeypatch.setattr(
        tpl_mod, "get_config_manager",
        lambda: SimpleNamespace(get_active_server=lambda: "prod", apps_dir=tmp_path),
    )
    monkeypatch.setitem(
        __import__("sys").modules, "navig.cli.recovery",
        SimpleNamespace(require_active_server=lambda *_a, **_k: "prod"),
    )

    def _no_editor(*_a, **_k):
        raise FileNotFoundError("nano")

    monkeypatch.setattr(subprocess, "run", _no_editor)
    monkeypatch.setenv("EDITOR", "nano")

    with pytest.raises(typer.Exit) as exc:
        tpl_mod.edit_template_cmd("web", {})
    assert exc.value.exit_code == 1


# ── the list must not contradict the command that just ran ──────────────


def test_enabling_a_template_drops_the_list_cache(monkeypatch, tmp_path):
    """`list` serves a machine-global `templates.json` with a 1-HOUR TTL.

    Nothing dropped it when the state changed, so the read surface contradicted the write
    for the rest of the TTL. Measured on the real CLI:

        navig flow template add caddy   -> "OK Template 'caddy' enabled"  (file: enabled: true)
        navig flow template list        -> caddy ... Disabled

    which reads as "the enable did not work" — and the operator's next move is to run it
    again, or to give up on a template that is in fact enabled. The cache is keyed to
    `global_cache_dir()`, not to NAVIG_CONFIG_DIR, so an isolated config does not escape it.
    """
    from navig import cache_store
    from navig.commands import template as _tpl

    monkeypatch.setattr(cache_store, "global_cache_dir", lambda: tmp_path)
    # The name is SCOPED to the store, so ask for it rather than hardcoding it — a
    # hardcoded "templates.json" passes by looking for a file nothing writes any more.
    cache_name = _tpl._templates_cache_name()
    cache_store.write_json_cache(cache_name, {"templates": [{"name": "caddy"}]})
    assert (tmp_path / cache_name).exists(), "precondition: a cache to invalidate"

    tpl_mod, mgr = _templates(monkeypatch, {"caddy": MagicMock()})
    mgr.enable_template = MagicMock(return_value=True)

    tpl_mod.enable_template_cmd("caddy", {})

    assert not (tmp_path / cache_name).exists(), (
        "enabling a template left the stale list cache in place — `list` will keep "
        "reporting the old state for the rest of the TTL"
    )


def test_a_failed_enable_leaves_the_cache_alone(monkeypatch, tmp_path):
    """Only a state CHANGE invalidates. A failed enable changed nothing, so throwing the
    cache away would just make the next list pay a full rediscovery for no reason."""
    from navig import cache_store
    from navig.commands import template as _tpl

    monkeypatch.setattr(cache_store, "global_cache_dir", lambda: tmp_path)
    cache_name = _tpl._templates_cache_name()
    cache_store.write_json_cache(cache_name, {"templates": [{"name": "caddy"}]})

    tpl_mod, mgr = _templates(monkeypatch, {"caddy": MagicMock()})
    mgr.enable_template = MagicMock(return_value=False)

    with pytest.raises(typer.Exit):
        tpl_mod.enable_template_cmd("caddy", {})

    assert (tmp_path / cache_name).exists(), "a failed enable must not drop the cache"


def test_invalidate_is_best_effort_and_never_raises(monkeypatch, tmp_path):
    """Failing to delete a cache must not fail an operation that already succeeded.

    The worst case is exactly the behaviour before this function existed: the next read
    serves stale data until the TTL expires.
    """
    from navig import cache_store

    monkeypatch.setattr(cache_store, "global_cache_dir", lambda: tmp_path)
    assert cache_store.invalidate_json_cache("does-not-exist.json") is False

    cache_store.write_json_cache("templates.json", {"templates": []})

    def boom(self):
        raise OSError("locked by another process")

    monkeypatch.setattr("pathlib.Path.unlink", boom)
    assert cache_store.invalidate_json_cache("templates.json") is False


# ── the builtin store is READ-ONLY at runtime ──────────────────────────


def _builtin_template(tmp_path, name="demo", enabled=False):
    """A template laid out the way the shipped ones are, in a dir we can inspect."""
    import yaml

    d = tmp_path / "builtin" / name
    d.mkdir(parents=True)
    (d / "template.yaml").write_text(
        yaml.dump(
            {
                "name": name,
                "version": "1.0.0",
                "description": "a fixture template",
                "author": "tests",
                "enabled": enabled,
            }
        ),
        encoding="utf-8",
    )
    return d


def test_enabling_a_template_does_not_write_into_the_shipped_store(tmp_path, monkeypatch):
    """`builtin_store_dir()` is documented read-only at runtime — "user content is written
    to store_dir, never here" — because it lives INSIDE the package so it reaches a wheel.

    `enable()` called `save_metadata()`, which wrote `enabled: true` straight into
    `navig/builtin/templates/<name>/template.yaml`, i.e. into site-packages. Consequences,
    all real: a `pip install --upgrade` silently discards every template the operator
    enabled; a system-wide or read-only install cannot enable one at all; and in a dev
    checkout the command dirties a tracked file (observed — enabling `caddy` left its
    shipped template.yaml modified in git).
    """
    from navig import template_manager as tm

    d = _builtin_template(tmp_path)
    monkeypatch.setattr(tm, "store_dir", lambda: tmp_path / "store", raising=False)
    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "store")

    template = tm.Template(d)
    before = (d / "template.yaml").read_bytes()

    template.enable()

    assert (d / "template.yaml").read_bytes() == before, (
        "enabling a template rewrote the SHIPPED metadata file — that is site-packages in "
        "a real install, and an upgrade throws it away"
    )
    assert template.is_enabled() is True, "the enable must still take effect"


def test_enablement_is_persisted_to_the_user_store(tmp_path, monkeypatch):
    from navig import template_manager as tm

    d = _builtin_template(tmp_path)
    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "store")

    tm.Template(d).enable()

    overlay = tmp_path / "store" / "templates" / "enabled.json"
    assert overlay.is_file(), f"no overlay written under the user store: {overlay}"
    assert json.loads(overlay.read_text(encoding="utf-8")) == {"demo": True}

    # a fresh object, as a new process would build, reads it back
    assert tm.Template(d).is_enabled() is True


def test_a_disable_is_persisted_too(tmp_path, monkeypatch):
    from navig import template_manager as tm

    d = _builtin_template(tmp_path)
    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "store")

    tm.Template(d).enable()
    tm.Template(d).disable()

    assert tm.Template(d).is_enabled() is False
    overlay = tmp_path / "store" / "templates" / "enabled.json"
    assert json.loads(overlay.read_text(encoding="utf-8")) == {"demo": False}


def test_an_install_already_marked_enabled_keeps_working(tmp_path, monkeypatch):
    """The migration, and it is a fallback rather than a conversion step.

    An install that ran the OLD code carries `enabled: true` in its builtin template.yaml
    and has no overlay. The shipped metadata answers when the overlay is silent, so that
    install keeps reporting enabled; the next enable/disable moves it to the overlay.
    """
    from navig import template_manager as tm

    d = _builtin_template(tmp_path, enabled=True)
    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "store")

    assert tm.Template(d).is_enabled() is True, (
        "an install enabled under the old behaviour was silently reset to disabled"
    )


def test_the_list_cache_is_scoped_to_the_store(tmp_path, monkeypatch):
    """The cache dir is machine-global by design; the template list is not a machine fact.

    Enablement now lives in `store_dir()`, which derives from NAVIG_CONFIG_DIR, so one
    shared `templates.json` let a second config read the first's answer. Observed while
    building this change: a fresh config reported "Disabled" for a template its own store
    said nothing about. The filename carries the store identity; the directory stays where
    the platform wants it.
    """
    from navig.commands import template as tpl_mod

    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "a")
    name_a = tpl_mod._templates_cache_name()
    monkeypatch.setattr("navig.platform.paths.store_dir", lambda: tmp_path / "b")
    name_b = tpl_mod._templates_cache_name()

    assert name_a != name_b, "two stores share one cache file — they will read each other"
    assert name_a.startswith("templates.") and name_a.endswith(".json")
