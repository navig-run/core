"""`navig history undo 0` undid the most recent operation. Nobody asked it to.

Operation indices are 1-based — the help says "1 = last". Three copies of the same
lookup accepted `0` anyway::

    index = int(op_id) - 1                                   # 0 -> -1
    operations = list(recorder.iter_operations(limit=index + 1))   # limit=0
    if index >= len(operations):                             # -1 >= 1 -> False
        ...
    op = operations[index]                                   # operations[-1]

`iter_operations` checks `count >= limit` *after* yielding, so `limit=0` returned one
record — the most recent. The bounds check compared a negative index against a length
and passed, and `[-1]` then selected that record. So `0` silently resolved to the same
operation as `1`.

Two of the three callers are destructive: `undo` reverses the operation and `replay`
re-executes the command. `history.py` had no tests at all.

Both halves are fixed and pinned here: the lookup rejects an index below 1, and
`iter_operations(limit<=0)` yields nothing rather than one row.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from navig.commands.history import _resolve_operation
from navig.operation_recorder import OperationType, get_operation_recorder


def _recorder_with(*commands: str) -> MagicMock:
    """A recorder whose iter_operations honours `limit` the way the real one does."""
    records = [SimpleNamespace(id=f"op-{i}", command=c) for i, c in enumerate(commands)]

    def _iter(limit: int = 100, **_kw):
        return iter(records[:limit] if limit > 0 else [])

    recorder = MagicMock()
    recorder.iter_operations.side_effect = _iter
    recorder.get_operation.side_effect = lambda oid: next(
        (r for r in records if r.id == oid), None
    )
    return recorder


# ── the index contract ──────────────────────────────────────────────────────────


def test_index_zero_is_rejected(capsys) -> None:
    """The whole bug: `0` used to resolve to the newest operation and undo it."""
    recorder = _recorder_with("newest", "older")

    assert _resolve_operation(recorder, "0") is None
    assert "indices start at 1" in capsys.readouterr().out


def test_index_one_is_the_most_recent(capsys) -> None:
    recorder = _recorder_with("newest", "older")

    assert _resolve_operation(recorder, "1").command == "newest"


def test_index_two_is_the_one_before(capsys) -> None:
    recorder = _recorder_with("newest", "older")

    assert _resolve_operation(recorder, "2").command == "older"


def test_an_index_past_the_end_is_rejected(capsys) -> None:
    recorder = _recorder_with("only")

    assert _resolve_operation(recorder, "5") is None
    assert "No operation at index 5" in capsys.readouterr().out


def test_a_record_id_resolves(capsys) -> None:
    recorder = _recorder_with("newest", "older")

    assert _resolve_operation(recorder, "op-1").command == "older"


def test_an_unknown_id_is_rejected(capsys) -> None:
    recorder = _recorder_with("newest")

    assert _resolve_operation(recorder, "does-not-exist") is None
    assert "Operation not found" in capsys.readouterr().out


def test_index_zero_never_reaches_the_recorder(capsys) -> None:
    """Refusing early matters: a limit=0 read is what produced the phantom row."""
    recorder = _recorder_with("newest")

    _resolve_operation(recorder, "0")

    recorder.iter_operations.assert_not_called()


# ── the underlying limit contract, on the real recorder ─────────────────────────


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    import navig.operation_recorder as mod

    monkeypatch.setattr(mod, "_recorder", None, raising=False)
    rec = get_operation_recorder()
    rec.history_file = tmp_path / "operations.jsonl"
    rec.history_file.parent.mkdir(parents=True, exist_ok=True)
    for command in ("navig file add A", "navig file add B", "navig file add C"):
        record = rec.start_operation(command=command, operation_type=OperationType.FILE_UPLOAD)
        rec.complete_operation(record, success=True)
    return rec


def test_limit_zero_yields_nothing(recorder) -> None:
    """"Maximum number of results: 0" must not return a result."""
    assert list(recorder.iter_operations(limit=0)) == []


def test_a_negative_limit_yields_nothing(recorder) -> None:
    assert list(recorder.iter_operations(limit=-1)) == []


def test_limit_one_yields_the_most_recent(recorder) -> None:
    got = list(recorder.iter_operations(limit=1))

    assert [o.command for o in got] == ["navig file add C"]


def test_limit_two_yields_two_newest_first(recorder) -> None:
    got = list(recorder.iter_operations(limit=2))

    assert [o.command for o in got] == ["navig file add C", "navig file add B"]


def test_end_to_end_index_zero_cannot_select_an_operation(recorder, capsys) -> None:
    """Against the real recorder, not a stub: `0` resolves to nothing at all."""
    assert _resolve_operation(recorder, "1") is not None      # sanity: data is there
    assert _resolve_operation(recorder, "0") is None
