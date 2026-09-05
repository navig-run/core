"""ContextBuilder boolean feature flags honor `navig config set <flag> false`.

`navig config set` stores its argument verbatim as a string, and ``bool("false")`` is
``True``. Every ContextBuilder boolean (``enabled`` + the ``include_*`` section gates) was
read raw from the ``context_builder:`` config and used in a truthiness test, so a
config-disabled section stayed ON. ``__init__`` now coerces all of them once via
``navig.core.coerce.coerce_bool``.
"""

from __future__ import annotations

import pytest

from navig.memory.context_builder import _BOOL_FLAGS, _DEFAULTS, EMPTY_CONTEXT, ContextBuilder


@pytest.mark.parametrize("flag", _BOOL_FLAGS)
def test_string_false_coerces_to_bool_false(flag):
    # The footgun: `navig config set context_builder.<flag> false` stored "false".
    cb = ContextBuilder(config={flag: "false"})
    assert cb._cfg[flag] is False, f"{flag}: string 'false' must coerce to False"


@pytest.mark.parametrize("flag", _BOOL_FLAGS)
def test_string_on_coerces_to_bool_true(flag):
    cb = ContextBuilder(config={flag: "on"})
    assert cb._cfg[flag] is True


@pytest.mark.parametrize("flag", _BOOL_FLAGS)
def test_real_bool_is_preserved(flag):
    assert ContextBuilder(config={flag: True})._cfg[flag] is True
    assert ContextBuilder(config={flag: False})._cfg[flag] is False


@pytest.mark.parametrize("flag", _BOOL_FLAGS)
def test_unset_uses_the_flag_default(flag):
    cb = ContextBuilder(config={})  # nothing set → _DEFAULTS
    assert cb._cfg[flag] is _DEFAULTS[flag]


@pytest.mark.parametrize("flag", _BOOL_FLAGS)
def test_unknown_token_falls_back_to_default_not_truthy(flag):
    cb = ContextBuilder(config={flag: "maybe"})
    assert cb._cfg[flag] is _DEFAULTS[flag]


def test_disabled_via_string_false_returns_empty_context():
    """End-to-end: `enabled: "false"` must actually short-circuit build_context."""
    cb = ContextBuilder(config={"enabled": "false"})
    assert cb.build_context("hello there") == dict(EMPTY_CONTEXT)
