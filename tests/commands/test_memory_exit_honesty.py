"""Regression: `navig memory …` must not exit 0 on failure — and must not reword its
own exit code into an error message.

Two shapes, both live before this:

1. TWENTY-ONE broad handlers of the form

       except Exception as e:
           ch.error(f"Error: {e}")     # ...and fall off the end

   so every `navig memory` verb exited 0 on any real error.

2. THE SWALLOWED EXIT — the nastier one, because the fix LOOKS present.
   `memory knowledge add|search` did

       raise typer.Exit(1)             # inside a try
       ...
       except Exception as e:          # typer.Exit is a RuntimeError -> CAUGHT
           ch.error(f"Error: {e}")

   `typer.Exit` derives from RuntimeError, so the broad handler caught the deliberate
   exit, printed the exit CODE as the message, and fell through. Measured before:

       navig memory knowledge add     -> exit 0 | ✗ Error: 1

   The shape is guarded tree-wide by tests/quality/test_no_swallowed_exit.py; these
   tests pin the user-visible behaviour of the two commands that actually had it.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.memory import memory_app

pytestmark = pytest.mark.integration


def _invoke(args: list[str]):
    return CliRunner().invoke(memory_app, args, obj={})


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["knowledge", "add"], "--key and --content required"),
        (["knowledge", "search"], "--query required"),
    ],
)
def test_missing_required_option_exits_nonzero_with_its_real_message(args, needle):
    """The message must be the one the code wrote — not the exit code str()'d."""
    result = _invoke(args)

    assert result.exit_code != 0, (
        f"`memory {' '.join(args)}` reported success for a missing required option"
    )
    flat = " ".join(result.output.split())
    assert needle in flat, f"expected the real message, got: {flat[:160]!r}"
    assert "Error: 1" not in flat, (
        "the exit code was reworded into the error message — a broad `except Exception` "
        "is swallowing the deliberate typer.Exit again"
    )


def test_a_valid_knowledge_action_is_not_broken_by_the_change():
    """Anti-vacuity: the assertions above must fail for the RIGHT reason. If every
    knowledge action now raised, they would pass while the command was unusable."""
    result = _invoke(["knowledge", "list"])

    assert result.exit_code == 0, (
        f"`memory knowledge list` should succeed on an empty store; got "
        f"{result.exit_code}: {' '.join(result.output.split())[:160]}"
    )


def test_an_unknown_knowledge_action_is_a_usage_error():
    result = _invoke(["knowledge", "definitely-not-an-action"])
    assert result.exit_code != 0


def test_history_with_no_store_is_an_empty_state_not_a_failure(tmp_path, monkeypatch):
    """`memory history` on a fresh install has no DB yet. That is EMPTY, not broken —
    the same function already reports an existing-but-empty session with ch.info and
    exit 0, so a missing DB used `ch.error` inconsistently with itself and made a first
    run look failed."""
    import navig.commands.memory as mem

    monkeypatch.setattr(
        mem, "_get_config", lambda: type("C", (), {"global_config_dir": str(tmp_path)})()
    )
    result = _invoke(["history", "some-session"])

    assert result.exit_code == 0, "no history YET must not be a failure"
    flat = " ".join(result.output.split())
    assert "No conversation history" in flat
