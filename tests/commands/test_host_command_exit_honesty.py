"""Regression: host command handlers that print an error must EXIT NON-ZERO.

Part of the silent-failure class PR #345 exposed. Several ``navig host …``
handlers printed ``ch.error(...)`` and then ``return``ed — exiting 0 — so a
genuine failure (a missing host, a save error, a missing action flag) recorded
SUCCESS in the operation ledger. They now ``raise typer.Exit`` with a truthful
code: exit 2 when the named host does not exist, exit 1 for every other failure.

Convention (matches ai.py peers + db.py): not-found -> 2, other failures -> 1.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

import navig.commands.host as host_mod


@pytest.fixture
def captured_errors(monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(
        host_mod.ch, "error", lambda message, details=None: errors.append(str(message))
    )
    return errors


def _fake_cm(monkeypatch, **attrs):
    cm = SimpleNamespace(**attrs)
    monkeypatch.setattr(host_mod, "config_manager", cm)
    return cm


def test_use_host_missing_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, host_exists=lambda _n: False)

    with pytest.raises(typer.Exit) as exc:
        host_mod.use_host("ghost", {})

    assert exc.value.exit_code == 2  # not found
    assert any("Host 'ghost' not found" in e for e in captured_errors)


def test_set_default_host_missing_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, host_exists=lambda _n: False)

    with pytest.raises(typer.Exit) as exc:
        host_mod.set_default_host("ghost", {})

    assert exc.value.exit_code == 2
    assert any("Host 'ghost' not found" in e for e in captured_errors)


def test_add_host_duplicate_exits_1(monkeypatch, captured_errors):
    # "already exists" is a conflict, not a not-found → exit 1.
    _fake_cm(monkeypatch, host_exists=lambda _n: True)

    with pytest.raises(typer.Exit) as exc:
        host_mod.add_host("prod", {})

    assert exc.value.exit_code == 1
    assert any("already exists" in e for e in captured_errors)


def test_remove_host_missing_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, host_exists=lambda _n: False)

    with pytest.raises(typer.Exit) as exc:
        host_mod.remove_host("ghost", {})

    assert exc.value.exit_code == 2
    assert any("Host 'ghost' not found" in e for e in captured_errors)


def test_remove_host_missing_quiet_still_exits_2(monkeypatch, captured_errors):
    # Even with --quiet (no message printed) the exit code must be truthful.
    _fake_cm(monkeypatch, host_exists=lambda _n: False)

    with pytest.raises(typer.Exit) as exc:
        host_mod.remove_host("ghost", {"quiet": True})

    assert exc.value.exit_code == 2


def test_info_host_missing_exits_2(monkeypatch, captured_errors):
    _fake_cm(
        monkeypatch,
        host_exists=lambda _n: False,
        get_active_host=lambda *a, **k: None,
    )

    with pytest.raises(typer.Exit) as exc:
        host_mod.info_host({"host_name": "ghost"})

    assert exc.value.exit_code == 2
    assert any("Host 'ghost' not found" in e for e in captured_errors)


def test_info_host_no_host_at_all_exits_1(monkeypatch, captured_errors):
    # No host named AND no active host → not a "not found" of a named host,
    # but still a failure (sibling test_host raises for the same condition).
    _fake_cm(monkeypatch, get_active_host=lambda *a, **k: None)

    with pytest.raises(typer.Exit) as exc:
        host_mod.info_host({})

    assert exc.value.exit_code == 1
    assert any("no active host" in e.lower() for e in captured_errors)


def test_clone_host_source_missing_exits_2(monkeypatch, captured_errors):
    _fake_cm(monkeypatch, host_exists=lambda _n: False)

    with pytest.raises(typer.Exit) as exc:
        host_mod.clone_host({"source_name": "ghost", "new_name": "copy"})

    assert exc.value.exit_code == 2
    assert any("Source host 'ghost' not found" in e for e in captured_errors)


def test_clone_host_target_exists_exits_1(monkeypatch, captured_errors):
    # source exists, target already exists → conflict → exit 1.
    _fake_cm(monkeypatch, host_exists=lambda name: name in ("src", "dst"))

    with pytest.raises(typer.Exit) as exc:
        host_mod.clone_host({"source_name": "src", "new_name": "dst"})

    assert exc.value.exit_code == 1
    assert any("already exists" in e for e in captured_errors)
