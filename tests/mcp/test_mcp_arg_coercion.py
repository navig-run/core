"""MCP boolean-argument coercion.

The logic was triplicated verbatim across desktop/filesystem/windows and had no tests.
It tested `bool` then `str` and returned *default* for anything else, so a JSON
**number** never reached a branch and the caller's value was discarded — inverting the
intent in both directions: `hold_ctrl=0` (default True) came back True, and `force=1`
(default False) came back False.

The string whitelist is deliberately narrower than `navig.core.coerce.coerce_bool`
(no `on`/`y`/`t`). These arguments include `force`, `overwrite` and `recursive`;
widening what counts as an affirmative on a destructive flag is a safety decision, not
a cleanup. That strictness is pinned below so a future consolidation cannot quietly
relax it.
"""

from __future__ import annotations

import pytest

from navig.mcp.tools._args import coerce_bool


@pytest.mark.parametrize("value", [True, False])
def test_real_booleans_pass_through(value):
    assert coerce_bool(value, default=not value) is value


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (1, False, True),    # force=1 used to come back False
        (0, True, False),    # hold_ctrl=0 used to come back True
        (1, True, True),
        (0, False, False),
        (2, False, True),
        (-1, False, True),
    ],
)
def test_json_numbers_are_honoured_not_discarded(value, default, expected):
    """THE REGRESSION: an int is neither bool nor str, so it used to yield the default."""
    assert coerce_bool(value, default=default) is expected, (
        f"{value!r} with default={default} must resolve to {expected}, not the default"
    )


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "  true  "])
def test_affirmative_strings_are_accepted(value):
    assert coerce_bool(value, default=False) is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "   ", "junk"])
def test_negative_and_unknown_strings_are_false(value):
    assert coerce_bool(value, default=False) is False


@pytest.mark.parametrize("value", ["on", "ON", "y", "t"])
def test_string_whitelist_stays_strict_on_purpose(value):
    """Pinned: these gate `force`/`overwrite`/`recursive`.

    `navig.core.coerce.coerce_bool` accepts these; this one must not. Relaxing it is a
    safety decision that should be made deliberately, not inherited from a refactor.
    """
    assert coerce_bool(value, default=False) is False


@pytest.mark.parametrize("default", [True, False])
def test_none_and_unknown_types_yield_the_default(default):
    assert coerce_bool(None, default=default) is default
    assert coerce_bool(object(), default=default) is default


def test_all_three_tool_modules_share_one_implementation():
    """They were verbatim copies; a divergent fourth copy is the failure mode."""
    from navig.mcp.tools.desktop import _coerce_bool as desktop_bool
    from navig.mcp.tools.filesystem import _coerce_bool as filesystem_bool
    from navig.mcp.tools.windows import _coerce_bool as windows_bool

    cases = [True, False, 0, 1, 2, "true", "yes", "1", "on", "y", "false", "", None]
    for value in cases:
        for default in (True, False):
            expected = coerce_bool(value, default)
            assert desktop_bool(value, default) is expected, f"desktop diverges on {value!r}"
            assert filesystem_bool(value, default) is expected, f"filesystem diverges on {value!r}"
            assert windows_bool(value, default) is expected, f"windows diverges on {value!r}"
