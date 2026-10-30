"""An append-only JSONL log must not grow without bound.

The incident log (config_incidents.jsonl) is written by every self-healing path
in the daemon, and a stuck loop writes to it on every heartbeat — measured: 294
entries in ~5 weeks on one install, all from one unfixable issue re-raised every
few hours. Readers cap by AGE (30 days), so the FILE only ever grows.
"""

from __future__ import annotations

import json

import pytest

from navig.core import yaml_io


@pytest.fixture
def perf_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(yaml_io, "_perf_dir", lambda: tmp_path)
    return tmp_path


def test_a_log_under_the_cap_is_left_alone(perf_dir):
    for i in range(50):
        yaml_io.log_shadow_anomaly("t", "ev", {"i": i})

    lines = (perf_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50


def test_a_log_over_the_cap_is_trimmed_to_the_newest_entries(perf_dir, monkeypatch):
    """Newest survive, oldest go, and the cut lands on a line boundary so every
    remaining line still parses."""
    monkeypatch.setattr(yaml_io, "_JSONL_CAP_BYTES", 4000)
    monkeypatch.setattr(yaml_io, "_JSONL_KEEP_BYTES", 2000)
    for i in range(200):
        yaml_io.log_shadow_anomaly("t", "ev", {"i": i, "pad": "x" * 20})

    text = (perf_dir / "t.jsonl").read_text(encoding="utf-8")
    assert len(text.encode()) <= 4000 + 200, "trim never ran"
    rows = [json.loads(line) for line in text.splitlines()]  # every line must parse
    assert rows, "trim deleted everything"
    assert rows[-1]["data"]["i"] == 199, "the newest entry was lost"
    assert rows[0]["data"]["i"] > 0, "the oldest entry survived a trim"
    assert [r["data"]["i"] for r in rows] == list(range(rows[0]["data"]["i"], 200)), (
        "trim broke ordering or dropped a middle entry"
    )


def test_the_trim_never_raises(perf_dir, monkeypatch):
    """A failed trim is the old (unbounded) behaviour, not a new failure."""
    monkeypatch.setattr(yaml_io, "_JSONL_CAP_BYTES", 1)

    def boom(*a, **k):
        raise OSError("locked")

    import pathlib

    monkeypatch.setattr(pathlib.Path, "read_bytes", boom)

    yaml_io.log_shadow_anomaly("t", "ev", {"i": 1})  # must not raise
    assert (perf_dir / "t.jsonl").exists()


def _fill_past_cap(perf_dir, monkeypatch):
    monkeypatch.setattr(yaml_io, "_JSONL_CAP_BYTES", 4000)
    monkeypatch.setattr(yaml_io, "_JSONL_KEEP_BYTES", 2000)
    log = perf_dir / "t.jsonl"
    with log.open("w", encoding="utf-8") as fh:
        for i in range(100):
            fh.write(json.dumps({"ts": 0, "event": "ev", "data": {"i": i, "pad": "x" * 20}}) + "\n")
    return log


def test_an_append_after_the_trim_snapshot_survives_the_rewrite(perf_dir, monkeypatch):
    """A writer that does not take the lock (the Rust desktop apps append to the
    incident log) can land between the trim's read and its replace. That entry was
    overwritten by the older snapshot."""
    import pathlib

    log = _fill_past_cap(perf_dir, monkeypatch)
    real_read = pathlib.Path.read_bytes

    def read_then_foreign_append(self):
        data = real_read(self)
        if self == log:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": 0, "event": "late", "data": {}}) + "\n")
        return data

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_then_foreign_append)
    yaml_io._cap_jsonl(log)

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(log.read_bytes()) < 4000, "trim never ran"
    assert rows[-1]["event"] == "late", "an append made during the trim was lost"


def test_a_python_append_waits_for_a_running_trim(perf_dir, monkeypatch):
    """Two Python writers: the second append must not land inside the first one's
    read-rewrite window — it waits for the lock and is still on disk afterwards."""
    import threading
    import time as _time

    log = _fill_past_cap(perf_dir, monkeypatch)
    in_trim = threading.Event()
    real_cap = yaml_io._cap_jsonl

    def slow_cap(path):
        if threading.current_thread().name == "trimmer":
            in_trim.set()
            _time.sleep(0.5)
        real_cap(path)

    monkeypatch.setattr(yaml_io, "_cap_jsonl", slow_cap)
    trimmer = threading.Thread(
        target=yaml_io.log_shadow_anomaly, args=("t", "first", {}), name="trimmer"
    )
    trimmer.start()
    assert in_trim.wait(5)
    size_during_trim = log.stat().st_size
    second = threading.Thread(target=yaml_io.log_shadow_anomaly, args=("t", "second", {}))
    second.start()
    _time.sleep(0.2)
    assert log.stat().st_size == size_during_trim, "an append ran inside the trim window"
    trimmer.join(10)
    second.join(10)

    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert events[-2:] == ["first", "second"]
