"""Regression: `navig app …` handlers that print an error must EXIT NON-ZERO.

The same silent-failure class #345 exposed and #358 swept across host/db/docker/
backup/files/service/tunnel — `commands/app.py` was never in that sweep, so all 23
of its error paths printed ✗ and returned, exiting 0. A script could not branch on
them, and since #345 the operations ledger recorded each failed run as SUCCESS.

Convention (matches host.py / db.py / ai.py): not-found or missing-argument -> 2,
every other failure -> 1.

These call the handlers directly, which is the whole reachable surface: the Typer
wrappers at the bottom of app.py just forward `ctx.obj` and nothing else in core or
the plugins imports `navig.commands.app` (checked before the change — the agent and
bot surfaces re-implement their own app listing rather than calling these).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

import navig.commands.app as app_mod


@pytest.fixture
def captured_errors(monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(
        app_mod.ch, "error", lambda message, details=None: errors.append(str(message))
    )
    return errors


def _fake_cm(monkeypatch, **attrs):
    cm = SimpleNamespace(**attrs)
    monkeypatch.setattr(app_mod, "config_manager", cm)
    return cm


def _no_recovery(monkeypatch):
    """`require_active_host` is imported INSIDE each handler, so patch it at source."""
    import navig.cli.recovery as rec
    monkeypatch.setattr(rec, "require_active_host", lambda *a, **k: "prod")


# ── missing required argument -> 2 ────────────────────────────────────────────

@pytest.mark.parametrize(
    ("handler", "options"),
    [
        ("use_app", {}),
        ("add_app", {}),
        ("show_app", {}),
        ("edit_app", {}),
        ("info_app", {}),
        ("search_apps", {}),
    ],
)
def test_missing_required_argument_exits_2(monkeypatch, captured_errors, handler, options):
    _fake_cm(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        getattr(app_mod, handler)(options)
    assert exc.value.exit_code == 2
    assert captured_errors, f"{handler} exited without telling the user why"


def test_clone_app_missing_names_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.clone_app({"source_name": "only-source"})
    assert exc.value.exit_code == 2
    assert any("required" in e for e in captured_errors)


# ── not found -> 2 ────────────────────────────────────────────────────────────

def test_list_apps_unknown_host_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, host_exists=lambda _n: False)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.list_apps({"host": "ghost"})
    assert exc.value.exit_code == 2
    assert any("Host 'ghost' not found" in e for e in captured_errors)


def test_use_app_unknown_app_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, app_exists=lambda _h, _a: False)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.use_app({"app_name": "ghost"})
    assert exc.value.exit_code == 2
    assert any("not found" in e for e in captured_errors)


def test_clone_app_unknown_source_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, app_exists=lambda _h, name: name != "src")
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.clone_app({"host": "prod", "source_name": "src", "new_name": "dst"})
    assert exc.value.exit_code == 2
    assert any("Source app 'src' not found" in e for e in captured_errors)


# ── operation failure -> 1 ────────────────────────────────────────────────────

def test_add_app_duplicate_exits_1(monkeypatch, captured_errors):
    """A name collision is a real conflict, not a usage mistake."""
    _fake_cm(monkeypatch, host_exists=lambda _n: True, app_exists=lambda _h, _a: True)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.add_app({"host": "prod", "app_name": "dupe"})
    assert exc.value.exit_code == 1
    assert any("already exists" in e for e in captured_errors)


def test_clone_app_duplicate_target_exits_1(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, app_exists=lambda _h, _a: True)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.clone_app({"host": "prod", "source_name": "src", "new_name": "dst"})
    assert exc.value.exit_code == 1
    assert any("already exists" in e for e in captured_errors)


def test_unreadable_app_config_exits_1_and_keeps_the_cause(monkeypatch, captured_errors):
    """`from e` preserves the chain — losing it turns a diagnosable failure into a
    bare exit code."""
    boom = OSError("disk gone")

    def _raise(*_a, **_k):
        raise boom

    _fake_cm(monkeypatch, app_exists=lambda _h, _a: True, load_app_config=_raise)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.show_app({"host": "prod", "app_name": "web"})
    assert exc.value.exit_code == 1
    assert exc.value.__cause__ is boom


# ── the other half of the contract: success must still exit 0 ─────────────────

def test_empty_app_list_is_not_a_failure(monkeypatch):
    """Anti-vacuity + a real product rule: a host with no apps is EMPTY, not broken.
    Turning this into a non-zero exit would break `navig app list` in every script
    that runs it against a fresh host."""
    _fake_cm(
        monkeypatch,
        host_exists=lambda _n: True,
        list_apps=lambda _h: [],
        load_host_config=lambda _h: {},
        get_active_app=lambda: None,
    )
    _no_recovery(monkeypatch)
    monkeypatch.setattr(
        "navig.cli.recovery.empty_list_recovery", lambda *a, **k: None
    )
    app_mod.list_apps({"host": "prod"})  # must NOT raise


def test_show_app_missing_app_exits_2(monkeypatch, captured_errors):
    """`AppManager.load` raises FileNotFoundError for an unknown app: that is not-found
    (2), not a broken configuration (1)."""

    def _missing(*_a, **_k):
        raise FileNotFoundError("App 'ghost' not found on host 'prod'")

    _fake_cm(monkeypatch, load_app_config=_missing)
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        app_mod.show_app({"host": "prod", "app_name": "ghost"})
    assert exc.value.exit_code == 2
    assert any("not found" in e for e in captured_errors)


# ── remove keeps an unrelated cached active app ───────────────────────────────

def _remove_with_cache(monkeypatch, tmp_path, cached: str, resolved: str):
    cache = tmp_path / "active_app.txt"
    cache.write_text(cached, encoding="utf-8")
    deleted: list[str] = []
    _fake_cm(
        monkeypatch,
        app_exists=lambda _h, _a: True,
        get_active_app=lambda: resolved,
        delete_app_config=lambda _h, a: deleted.append(a),
        active_app_file=cache,
    )
    monkeypatch.setattr(app_mod.ch, "success", lambda *a, **k: None)
    app_mod.remove_app({"host": "prod", "app_name": "a", "force": True})
    assert deleted == ["a"]
    return cache


def test_remove_app_keeps_a_different_cached_app(monkeypatch, tmp_path):
    """NAVIG_ACTIVE_APP / project config resolve to `a`, the user cache holds `b`:
    removing `a` must not forget the user's global active app `b`."""
    cache = _remove_with_cache(monkeypatch, tmp_path, cached="b", resolved="a")
    assert cache.read_text(encoding="utf-8") == "b"


def test_remove_app_clears_its_own_cached_pointer(monkeypatch, tmp_path):
    cache = _remove_with_cache(monkeypatch, tmp_path, cached="a", resolved="other")
    assert not cache.exists()


# ── failures caught in an `except` that used to fall off the end (exit 0) ────────
#
# These five handlers printed the failure from an `except` block and then simply ended,
# which is `return None` — exit 0. The exit-honesty source guard did not see them: it
# exempts the tail scan for any function that raises ANYWHERE, and each of these raises
# on an earlier validation branch.


def _raising(exc: BaseException):
    def _raise(*_a, **_k):
        raise exc

    return _raise


@pytest.mark.parametrize("exc", [FileNotFoundError("no .navig"), RuntimeError("io")])
def test_use_app_clear_local_failure_exits_1(monkeypatch, captured_errors, exc):
    _fake_cm(monkeypatch, clear_active_app_local=_raising(exc))
    with pytest.raises(typer.Exit) as exc_info:
        app_mod.use_app({"clear_local": True})
    assert exc_info.value.exit_code == 1
    assert exc_info.value.__cause__ is exc
    assert captured_errors


@pytest.mark.parametrize(
    ("exc", "code"),
    [(FileNotFoundError("no .navig"), 1), (ValueError("bad app"), 2), (RuntimeError("io"), 1)],
)
def test_use_app_set_failure_exits_non_zero(monkeypatch, captured_errors, exc, code):
    _fake_cm(
        monkeypatch, app_exists=lambda _h, _a: True, set_active_app=_raising(exc)
    )
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc_info:
        app_mod.use_app({"app_name": "web"})
    assert exc_info.value.exit_code == code
    assert exc_info.value.__cause__ is exc
    assert captured_errors


def test_add_app_save_failure_exits_1(monkeypatch, captured_errors):
    boom = OSError("read-only")
    _fake_cm(
        monkeypatch,
        host_exists=lambda _n: True,
        app_exists=lambda _h, _a: False,
        load_host_config=lambda _h: {"services": {"web": "nginx"}},
        save_app_config=_raising(boom),
    )
    _no_recovery(monkeypatch)
    # Accept the detected webserver, skip the optional database and paths prompts.
    answers = iter([True, False, False])
    monkeypatch.setattr(app_mod.ch, "confirm_action", lambda *a, **k: next(answers))
    with pytest.raises(typer.Exit) as exc_info:
        app_mod.add_app({"host": "prod", "app_name": "web"})
    assert exc_info.value.exit_code == 1
    assert exc_info.value.__cause__ is boom
    assert any("saving app" in e for e in captured_errors)


def test_clone_app_save_failure_exits_1(monkeypatch, captured_errors):
    boom = OSError("read-only")
    _fake_cm(
        monkeypatch,
        app_exists=lambda _h, name: name == "src",
        load_app_config=lambda _h, _a: {"webserver": {"type": "nginx"}},
        save_app_config=_raising(boom),
    )
    _no_recovery(monkeypatch)
    with pytest.raises(typer.Exit) as exc_info:
        app_mod.clone_app({"host": "prod", "source_name": "src", "new_name": "dst"})
    assert exc_info.value.exit_code == 1
    assert exc_info.value.__cause__ is boom
    assert any("cloned app" in e for e in captured_errors)


@pytest.mark.parametrize(
    "exc", [app_mod.subprocess.CalledProcessError(1, "nano"), RuntimeError("no editor")]
)
def test_edit_app_editor_failure_exits_1(monkeypatch, captured_errors, tmp_path, exc):
    (tmp_path / "web.yaml").write_text("webserver: {type: nginx}\n", encoding="utf-8")
    _fake_cm(
        monkeypatch,
        app_exists=lambda _h, _a: True,
        apps_dir=tmp_path,
        hosts_dir=tmp_path,
    )
    _no_recovery(monkeypatch)
    monkeypatch.setenv("EDITOR", "nano")
    monkeypatch.setattr(app_mod.subprocess, "run", _raising(exc))
    with pytest.raises(typer.Exit) as exc_info:
        app_mod.edit_app({"host": "prod", "app_name": "web"})
    assert exc_info.value.exit_code == 1
    assert exc_info.value.__cause__ is exc
    assert captured_errors
