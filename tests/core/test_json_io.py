"""Unit tests for the JSON-store safe read-modify-write primitives (json_io).

These lock the wipe-prevention contract: a transient-but-persistent read failure must
RAISE on the mutating path (so a load-modify-save aborts instead of overwriting every
record with {}), while missing/empty degrade to the default and corrupt is quarantined.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.core import json_io
from navig.core.json_io import (
    JsonReadError,
    atomic_write_json,
    load_json_for_update,
    load_json_safe,
)

# ── load_json_for_update ────────────────────────────────────────────────────


def test_reads_a_valid_dict(tmp_path: Path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
    assert load_json_for_update(f, default={}) == {"a": 1, "b": 2}


def test_reads_a_valid_list(tmp_path: Path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_json_for_update(f, default=[]) == [1, 2, 3]


def test_missing_returns_fresh_default(tmp_path: Path):
    assert load_json_for_update(tmp_path / "nope.json", default={}) == {}
    assert load_json_for_update(tmp_path / "nope.json", default=[]) == []


def test_empty_file_returns_default(tmp_path: Path):
    f = tmp_path / "s.json"
    f.write_text("   \n", encoding="utf-8")
    assert load_json_for_update(f, default={}) == {}


def test_default_is_copied_not_shared(tmp_path: Path):
    shared = {"seed": 1}
    out = load_json_for_update(tmp_path / "nope.json", default=shared)
    out["mutated"] = True
    assert shared == {"seed": 1}  # the caller's default was not mutated


def test_corrupt_is_quarantined_and_returns_default(tmp_path: Path):
    f = tmp_path / "s.json"
    f.write_text("{not: valid json,,,", encoding="utf-8")
    assert load_json_for_update(f, default={}) == {}
    # the bytes are preserved beside the store, not silently lost
    assert (tmp_path / "s.json.corrupt").read_text(encoding="utf-8") == "{not: valid json,,,"


def test_type_mismatch_is_treated_as_corrupt(tmp_path: Path):
    """A dict-store file that parsed to a list must NOT be handed back — merging a
    mutation into {} and saving would drop the list's data. Quarantine + default."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_json_for_update(f, default={}) == {}
    assert (tmp_path / "s.json.corrupt").exists()


def test_transient_read_failure_raises_on_the_mutating_path(tmp_path: Path, monkeypatch):
    """The whole point: a lock that survives the retries must RAISE, so a mutator aborts
    the save instead of wiping the store with {}."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("sharing violation")

    monkeypatch.setattr(json_io, "read_text_retrying", _boom)
    with pytest.raises(JsonReadError):
        load_json_for_update(f, default={})


# ── load_json_safe ──────────────────────────────────────────────────────────


def test_safe_degrades_on_transient_failure_instead_of_raising(tmp_path: Path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("sharing violation")

    monkeypatch.setattr(json_io, "read_text_retrying", _boom)
    assert load_json_safe(f, default={}) == {}  # read-only view never crashes


def test_safe_reads_valid_data(tmp_path: Path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"x": 9}), encoding="utf-8")
    assert load_json_safe(f, default={}) == {"x": 9}


# ── atomic_write_json + round-trip ──────────────────────────────────────────


def test_atomic_write_round_trips(tmp_path: Path):
    f = tmp_path / "s.json"
    data = {"schedules": {"nightly": {"enabled": True, "count": 3}}}
    atomic_write_json(data, f)
    assert load_json_for_update(f, default={}) == data
    # valid JSON on disk, and the temp files were cleaned up
    assert json.loads(f.read_text(encoding="utf-8")) == data
    assert list(tmp_path.glob("*.navig~")) == []


def test_write_then_mutate_then_write_preserves_records(tmp_path: Path):
    """The end-to-end load-modify-save must keep siblings — the behaviour a wipe breaks."""
    f = tmp_path / "s.json"
    atomic_write_json({"a": {"v": 1}}, f)

    store = load_json_for_update(f, default={})
    store["b"] = {"v": 2}
    atomic_write_json(store, f)

    assert load_json_for_update(f, default={}) == {"a": {"v": 1}, "b": {"v": 2}}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
