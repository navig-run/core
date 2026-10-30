"""The env-leak guard must actually catch a leak, and must not cry wolf.

`conftest._no_env_leaks` fails any test that leaves `os.environ` changed. That is only
worth having if it (a) catches a real leak, (b) stays silent for correctly scoped
`monkeypatch` use, and (c) repairs the damage so one leaking test does not cascade.

Driven by stepping the real fixture's generator -- `getattr(fn, "__wrapped__", fn)`, since
pytest refuses a direct call -- rather than by asserting on its source. A source check would
pass while the fixture did nothing, which is the failure mode this repo keeps finding.
"""
from __future__ import annotations

import os

import pytest

from tests import conftest as ct

_KEY = "NAVIG_ENV_LEAK_GUARD_PROBE"


def _driver():
    """The real fixture body, ready to step. Its argument is only an ordering dependency."""
    fn = getattr(ct._no_env_leaks, "__wrapped__", ct._no_env_leaks)
    return fn(None)


def test_it_catches_a_leak_and_names_the_variable() -> None:
    gen = _driver()
    next(gen)
    os.environ[_KEY] = "1"
    try:
        with pytest.raises(AssertionError, match="left os.environ modified") as exc:
            next(gen, None)
        assert _KEY in str(exc.value), "the failure must name the leaked variable"
    finally:
        os.environ.pop(_KEY, None)


def test_it_repairs_the_leak_so_one_test_does_not_cascade() -> None:
    """A guard that only reports would leave every later test running dirty."""
    gen = _driver()
    next(gen)
    os.environ[_KEY] = "1"
    try:
        with pytest.raises(AssertionError):
            next(gen, None)
        assert _KEY not in os.environ, (
            "the guard reported the leak but left it in place, so every later test in this "
            "worker still inherits it"
        )
    finally:
        os.environ.pop(_KEY, None)


def test_it_stays_silent_when_nothing_changed() -> None:
    gen = _driver()
    next(gen)
    next(gen, None)  # must not raise


def test_it_ignores_pytest_s_own_bookkeeping() -> None:
    """pytest rewrites PYTEST_CURRENT_TEST per phase; flagging it fails every test.

    Without this exclusion the guard reported all 29,000 tests as leaking -- caught by
    running a negative control before trusting it.
    """
    gen = _driver()
    next(gen)
    os.environ["PYTEST_CURRENT_TEST"] = "something/else::changed (call)"
    next(gen, None)  # must not raise


@pytest.mark.skipif(os.name != "nt", reason="the CLI sets the encoding vars only on Windows")
def test_it_tolerates_the_cli_s_one_time_encoding_setdefault() -> None:
    """`navig.cli` sets PYTHONIOENCODING/PYTHONUTF8 once via setdefault on Windows.

    That is the product configuring its own stdio so `ch.success("✓")` does not crash a
    cp1252 console -- idempotent, and triggered by whichever test imports the CLI first.
    Flagging it would fail seven unrelated guards for something no test did.
    """
    key = "PYTHONIOENCODING"
    had = os.environ.pop(key, None)
    try:
        gen = _driver()
        next(gen)
        os.environ[key] = "utf-8"      # the setdefault the CLI performs
        next(gen, None)                # must not raise
    finally:
        os.environ.pop(key, None)
        if had is not None:
            os.environ[key] = had


def test_but_changing_a_product_owned_var_is_still_a_leak() -> None:
    """Tolerating the one-time appearance must not make the variable invisible."""
    key = "PYTHONIOENCODING"
    had = os.environ.get(key)
    os.environ[key] = "utf-8"
    try:
        gen = _driver()
        next(gen)
        os.environ[key] = "latin-1"    # a CHANGE, not a first-time setdefault
        with pytest.raises(AssertionError, match="left os.environ modified"):
            next(gen, None)
    finally:
        if had is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = had


def _added_is_reported(key: str, value: str, *, os_name: str) -> bool:
    """Step the guard around `key` APPEARING with `value` while `os.name` reads `os_name`."""
    had = os.environ.pop(key, None)
    real_name = os.name
    try:
        gen = _driver()
        os.name = os_name  # type: ignore[misc]  # the guard decides its exemption at setup
        try:
            next(gen)
        finally:
            os.name = real_name  # type: ignore[misc]
        os.environ[key] = value
        try:
            next(gen, None)
        except AssertionError:
            return True
        return False
    finally:
        os.environ.pop(key, None)
        if had is not None:
            os.environ[key] = had


def test_the_encoding_exemption_does_not_apply_off_windows() -> None:
    """`_force_utf8_stdio` returns early on POSIX, so a test that adds PYTHONUTF8 there is
    the test's own leak -- the exemption once applied on every platform."""
    assert _added_is_reported("PYTHONUTF8", "1", os_name="posix")


def test_the_encoding_exemption_covers_only_the_cli_s_values() -> None:
    """On Windows only the exact `utf-8` / `1` the CLI writes are tolerated; any other
    value alters later subprocesses' encoding and is reported."""
    assert _added_is_reported("PYTHONIOENCODING", "latin-1", os_name="nt")
    assert _added_is_reported("PYTHONUTF8", "0", os_name="nt")
    assert not _added_is_reported("PYTHONUTF8", "1", os_name="nt")
