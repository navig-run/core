"""The scaffolded plugin is the ONLY place the exit contract can be taught to user plugins.

`navig plugin new <name>` writes into `~/.navig/plugins/<name>/`, which no repo test can
ever scan — `test_command_exit_honesty.py`'s whole-tree ban covers `core/navig/commands/`
and the first-party `plugins/navig-*` packages, and stops there. So for a plugin somebody
scaffolds on their own machine, the template's example command is the entire teaching
mechanism for "a command that prints a failure must exit non-zero".

These tests pin that the example still demonstrates it, and that the template is generatable
at all: it is `.format(name=…, description=…)`-ed, so a single un-doubled brace in the
generated code raises KeyError at scaffold time and ships a plugin that cannot be created.
"""
from __future__ import annotations

import ast
import sys

import pytest
from typer.testing import CliRunner

from navig.commands.plugin import _PLUGIN_PY

pytestmark = pytest.mark.integration


def _generated() -> str:
    return _PLUGIN_PY.format(name="demo", description="A demo plugin")


def test_the_template_formats_without_a_brace_error():
    """A literal `{` in the template body must be doubled or scaffolding raises KeyError."""
    src = _generated()
    assert "demo" in src
    assert "{name}" not in src and "{description}" not in src


def test_the_generated_source_is_valid_python():
    ast.parse(_generated())


@pytest.fixture
def demo_app(tmp_path):
    """Write the generated plugin to disk and import it, as the loader would."""
    (tmp_path / "scaffold_demo_plugin.py").write_text(_generated(), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        sys.modules.pop("scaffold_demo_plugin", None)
        import scaffold_demo_plugin  # noqa: PLC0415

        yield scaffold_demo_plugin.app
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("scaffold_demo_plugin", None)


def test_the_example_failure_path_exits_nonzero(demo_app):
    """THE POINT: the example a plugin author copies must not print an error and return.

    Invoked with the bare argument, not ["hello", …] — a Typer app with exactly ONE
    command collapses the command name, so passing it makes Click reject the arg COUNT
    and return its own exit 2. That is a different 2, and asserting on it would pass
    while the template taught nothing.
    """
    result = CliRunner().invoke(demo_app, ["   "])

    assert result.exit_code == 2, result.output
    assert "must not be empty" in result.output, (
        "exit 2 came from Click's usage error, not the template's own raise"
    )


@pytest.mark.parametrize("args", [[], ["world"]])
def test_the_success_path_still_exits_zero(demo_app, args):
    """Anti-vacuity: if the example raised unconditionally the assertion above would
    pass while the scaffolded plugin was unusable."""
    result = CliRunner().invoke(demo_app, args)

    assert result.exit_code == 0, result.output
    assert "Hello, world" in result.output


def test_the_template_names_the_convention():
    """The comment is the teaching, not the code — keep it where it is copied from."""
    src = _generated()
    assert "typer.Exit(2)" in src and "typer.Exit(1)" in src, (
        "the example no longer states both exit codes, so a plugin author copying it "
        "learns only half the convention"
    )
