"""
Focused regressions for navig/cli/middleware.py — init_operation_recorder()
skip-record detection.

The core regression being guarded:

    command_str = " ".join(sys.argv[1:])  →  "--host prod db list"
    "-h" in command_str  →  True  (FALSE POSITIVE — "-h" is a substring of "--host")

After the fix, the skip check uses the non-global token string:
    _cmd_str_for_skip = "db list"
    "-h" in "db list"  →  False  (recording proceeds correctly)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

import navig.cli.middleware as middleware_mod

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke_recorder(argv_tail: list[str], monkeypatch) -> dict:
    """
    Run init_operation_recorder() with the given argv tail and return ctx.obj.

    The operation_recorder dependency is mocked so the test is hermetic.
    Returns the final ctx.obj dict so callers can inspect whether
    ``_operation_record`` was set (recording happened) or not (skipped).
    """
    monkeypatch.setattr(middleware_mod.sys, "argv", ["navig", *argv_tail])

    fake_record = object()
    fake_recorder = MagicMock()
    fake_recorder.start_operation.return_value = fake_record

    ctx = MagicMock()
    ctx.obj = {}

    with patch("navig.operation_recorder.get_operation_recorder", return_value=fake_recorder):
        middleware_mod.init_operation_recorder(
            ctx=ctx, host=None, app=None, verbose=False
        )

    return ctx.obj


# ---------------------------------------------------------------------------
# Core false-positive guard (the Phase C fix)
# ---------------------------------------------------------------------------

def test_host_prefixed_db_command_is_not_skipped(monkeypatch):
    """``navig --host prod db list`` must NOT be skipped.

    Before the fix: ``"-h" in "--host prod db list"`` → True (false positive).
    After the fix:  ``"-h" in "db list"``              → False (correct).
    """
    obj = _invoke_recorder(["--host", "prod", "db", "list"], monkeypatch)
    assert "_operation_record" in obj, (
        "Recording was incorrectly skipped for navig --host prod db list "
        "(false-positive from '-h' being a substring of '--host')"
    )


def test_host_prefixed_run_command_is_not_skipped(monkeypatch):
    """``navig --host prod run ls`` must NOT be skipped."""
    obj = _invoke_recorder(["--host", "prod", "run", "ls"], monkeypatch)
    assert "_operation_record" in obj


def test_app_prefixed_file_command_is_not_skipped(monkeypatch):
    """``navig --app myapp file list /tmp`` must NOT be skipped."""
    obj = _invoke_recorder(["--app", "myapp", "file", "list", "/tmp"], monkeypatch)
    assert "_operation_record" in obj


# ---------------------------------------------------------------------------
# Legitimate skip cases — these must still be skipped after the fix
# ---------------------------------------------------------------------------

def test_bare_help_is_skipped(monkeypatch):
    """``navig help`` must be skipped (meta command)."""
    obj = _invoke_recorder(["help"], monkeypatch)
    assert "_operation_record" not in obj


# ⚠ The note that used to sit here claimed `--help` skipping was "dead code but
# benign, because Typer exits before the atexit op-recorder fires". It is not
# benign: the ROOT callback starts the record (and writes the in-flight marker)
# before Click's eager help handler runs on the SUBCOMMAND, so `navig apply
# --help` landed in the ledger as a red-risk operation. Measured in a fresh
# sandbox — `navig ledger show` listed it. The flags are now matched as whole
# tokens of the raw argv, which is the only place they still exist by the time
# the skip check runs.


def test_prefixed_help_is_skipped(monkeypatch):
    """``navig --host prod help`` must still be skipped (meta command after global flags)."""
    obj = _invoke_recorder(["--host", "prod", "help"], monkeypatch)
    assert "_operation_record" not in obj


def test_history_is_skipped(monkeypatch):
    """``navig history list`` must be skipped."""
    obj = _invoke_recorder(["history", "list"], monkeypatch)
    assert "_operation_record" not in obj


# ---------------------------------------------------------------------------
# Whole-token matching — a remote payload must never be mistaken for a flag
# ---------------------------------------------------------------------------
#
# The skip list used to be a SUBSTRING scan of the joined command, so every
# `navig run` whose shell payload contained `-h`, `-v`, `help`, `dashboard`, …
# was silently dropped from the audit ledger: two consecutive `navig run`s,
# both exit 0, one line on the chain. `df -h`, `free -h`, `ls -lh`, `grep -v`
# are the flags admins type most — the hole was shaped exactly like real use.


@pytest.mark.parametrize(
    "payload",
    ["df -h /", "free -h", "ls -lh /var/log", "grep -v noise app.log", "cat helper.log", "open dashboard"],
)
def test_a_remote_payload_containing_a_keyword_is_still_recorded(monkeypatch, payload):
    obj = _invoke_recorder(["run", payload], monkeypatch)
    assert "_operation_record" in obj, payload


def test_help_on_a_subcommand_is_skipped(monkeypatch):
    """`navig apply --help` is a help screen, not an executed (red) operation."""
    obj = _invoke_recorder(["apply", "--help"], monkeypatch)
    assert "_operation_record" not in obj


def test_root_version_flag_is_skipped(monkeypatch):
    obj = _invoke_recorder(["--version"], monkeypatch)
    assert "_operation_record" not in obj


def test_short_h_is_the_host_flag_not_help(monkeypatch):
    """`-h` consumes a host name on this CLI; the command behind it is real and recorded."""
    obj = _invoke_recorder(["-h", "prod", "run", "uptime"], monkeypatch)
    assert "_operation_record" in obj


def test_ledger_reads_are_skipped_by_resource_not_substring(monkeypatch):
    assert "_operation_record" not in _invoke_recorder(["ledger", "show"], monkeypatch)
    assert "_operation_record" not in _invoke_recorder(["audit", "tail"], monkeypatch)
    assert "_operation_record" not in _invoke_recorder(["trigger", "test", "x"], monkeypatch)
    # ...but `trigger fire` is a real operation
    assert "_operation_record" in _invoke_recorder(["trigger", "fire", "x"], monkeypatch)


def test_a_dry_run_is_recorded_as_read_only(monkeypatch):
    """A dry run writes nothing; the ledger must not paint it with the real command's risk."""
    from navig.operation_recorder import OperationType

    monkeypatch.setattr(middleware_mod.sys, "argv", ["navig", "apply", "server-health", "--dry-run"])
    fake_recorder = MagicMock()
    fake_recorder.start_operation.return_value = object()
    ctx = MagicMock()
    ctx.obj = {}
    with patch("navig.operation_recorder.get_operation_recorder", return_value=fake_recorder):
        middleware_mod.init_operation_recorder(ctx=ctx, host=None, app=None, verbose=False)
    assert "_operation_record" in ctx.obj
    kwargs = fake_recorder.start_operation.call_args.kwargs
    assert kwargs["operation_type"] == OperationType.READ_QUERY


# ---------------------------------------------------------------------------
# A command wrapped in RecordedOperation must enrich the middleware record, not
# write a sibling — `navig apply` produced two ledger lines per run.
# ---------------------------------------------------------------------------


def test_recorded_operation_adopts_the_middleware_record_when_asked(monkeypatch):
    from typer.testing import CliRunner

    from navig.operation_recorder import OperationRecord, OperationType, RecordedOperation

    app = typer.Typer()
    seen: dict[str, object] = {}
    started: list[str] = []

    @app.callback()
    def _root(ctx: typer.Context) -> None:
        ctx.ensure_object(dict)
        ctx.obj["_operation_record"] = OperationRecord(
            id="op-mw", timestamp="t", command="navig apply x --dry-run",
            operation_type=OperationType.READ_QUERY,
        )

    @app.command()
    def apply(ctx: typer.Context) -> None:
        with RecordedOperation(
            command="navig apply x --dry-run",
            op_type=OperationType.WORKFLOW_RUN,
            tags=["block", "x"],
            claim=("apply",),
        ) as rec:
            seen["id"] = rec._record.id
            seen["type"] = rec._record.operation_type
            seen["detached"] = "_operation_record" not in ctx.obj
            seen["tags"] = list(rec._record.tags or [])

    import navig.operation_recorder as orec

    real_start = orec.OperationRecorder.start_operation

    def _spy(self, *a, **k):
        started.append(k.get("command", "?"))
        return real_start(self, *a, **k)

    monkeypatch.setattr(orec.OperationRecorder, "start_operation", _spy)
    monkeypatch.setattr(orec.OperationRecorder, "complete_operation", lambda self, *a, **k: None)

    result = CliRunner().invoke(app, ["apply"])
    assert result.exit_code == 0, result.output
    assert seen["id"] == "op-mw", "the middleware record was not adopted"
    assert seen["detached"] is True
    assert seen["type"] == OperationType.READ_QUERY, "a dry run must stay read-only"
    assert "block" in seen["tags"]
    assert started == [], f"a second record was started: {started}"


def test_recorded_operation_without_claim_still_records_on_its_own(monkeypatch):
    import navig.operation_recorder as orec
    from navig.operation_recorder import OperationType, RecordedOperation

    started: list[str] = []
    real_start = orec.OperationRecorder.start_operation
    monkeypatch.setattr(
        orec.OperationRecorder, "start_operation",
        lambda self, *a, **k: (started.append(k.get("command")), real_start(self, *a, **k))[1],
    )
    monkeypatch.setattr(orec.OperationRecorder, "complete_operation", lambda self, *a, **k: None)
    with RecordedOperation(command="navig lib call", op_type=OperationType.OTHER):
        pass
    assert started == ["navig lib call"]
