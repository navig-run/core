"""A scaffold condition that cannot be evaluated must fail, not silently skip.

`_process_structure` gates every item on `_check_condition` and `continue`s when it is
False. That handler used to catch a Jinja error, warn, and return False — so a malformed
expression SKIPPED the item and `navig scaffold` still reported
`✓ Scaffold complete`. For a `directory` item the whole `children` subtree goes with it,
and a file missing from a generated project is found much later, when the template is no
longer in mind.

`False` must mean "the condition evaluated to false", not "I could not tell".

An undefined *variable* is deliberately NOT this case: the environment uses Jinja's
default `Undefined`, which renders as an empty string, so `{{ missing }}` is a legitimate
False (pinned in `test_core_scaffolder.py`). Reaching the error path means the
EXPRESSION itself is malformed — a template bug its author must fix.

`commands/scaffold.py` already wraps `generate()` in
`except Exception -> ch.error(...) + typer.Exit(1)`; that path was simply unreachable
from here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.core.scaffolder import Scaffolder

# A syntactically broken Jinja expression — not a missing variable.
BROKEN = "{{ 1 +++ }}"


def _scaffolder() -> Scaffolder:
    return Scaffolder()


# ── the condition itself ───────────────────────────────────────────────────────


def test_a_malformed_condition_raises_instead_of_skipping():
    s = _scaffolder()
    with pytest.raises(ValueError) as exc:
        s._check_condition({"condition": BROKEN, "path": "src/app.py"}, {})

    msg = str(exc.value)
    assert "could not be evaluated" in msg
    assert "src/app.py" in msg, "the message must name the item that would vanish"


def test_the_original_error_is_chained():
    """`raise ... from e` keeps the Jinja message, which is what tells the template
    author WHERE the expression is wrong."""
    s = _scaffolder()
    with pytest.raises(ValueError) as exc:
        s._check_condition({"condition": BROKEN}, {})
    assert exc.value.__cause__ is not None


@pytest.mark.parametrize(
    "condition,variables,expected",
    [
        ("true", {}, True),
        ("false", {}, False),
        ("1", {}, True),
        ("{{ flag }}", {"flag": "true"}, True),
        ("{{ flag }}", {"flag": "false"}, False),
        ("{{ missing }}", {}, False),  # undefined -> "" -> False, NOT an error
        (None, {}, True),
    ],
)
def test_ordinary_conditions_are_unchanged(condition, variables, expected):
    """The success contract must not move — this is a narrowing, not a rewrite."""
    s = _scaffolder()
    assert s._check_condition({"condition": condition}, variables) is expected


# ── what it means for a real generate() ────────────────────────────────────────


def test_generate_fails_instead_of_producing_an_incomplete_tree(tmp_path: Path):
    template = {
        "structure": [
            {"type": "file", "path": "kept.txt", "content": "kept"},
            {"type": "file", "path": "skipped.txt", "content": "x", "condition": BROKEN},
        ]
    }
    with pytest.raises(ValueError):
        _scaffolder().generate(template, tmp_path, {})


def test_a_broken_condition_on_a_directory_takes_its_whole_subtree(tmp_path: Path):
    """The worst shape: skipping a `directory` item drops every child with it, so the
    generated project is missing an entire branch and nothing says so."""
    template = {
        "structure": [
            {
                "type": "directory",
                "path": "src",
                "condition": BROKEN,
                "children": [
                    {"type": "file", "path": "app.py", "content": "print(1)"},
                    {"type": "file", "path": "util.py", "content": "print(2)"},
                ],
            }
        ]
    }
    with pytest.raises(ValueError):
        _scaffolder().generate(template, tmp_path, {})

    assert not (tmp_path / "src" / "app.py").exists()


def test_a_false_condition_still_skips_quietly(tmp_path: Path):
    """The other direction — a deliberate `false` must remain a silent, successful
    skip, or every optional item in every template becomes an error."""
    template = {
        "structure": [
            {"type": "file", "path": "always.txt", "content": "a"},
            {"type": "file", "path": "never.txt", "content": "b", "condition": "false"},
            {"type": "file", "path": "absent.txt", "content": "c", "condition": "{{ nope }}"},
        ]
    }
    _scaffolder().generate(template, tmp_path, {})  # must not raise

    assert (tmp_path / "always.txt").exists()
    assert not (tmp_path / "never.txt").exists()
    assert not (tmp_path / "absent.txt").exists()
