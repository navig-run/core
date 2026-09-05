"""`navig --dry-run history clear` deleted the whole history and reported success.

``--dry-run`` is a GLOBAL option: ``navig/cli/__init__.py`` sets ``ctx.obj["dry_run"]``,
and ``ctx.obj`` is the ``opts`` dict every command in this module receives. All three
destructive verbs ignored it:

* ``clear`` never looked at it — ``navig --dry-run history clear --yes`` unlinked the
  history file and printed "✓ Cleared 2 operations from history";
* ``undo`` never looked at it — the undo was performed, and recorded;
* ``replay`` honoured only its OWN ``--dry-run``, so the global one re-executed the
  command.

A flag documented as "show what would be done without executing" has to mean that
everywhere, or it is worse than not existing — it invites exactly the command someone
types when they are unsure.

Clearing is the sharpest of the three: those records ARE the undo history, so it
permanently removes the ability to ``navig undo`` anything already done. The old prompt
said only "Clear all operation history?", which reads like tidying a log.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.commands.history import (
    _is_dry_run,
    clear_history,
    replay_operation,
    undo_operation,
)
from navig.operation_recorder import OperationType, get_operation_recorder


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    import navig.operation_recorder as mod

    monkeypatch.setattr(mod, "_recorder", None, raising=False)
    rec = get_operation_recorder()
    rec.history_file = tmp_path / "operations.jsonl"
    rec.history_file.parent.mkdir(parents=True, exist_ok=True)
    for command in ("navig file add A", "navig file add B"):
        record = rec.start_operation(command=command, operation_type=OperationType.FILE_UPLOAD)
        rec.complete_operation(record, success=True)
    monkeypatch.setattr("navig.commands.history.get_operation_recorder", lambda: rec)
    return rec


# ── the resolver ────────────────────────────────────────────────────────────────


def test_the_global_flag_counts() -> None:
    assert _is_dry_run({"dry_run": True}) is True


def test_an_explicit_flag_counts() -> None:
    assert _is_dry_run({}, True) is True


def test_neither_means_go_ahead() -> None:
    assert _is_dry_run({"dry_run": False}) is False
    assert _is_dry_run(None) is False


# ── clear ───────────────────────────────────────────────────────────────────────


def test_dry_run_clear_deletes_nothing(recorder, capsys) -> None:
    """The whole bug: this used to unlink the history file."""
    clear_history(opts={"dry_run": True, "yes": True})

    assert recorder.count() == 2
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Would clear 2" in out


def test_dry_run_clear_does_not_claim_it_cleared(recorder, capsys) -> None:
    clear_history(opts={"dry_run": True, "yes": True})

    assert "Cleared 2 operations" not in capsys.readouterr().out


def test_clear_still_clears_for_real(recorder, capsys) -> None:
    clear_history(opts={"yes": True})

    assert recorder.count() == 0
    assert "Cleared 2 operations" in capsys.readouterr().out


def test_the_prompt_says_undo_history_is_lost(recorder, capsys) -> None:
    """"Clear all operation history?" reads like tidying a log. It is not."""
    with patch("rich.prompt.Confirm.ask", return_value=False):
        clear_history(opts={})

    out = capsys.readouterr().out
    assert "undo" in out.lower()
    assert "2 operations" in out
    assert recorder.count() == 2  # declined


def test_declining_the_prompt_keeps_everything(recorder, capsys) -> None:
    with patch("rich.prompt.Confirm.ask", return_value=False):
        clear_history(opts={})

    assert recorder.count() == 2
    assert "Cancelled" in capsys.readouterr().out


# ── undo ────────────────────────────────────────────────────────────────────────


def test_dry_run_undo_performs_nothing(recorder, capsys) -> None:
    with patch("navig.undo.ensure_undoable"), patch("navig.undo.check_drift"), patch(
        "navig.undo.describe_undo", return_value="delete the uploaded copy"
    ), patch("navig.undo.perform_undo") as perform:
        undo_operation("1", opts={"dry_run": True, "yes": True})

    perform.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out


def test_dry_run_undo_records_no_undo_operation(recorder) -> None:
    """A preview must not leave an `navig undo …` entry in the history either."""
    before = recorder.count()

    with patch("navig.undo.ensure_undoable"), patch("navig.undo.check_drift"), patch(
        "navig.undo.describe_undo", return_value="delete the uploaded copy"
    ), patch("navig.undo.perform_undo"):
        undo_operation("1", opts={"dry_run": True, "yes": True})

    assert recorder.count() == before


# ── replay ──────────────────────────────────────────────────────────────────────


def test_dry_run_replay_executes_nothing_via_the_global_flag(recorder, capsys) -> None:
    """replay honoured only its own --dry-run; the global one ran the command."""
    with patch("subprocess.run") as run:
        replay_operation("1", opts={"dry_run": True, "yes": True})

    run.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out


def test_the_explicit_replay_flag_still_works(recorder, capsys) -> None:
    with patch("subprocess.run") as run:
        replay_operation("1", dry_run=True, opts={})

    run.assert_not_called()


def test_replay_without_any_dry_run_still_executes(recorder) -> None:
    fake = MagicMock()
    fake.returncode = 0
    with patch("subprocess.run", return_value=fake) as run, patch(
        "rich.prompt.Confirm.ask", return_value=True
    ):
        replay_operation("1", opts={})

    run.assert_called()
