"""Regression: `navig history` reported success for operations it did not perform.

`navig history undo` is the sharpest case, because the failure was *already recorded*.
On a failed undo the function writes a FAILED record to the operations ledger and then
returned — exit 0. So navig's two sources of truth contradicted each other:

    navig history list   ->  the undo is there, marked FAILED
    echo $?              ->  0   (the shell that ran it saw success)

The command documents itself as "same engine as the top-level `navig undo`", and that
one routes every refusal through `_fail()`, which always exits 1. They now agree.

`navig history replay` had the same shape with an extra twist: the replay IS the
command, run as a subprocess whose exit code is known and was printed — then thrown
away, so `navig history replay 1 && ./deploy.sh` deployed on top of a failed replay.

Measured before (against main, isolated NAVIG_DATA_DIR):

    navig history list --status bogus  -> exit 0
    navig history show 99999           -> exit 0
    navig history undo 99999           -> exit 0

and after: exit 2 for all three, with the same messages.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.history import history_app

pytestmark = pytest.mark.integration


def _invoke(args: list[str]):
    # obj={} deliberately NOT passed: reaching a sub-app without the root `navig`
    # callback is exactly the path that used to die on `ctx.obj["yes"] = …`.
    return CliRunner().invoke(history_app, args)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["list", "--status", "bogus"], "Invalid status"),
        (["list", "--type", "bogus"], "Invalid operation type"),
        (["show", "99999"], "No operation at index"),
        (["undo", "99999"], "No operation at index"),
        (["replay", "99999"], "No operation at index"),
        (["export", "out.json", "--format", "xml"], "Unknown format"),
    ],
)
def test_failure_paths_exit_nonzero(args, needle, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(args)

    assert result.exit_code != 0, (
        f"`navig history {' '.join(args)}` printed an error and still reported success"
    )
    flat = " ".join(result.output.split())
    assert needle in flat, f"expected {needle!r}, got: {flat[:160]!r}"


def test_the_subapp_is_reachable_without_the_root_callback():
    """Anti-vacuity for the ctx.ensure_object fix. Every subcommand assigns into
    ctx.obj; without the guarantee this raised
    "'NoneType' object does not support item assignment" — which would make the
    assertions above pass for the WRONG reason (a crash is also non-zero)."""
    result = _invoke(["show", "99999"])

    assert "NoneType" not in result.output, (
        "the sub-app crashed on ctx.obj instead of reporting the real problem:\n"
        + result.output
    )
    assert result.exit_code == 2, "not-found is the usage class"


def test_listing_an_empty_ledger_is_not_a_failure():
    """Anti-vacuity: an empty history is an empty state. If every path now exited
    non-zero the tests above would pass while the command was unusable."""
    result = _invoke(["list"])
    assert result.exit_code == 0, (
        f"`navig history list` should succeed on an empty ledger; got "
        f"{result.exit_code}: {' '.join(result.output.split())[:160]}"
    )
