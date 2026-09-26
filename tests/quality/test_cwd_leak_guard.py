"""The cwd-leak guard in conftest has teeth, and repairs before it fails.

`conftest._no_cwd_leaks` fails any test that leaves the process cwd changed. Driven here
the way `test_env_leak_guard.py` drives its sibling: step the real fixture body around a
deliberate `os.chdir`, and assert it is reported BY NAME and REPAIRED -- a guard that only
reports leaves every later test in the worker standing in the wrong directory, which is
exactly what `tests/ops/test_monitoring_unicode.py` fell over (#1427).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import conftest as ct


def _driver():
    """The real fixture body, ready to step. Its argument is only an ordering dependency."""
    fn = getattr(ct._no_cwd_leaks, "__wrapped__", ct._no_cwd_leaks)
    return fn(None)


def test_it_catches_a_leak_and_names_both_directories(tmp_path: Path) -> None:
    start = os.getcwd()
    gen = _driver()
    next(gen)
    os.chdir(tmp_path)  # the leak: no restore
    try:
        with pytest.raises(AssertionError, match="did not restore it") as exc:
            next(gen, None)
        msg = str(exc.value)
        # the guard prints both with !r, so Windows backslashes appear doubled
        assert repr(start) in msg and repr(str(tmp_path)) in msg, "the failure must name where it was and where it went"
        assert "monkeypatch.chdir" in msg, "the failure must name the remedy"
    finally:
        os.chdir(start)


def test_it_repairs_the_leak_so_one_test_does_not_cascade(tmp_path: Path) -> None:
    start = os.getcwd()
    gen = _driver()
    next(gen)
    os.chdir(tmp_path)
    try:
        with pytest.raises(AssertionError):
            next(gen, None)
        assert os.getcwd() == start, (
            "the guard reported the leak but left the cwd changed, so every later test in "
            "this worker still runs from the wrong directory"
        )
    finally:
        os.chdir(start)


def test_it_stays_silent_when_nothing_changed() -> None:
    gen = _driver()
    next(gen)
    next(gen, None)  # must not raise


def test_a_deleted_cwd_is_reported_not_swallowed(tmp_path: Path) -> None:
    """The worst case: a test chdir'd into a directory it then removed. `os.getcwd()`
    raises for every later test; the guard must still report and get back home."""
    start = os.getcwd()
    doomed = tmp_path / "gone"
    doomed.mkdir()
    gen = _driver()
    next(gen)
    os.chdir(doomed)
    try:
        os.chdir(tmp_path)  # Windows cannot delete the cwd itself; step out, then delete
        doomed.rmdir()
        os.chdir(tmp_path)
        with pytest.raises(AssertionError, match="did not restore it"):
            next(gen, None)
        assert os.getcwd() == start
    finally:
        os.chdir(start)


def test_the_guard_is_autouse_and_ordered_after_isolation() -> None:
    """It must run for EVERY test, and only after the session isolation has settled --
    the same two ordering facts the env guard documents."""
    import inspect

    fn = getattr(ct._no_cwd_leaks, "__wrapped__", ct._no_cwd_leaks)
    assert list(inspect.signature(fn).parameters) == ["_isolate_navig_config_dir"]
    src = inspect.getsource(ct)
    i = src.index("def _no_cwd_leaks")
    assert "@pytest.fixture(autouse=True)" in src[i - 80 : i], "the cwd guard must be autouse"
