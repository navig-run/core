"""
Regressions for navig/cli/middleware.py — _classify_operation_type().

T-068 made these labels USER-VISIBLE: `navig ledger show` derives the
green/yellow/red reversibility label from the recorded OperationType
(navig.reversibility's OperationType→label table). The old classifier
substring-matched the RAW command string, so:

    "get "     in "config get log_level"      → FILE_DOWNLOAD (yellow:
                                                 "delete the local copy" —
                                                 for a pure config read)
    "put"      in "--output"                   → FILE_UPLOAD
    "service"  in "app deploy user-service"    → SERVICE_RESTART
    "tunnel "  → REMOTE_COMMAND                 (TUNNEL_START/STOP dead)
    "db "      → DATABASE_QUERY for everything  (DATABASE_DUMP dead)

The fixed classifier keys on the RESOLVED tokens (`navig <resource>
<action>`, via extract_non_global_tokens) and labels pure reads as
READ_QUERY → Reversibility.NONE ("read-only", never a scare colour).
"""

from __future__ import annotations

import json

import pytest

from navig.cli.middleware import _classify_operation_type
from navig.cli.registration import extract_non_global_tokens
from navig.operation_recorder import OperationType

pytestmark = pytest.mark.integration


def classify(argv_tail: list[str]) -> OperationType:
    """Classify exactly as the middleware does: strip global flags first."""
    return _classify_operation_type(extract_non_global_tokens(argv_tail))


# ---------------------------------------------------------------------------
# The misclassification regressions (each was wrong before the fix)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("argv", "expected", "was"),
    [
        # "get " substring → FILE_DOWNLOAD for pure reads
        (["config", "get", "log_level"], OperationType.READ_QUERY, "file_download"),
        (["vault", "get", "github"], OperationType.READ_QUERY, "file_download"),
        # "get " inside a free-text payload argument
        (["run", "apt-get update"], OperationType.REMOTE_COMMAND, "file_download"),
        # "put" substring (no delimiter) matched --output/input anywhere
        (["log", "show", "--output", "x.txt"], OperationType.READ_QUERY, "file_upload"),
        # "service" substring matched argument text
        (["app", "deploy", "user-service"], OperationType.LOCAL_COMMAND, "service_restart"),
        # service reads are reads, not restarts
        (["service", "status", "nginx"], OperationType.READ_QUERY, "service_restart"),
        (["service", "logs", "nginx"], OperationType.READ_QUERY, "service_restart"),
        # tunnel lifecycle: TUNNEL_START/TUNNEL_STOP were unreachable
        (["tunnel", "run", "web"], OperationType.TUNNEL_START, "remote_command"),
        (["tunnel", "remove", "web"], OperationType.TUNNEL_STOP, "remote_command"),
        (["tunnel", "show"], OperationType.READ_QUERY, "remote_command"),
        # db dump has its own type (yellow: "delete the dump file")
        (["db", "dump", "mydb"], OperationType.DATABASE_DUMP, "database_query"),
        # db reads are reads, not "may be a write; unknowable" red
        (["db", "list"], OperationType.READ_QUERY, "database_query"),
        (["db", "tables", "mydb"], OperationType.READ_QUERY, "database_query"),
        # the real upload verb was completely missed
        (["file", "add", "local.txt", "/remote/"], OperationType.FILE_UPLOAD, "local_command"),
        (["file", "edit", "/etc/motd"], OperationType.FILE_MODIFY, "local_command"),
        (["file", "remove", "/tmp/x"], OperationType.FILE_DELETE, "local_command"),
        # generic reads were red local_commands
        (["host", "list"], OperationType.READ_QUERY, "local_command"),
        (["config", "show"], OperationType.READ_QUERY, "local_command"),
        (["status"], OperationType.READ_QUERY, "local_command"),
        (["doctor"], OperationType.READ_QUERY, "local_command"),
        # `navig run` executes on the active host
        (["run", "ls -la"], OperationType.REMOTE_COMMAND, "local_command"),
        # `navig flow run` is the real workflow verb ("workflow run" never matched)
        (["flow", "run", "deploy"], OperationType.WORKFLOW_RUN, "local_command"),
        # config set pre-labels as config_change (the capture seam refines it)
        (["config", "set", "log_level", "DEBUG"], OperationType.CONFIG_CHANGE, "local_command"),
    ],
)
def test_misclassification_fixed(argv, expected, was):
    got = classify(argv)
    assert got == expected, (
        f"navig {' '.join(argv)} classified {got.value!r}; "
        f"expected {expected.value!r} (old heuristic said {was!r})"
    )


# ---------------------------------------------------------------------------
# Pinned behaviour that was already correct — must not drift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["file", "get", "/etc/hosts"], OperationType.FILE_DOWNLOAD),
        (["host", "use", "prod"], OperationType.HOST_SWITCH),
        (["docker", "run", "nginx"], OperationType.DOCKER_COMMAND),
        (["docker", "restart", "web"], OperationType.DOCKER_COMMAND),
        (["service", "restart", "nginx"], OperationType.SERVICE_RESTART),
        (["db", "query", "SELECT 1"], OperationType.DATABASE_QUERY),
        (["local", "ls"], OperationType.LOCAL_COMMAND),
        (["backup", "create"], OperationType.LOCAL_COMMAND),
        ([], OperationType.LOCAL_COMMAND),
    ],
)
def test_correct_classifications_pinned(argv, expected):
    assert classify(argv) == expected


# ---------------------------------------------------------------------------
# Token discipline
# ---------------------------------------------------------------------------

def test_global_flags_do_not_confuse_classification():
    """`navig --host prod db list` must classify on `db list`, not the flags."""
    assert classify(["--host", "prod", "db", "list"]) == OperationType.READ_QUERY


def test_free_payload_resources_ignore_read_verbs():
    """`navig run list` executes `list` remotely — it is not a listing."""
    assert classify(["run", "list"]) == OperationType.REMOTE_COMMAND
    assert classify(["ssh", "status"]) == OperationType.REMOTE_COMMAND


def test_short_aliases_classify_like_their_resource():
    assert classify(["f", "get", "/etc/hosts"]) == OperationType.FILE_DOWNLOAD
    assert classify(["t", "run", "web"]) == OperationType.TUNNEL_START
    assert classify(["h", "use", "prod"]) == OperationType.HOST_SWITCH
    assert classify(["r", "whoami"]) == OperationType.REMOTE_COMMAND


# ---------------------------------------------------------------------------
# The label side: READ_QUERY is "read-only", never a scare colour, never
# an undo candidate — and old/foreign ledger entries still load.
# ---------------------------------------------------------------------------

def test_read_query_labels_as_none():
    from navig.reversibility import Reversibility
    from navig.reversibility import classify as rev_classify

    assert rev_classify("read_query") == Reversibility.NONE
    # even with (accidental) undo_data it must not turn green: green is a
    # promise `navig undo` can act on, and reads are not in GREEN_CAPABLE_TYPES
    assert rev_classify("read_query", {"anything": 1}) == Reversibility.NONE


def test_read_only_glyph_is_distinct_from_legacy_dash():
    from navig.reversibility import label_glyph

    assert "read-only" in label_glyph("none")
    assert label_glyph("none") != label_glyph("")


def test_recorded_read_is_never_undoable(tmp_path):
    from navig.operation_recorder import OperationRecord, OperationRecorder, OperationStatus
    from navig.undo import find_candidates

    recorder = OperationRecorder(history_dir=tmp_path)
    record = OperationRecord(
        command="navig config get log_level",
        operation_type=OperationType.READ_QUERY,
        status=OperationStatus.SUCCESS,
    )
    recorder.record(record)

    assert record.reversibility == "none"
    assert record.reversible is False
    assert find_candidates(recorder) == []


def test_from_dict_degrades_unknown_enum_values(tmp_path):
    """A ledger line written by a NEWER navig must not break iteration."""
    from navig.operation_recorder import OperationRecord, OperationRecorder

    rec = OperationRecord.from_dict(
        {"id": "op-x", "command": "navig future thing",
         "operation_type": "from_the_future", "status": "warp"}
    )
    assert rec.operation_type == OperationType.OTHER
    assert rec.status.value == "pending"

    # end-to-end: a foreign line in the file must not kill iter_operations
    recorder = OperationRecorder(history_dir=tmp_path)
    line = {"id": "op-y", "command": "navig future thing",
            "operation_type": "from_the_future", "status": "success",
            "timestamp": "2026-07-16T00:00:00+00:00"}
    (tmp_path / "operations.jsonl").write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )
    records = list(recorder.iter_operations(limit=10))
    assert len(records) == 1
    assert records[0].operation_type == OperationType.OTHER
