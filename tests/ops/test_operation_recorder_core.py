import json

from navig.operation_recorder import OperationRecord, OperationRecorder
import pytest

pytestmark = pytest.mark.integration


def test_record_indexes_new_entry_with_physical_line_offsets(tmp_path):
    recorder = OperationRecorder(history_dir=tmp_path)

    # Seed history with malformed and blank lines before a valid entry.
    recorder.history_file.write_text(
        "{bad-json\n\n"
        + json.dumps(
            OperationRecord(command="old", id="op-old", timestamp="2026-01-01T00:00:00Z").to_dict()
        )
        + "\n",
        encoding="utf-8",
    )

    new_record = OperationRecord(command="new")
    new_id = recorder.record(new_record)

    loaded = recorder.get_operation(new_id)
    assert loaded is not None
    assert loaded.id == new_id
    assert loaded.command == "new"
