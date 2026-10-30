"""`coerce_int` must keep numeric config values a YAML/JSON loader hands back.

Review #1269: `timeout_seconds: 600.0` arrives as a float; `int(str(600.0))` raised and
the coercer silently substituted the default, so a valid override changed behaviour.
"""

from __future__ import annotations

import pytest

from navig.core.coerce import coerce_int


@pytest.mark.parametrize("value", [600, 600.0, "600", " 600 ", "600.0"])
def test_integral_values_in_every_stored_form_are_kept(value):
    assert coerce_int(value, 900) == 600


@pytest.mark.parametrize(
    "value", [600.5, "600.5", "junk", None, True, float("inf"), float("nan"), "inf", ""]
)
def test_values_that_are_not_an_integer_fall_back_to_the_default(value):
    assert coerce_int(value, 900) == 900


def test_minimum_still_applies_to_float_input():
    assert coerce_int(0.0, 900, minimum=1) == 900
    assert coerce_int(5.0, 900, minimum=1) == 5
