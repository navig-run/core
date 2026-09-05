"""Regression: a FAILED operation must not be silent, and must not exit 0.

Two shapes, neither visible to the AST guard in tests/quality/test_command_exit_honesty.py
— which is why they survived every previous sweep.

1. THE SUCCESS-ONLY BRANCH (`navig trigger add|remove|enable|disable`)

       if manager.remove_trigger(trigger_id):
           ch.success(...)          # ← and no else

   A failed removal printed NOTHING AT ALL and exited 0. That is worse than the
   error-then-return shape the guard does catch: there is no ✗ to notice, so the
   command looks like it did the job. Nothing in the source pairs an error call with
   a return, so no single-frame scan can see it.

2. THE DISCARDED BOOL (`navig db optimize|repair`)

       optimize_table_cmd(table, ctx.obj)   # returns False on failure — dropped

   The Typer wrapper threw the value away, so all three failure paths exited 0 —
   including `_validate_sql_identifier`, which exists to reject SQL injection. The
   guard classifies a value-returning function as a helper and skips it, and the call
   site has no `if` to inspect.

The bool contract stays where it is (the function knows WHY it failed); the CLI
boundary is what turns it into an exit code.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

import navig.commands.triggers as trig


class _Manager:
    """Every mutation fails — the case that used to produce silence."""

    #: The commands go through `_readable_manager()`, which refuses to answer from a
    #: manager whose file could not be read. A fake that omits this attribute does not
    #: implement the interface it is standing in for — and the failure it produces
    #: (AttributeError) looks nothing like the behaviour under test. `False` is the
    #: honest value here: these cases are about a mutation failing on a file that WAS
    #: readable, which is a different fault from the file being unreadable.
    load_failed = False

    def __init__(self, trigger=None):
        self._trigger = trigger

    def get_trigger(self, _id):
        return self._trigger

    def add_trigger(self, _t):
        return False

    def remove_trigger(self, _id):
        return False

    def enable_trigger(self, _id):
        return False

    def disable_trigger(self, _id):
        return False


@pytest.fixture
def errors(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(trig.ch, "error", lambda msg, *a, **k: seen.append(str(msg)))
    monkeypatch.setattr(trig.ch, "success", lambda *a, **k: seen.append("SUCCESS-CLAIMED"))
    return seen


# ── trigger: a failed mutation is loud and non-zero ──────────────────────────

def test_remove_failure_is_not_silent(monkeypatch, errors):
    fake = SimpleNamespace(name="nightly", id="nightly-abc123")
    monkeypatch.setattr(trig, "TriggerManager", lambda: _Manager(trigger=fake))

    with pytest.raises(typer.Exit) as exc:
        trig.remove_trigger("nightly-abc123", force=True)

    assert exc.value.exit_code == 1
    assert any("Failed to remove" in e for e in errors)
    assert "SUCCESS-CLAIMED" not in errors


@pytest.mark.parametrize("handler", ["enable_trigger", "disable_trigger"])
def test_enable_disable_failure_is_not_silent(monkeypatch, errors, handler):
    monkeypatch.setattr(trig, "TriggerManager", lambda: _Manager())

    with pytest.raises(typer.Exit) as exc:
        getattr(trig, handler)("nightly-abc123")

    assert exc.value.exit_code == 1
    assert errors and "SUCCESS-CLAIMED" not in errors


def test_unknown_trigger_is_a_usage_error(monkeypatch, errors):
    """not-found -> 2, matching host/app/db."""
    monkeypatch.setattr(trig, "TriggerManager", lambda: _Manager(trigger=None))

    with pytest.raises(typer.Exit) as exc:
        trig.remove_trigger("ghost", force=True)

    assert exc.value.exit_code == 2
    assert any("not found" in e for e in errors)


def test_a_successful_removal_still_exits_zero(monkeypatch, errors):
    """Anti-vacuity: the tests above must fail for the RIGHT reason. If the handler
    raised unconditionally they would all pass while `trigger remove` was broken."""
    fake = SimpleNamespace(name="nightly", id="nightly-abc123")
    mgr = _Manager(trigger=fake)
    mgr.remove_trigger = lambda _id: True
    monkeypatch.setattr(trig, "TriggerManager", lambda: mgr)

    trig.remove_trigger("nightly-abc123", force=True)  # must NOT raise
    assert "SUCCESS-CLAIMED" in errors


# ── db: the wrapper must not drop the failure bool ───────────────────────────

@pytest.mark.parametrize(
    ("wrapper", "target"),
    [("db_optimize_new", "optimize_table_cmd"), ("db_repair_new", "repair_table_cmd")],
)
def test_db_wrapper_propagates_the_failure_bool(wrapper, target):
    """`False` is the failure signal — dropping it reported success for a rejected
    table name (the SQL-identifier guard), a dead mysql client, and a failed run."""
    import navig.commands.db as db_mod

    ctx = SimpleNamespace(obj={})
    with patch(f"navig.commands.database_advanced.{target}", return_value=False):
        with pytest.raises(typer.Exit) as exc:
            getattr(db_mod, wrapper)(ctx, "some_table")
    assert exc.value.exit_code == 1


@pytest.mark.parametrize(
    ("wrapper", "target"),
    [("db_optimize_new", "optimize_table_cmd"), ("db_repair_new", "repair_table_cmd")],
)
def test_db_wrapper_stays_zero_on_success(wrapper, target):
    """Anti-vacuity for the pair above."""
    import navig.commands.db as db_mod

    ctx = SimpleNamespace(obj={})
    with patch(f"navig.commands.database_advanced.{target}", return_value=True):
        getattr(db_mod, wrapper)(ctx, "some_table")  # must NOT raise
