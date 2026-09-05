"""A manifest that can't be parsed degrades to EMPTY — loudly.

`load_space_manifest` swallows every parse error by design: a hand-edited or
half-written manifest must never break a space switch. That resilience is right,
but on its own it is the exact failure shape this codebase keeps re-learning —
everything stays green while the space silently loses its `name`, its pinned
`apps`, and its finance `books`, so Finance starts writing to the personal
ledger with no signal anywhere.

So the degradation now records a `space_manifest_unreadable` incident, which
`navig doctor` → Config Health reads back and the config-incidents monitor
pushes. These tests pin the three properties that make that trustworthy:

1. it still NEVER raises (resilience is not traded away for the report),
2. it reports once per file per process (this runs on every scan of every space —
   an un-deduped report would bury the log in copies of itself),
3. a healthy manifest reports NOTHING (a channel that cries wolf gets ignored).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.core import incidents
from navig.spaces import space_manifest as sm
from navig.spaces.space_manifest import load_space_manifest


@pytest.fixture(autouse=True)
def _isolate_reports(monkeypatch):
    """Fresh dedupe set + a capturing sink for every test."""
    monkeypatch.setattr(sm, "_reported_unreadable", set())
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda event, **data: seen.append((event, data)))
    return seen


def _space(tmp_path: Path, name: str, text: str | None, *, filename: str = "space.json") -> Path:
    space = tmp_path / name
    (space / ".navig").mkdir(parents=True)
    if text is not None:
        (space / ".navig" / filename).write_text(text, encoding="utf-8")
    return space


def test_corrupt_json_degrades_to_empty_without_raising(tmp_path, _isolate_reports):
    space = _space(tmp_path, "broken", '{"id": "broken", "books": "Comp')  # truncated
    manifest = load_space_manifest(space)  # must not raise
    assert manifest.resolved_id is None
    assert manifest.books is None
    assert manifest.app_allowlist == []

    assert len(_isolate_reports) == 1
    event, data = _isolate_reports[0]
    assert event == incidents.SPACE_MANIFEST_UNREADABLE
    assert data["path"].endswith("space.json")
    assert "reason" in data and data["reason"]


def test_reports_once_per_file_per_process(tmp_path, _isolate_reports):
    """Every /spaces/scan re-reads every manifest — one bad file must not spam."""
    space = _space(tmp_path, "broken", "{not json")
    for _ in range(5):
        load_space_manifest(space)
    assert len(_isolate_reports) == 1


def test_two_broken_manifests_report_separately(tmp_path, _isolate_reports):
    a = _space(tmp_path, "a", "{not json")
    b = _space(tmp_path, "b", "{also not json")
    load_space_manifest(a)
    load_space_manifest(b)
    assert len(_isolate_reports) == 2
    assert {Path(d["path"]).parent.parent.name for _, d in _isolate_reports} == {"a", "b"}


def test_non_object_top_level_is_reported_too(tmp_path, _isolate_reports):
    """It parses cleanly but yields the same empty manifest, so it's the same bug."""
    space = _space(tmp_path, "listy", json.dumps(["finance", "system"]))
    assert load_space_manifest(space).resolved_id is None
    assert len(_isolate_reports) == 1
    assert "not an object" in _isolate_reports[0][1]["reason"]


def test_healthy_manifest_reports_nothing(tmp_path, _isolate_reports):
    space = _space(tmp_path, "good", json.dumps({"id": "good", "books": "Company", "apps": ["finance"]}))
    manifest = load_space_manifest(space)
    assert manifest.resolved_id == "good"
    assert manifest.books == "Company"
    assert manifest.app_allowlist == ["finance"]
    assert _isolate_reports == []


def test_bare_navig_dir_reports_nothing(tmp_path, _isolate_reports):
    """No manifest at all is a perfectly valid space, not a failure."""
    space = _space(tmp_path, "bare", None)
    assert load_space_manifest(space).resolved_id is None
    assert _isolate_reports == []


def test_broken_yaml_manifest_is_reported(tmp_path, _isolate_reports):
    pytest.importorskip("yaml")
    space = _space(tmp_path, "yamlspace", "id: [unclosed\n", filename="space.yaml")
    assert load_space_manifest(space).resolved_id is None
    assert len(_isolate_reports) == 1


def test_a_failing_incident_sink_never_breaks_the_load(tmp_path, monkeypatch):
    """An observation must never break the thing it observes."""
    monkeypatch.setattr(sm, "_reported_unreadable", set())

    def _boom(*_a, **_k):
        raise RuntimeError("incident log is on fire")

    monkeypatch.setattr(incidents, "record", _boom)
    space = _space(tmp_path, "broken", "{not json")
    assert load_space_manifest(space).resolved_id is None  # still degrades cleanly
