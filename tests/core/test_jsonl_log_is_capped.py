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
