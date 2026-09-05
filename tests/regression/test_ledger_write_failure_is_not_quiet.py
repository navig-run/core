"""A failed ledger append must not be a dim line that erases the last trace.

`OperationRecorder.record()` caught `OSError` and reported it with `ch.dim` — the
quietest sink there is — then returned `record.id` as if the line had been written.
The operation ITSELF already ran; what went missing was the only record of it:

* `navig undo` looks operations up by id (`recorder.get_operation(op_id)`), so the id
  this call hands back resolves to nothing;
* `navig insights` reads the same ledger and simply under-counts, with no gap to see.

Worse, the completion path then called `clear_inflight(record.id)` unconditionally. The
in-flight marker exists precisely so an operation with no terminal line can be reaped
into an honest `interrupted` entry — clearing it after a failed append removed the last
evidence the command ever ran.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.operation_recorder import OperationRecord, OperationRecorder


@pytest.fixture
def recorder(tmp_path: Path) -> OperationRecorder:
    return OperationRecorder(history_dir=tmp_path)


def _fail_open(monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder) -> None:
    """Make the ledger append fail the way a full/locked disk does."""
    real_open = open

    def _open(file, mode="r", *a, **k):  # noqa: ANN001
        if str(file) == str(recorder.history_file) and "a" in str(mode):
            raise OSError("no space left on device")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", _open)


def test_a_failed_append_is_reported_at_warning_not_dim(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    from navig import console_helper as ch

    warnings: list[str] = []
    dims: list[str] = []
    monkeypatch.setattr(ch, "warning", lambda m, *a, **k: warnings.append(m))
    monkeypatch.setattr(ch, "dim", lambda m, *a, **k: dims.append(m))

    _fail_open(monkeypatch, recorder)
    recorder.record(OperationRecord(command="navig host remove prod"))

    assert warnings, "a lost audit line was reported only through a quiet sink"
    joined = " ".join(warnings)
    assert "undo" in joined and "insights" in joined, (
        "the message must say what the user has lost, not just that a write failed"
    )


def test_a_failed_append_records_an_incident(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    """The daemon records operations too, where nobody is reading the terminal."""
    from navig import console_helper as ch
    from navig.core import incidents

    monkeypatch.setattr(ch, "warning", lambda *a, **k: None)
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda kind, **f: seen.append((kind, f)))

    _fail_open(monkeypatch, recorder)
    recorder.record(OperationRecord(command="navig host remove prod"))

    assert seen, "no incident recorded for a lost ledger line"
    kind, fields = seen[0]
    assert kind == incidents.STORE_WRITE_FAILED
    assert fields.get("store") == "operations"


def test_the_incident_type_is_described() -> None:
    """The notify producer renders from DESCRIPTIONS; an unlisted type pushes an empty
    explanation, which turns a health signal into noise."""
    from navig.core import incidents

    assert incidents.STORE_WRITE_FAILED in incidents.DESCRIPTIONS
    assert incidents.DESCRIPTIONS[incidents.STORE_WRITE_FAILED].strip()


def test_recording_the_incident_never_breaks_the_call(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    from navig import console_helper as ch
    from navig.core import incidents

    monkeypatch.setattr(ch, "warning", lambda *a, **k: None)

    def _explode(*a: object, **k: object) -> None:
        raise RuntimeError("incident log is on fire")

    monkeypatch.setattr(incidents, "record", _explode)
    _fail_open(monkeypatch, recorder)

    recorder.record(OperationRecord(command="x"))  # must not raise


def _completion_clears(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder, *, fail: bool
) -> list[str]:
    """Drive `complete_operation` — NOT `record` — and report which ids it cleared.

    `record()` does not touch the in-flight marker; `complete_operation()` does. An
    earlier draft of this test called `record()` and asserted nothing was cleared,
    which passed on the broken code too because the call never reached the clear at
    all. Drive the method that owns the decision.
    """
    from navig import console_helper as ch

    monkeypatch.setattr(ch, "warning", lambda *a, **k: None)

    cleared: list[str] = []
    monkeypatch.setattr(recorder, "clear_inflight", lambda op_id: cleared.append(op_id))

    if fail:
        _fail_open(monkeypatch, recorder)
    recorder.complete_operation(
        OperationRecord(command="navig host remove prod"), success=True
    )
    return cleared


def test_a_failed_append_leaves_the_inflight_marker_alone(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    """The sharpest consequence. The marker is what `navig ledger reap` turns into an
    honest `interrupted` entry — clearing it after a failed append erased the last
    evidence that the command ran at all."""
    cleared = _completion_clears(monkeypatch, recorder, fail=True)

    assert cleared == [], "cleared the in-flight marker for a line that was never written"
    assert recorder._last_record_failed is True


def test_a_successful_completion_still_clears_the_marker(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    """The partner, and the anti-vacuity check for the test above: if nothing is ever
    cleared, `cleared == []` proves nothing. A fix that simply stopped clearing would
    leave every completed operation looking interrupted."""
    cleared = _completion_clears(monkeypatch, recorder, fail=False)

    assert len(cleared) == 1, "a normal completion must still drop its in-flight marker"
    assert recorder._last_record_failed is False


def test_a_successful_append_is_silent_and_persisted(
    recorder: OperationRecorder
) -> None:
    """The normal path must stay quiet and must actually write — otherwise the tests
    above would pass against a recorder that records nothing."""
    op_id = recorder.record(OperationRecord(command="navig host list"))

    assert recorder.history_file.exists()
    assert op_id and recorder.get_operation(op_id) is not None
