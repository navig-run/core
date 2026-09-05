"""Tests for remote_agent timeout configuration parsing.

These used to call ``importlib.reload(navig.agent.remote_agent)`` to re-read
``NAVIG_REMOTE_TIMEOUT`` at import time. Reload rebinds the module's globals
**in place**: `CommandState` and `RemoteResult` become fresh classes while every
name already imported elsewhere still holds the originals. Enum members then
compare unequal to their identical-looking twins, which reads as nonsense::

    assert <CommandState.FAILED: 'failed'> == <CommandState.FAILED: 'failed'>

and `RemoteResult.success` — ``state == CommandState.COMPLETED and
return_code == 0`` — went False for a completed, zero-exit result, because the
property resolved the *new* enum out of the shared globals while the test held
an *old* member. Measured on clean main with this file forced to run first:
**9 of `test_remote_agent.py`'s tests failed**. Alphabetically this file sorts
second, so it usually looked fine — but the gate's relevance-ranked selection can
order it either way, so the failure surfaced only sometimes, which reads as flake.

The parse is now a plain function, so these tests pass it a value instead of
reloading a module half the suite already holds references into.
"""

import pytest

from navig.agent.remote_agent import DEFAULT_COMMAND_TIMEOUT, resolve_command_timeout


@pytest.mark.parametrize("raw,expected", [("45", 45), ("1", 1), ("3600", 3600)])
def test_a_valid_positive_timeout_is_used(raw: str, expected: int) -> None:
    assert resolve_command_timeout(raw) == expected


@pytest.mark.parametrize("raw", ["invalid-timeout", "", "12.5", "1e3", None])
def test_an_unparseable_timeout_falls_back_to_the_default(raw: str | None) -> None:
    assert resolve_command_timeout(raw) == DEFAULT_COMMAND_TIMEOUT


@pytest.mark.parametrize("raw", ["-10", "0", "-1"])
def test_a_non_positive_timeout_falls_back_to_the_default(raw: str) -> None:
    """A zero or negative timeout would abort every command instantly."""
    assert resolve_command_timeout(raw) == DEFAULT_COMMAND_TIMEOUT


def test_the_module_constant_comes_from_the_same_parser() -> None:
    """Anti-vacuity: testing the helper only means something if the module uses it."""
    import os

    from navig.agent import remote_agent

    assert remote_agent.COMMAND_TIMEOUT == resolve_command_timeout(
        os.environ.get("NAVIG_REMOTE_TIMEOUT")
    )


def test_no_reload_leaves_the_enum_identity_intact() -> None:
    """The regression this file used to cause. `CommandState` seen from here must
    be the same object every other module holds — one reload anywhere breaks that
    for the rest of the session."""
    import sys

    from navig.agent.remote_agent import CommandState, RemoteResult

    assert CommandState is sys.modules["navig.agent.remote_agent"].CommandState

    result = RemoteResult(host="h", state=CommandState.COMPLETED, return_code=0)
    assert result.success is True
